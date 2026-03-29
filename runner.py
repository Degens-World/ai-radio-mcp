#!/usr/bin/env python3
"""
runner.py — Generic AI Radio Station Runner
============================================
Reads a station_config.json from the current directory (or --config path),
then runs a live HLS radio stream driven by a local Ollama LLM + Kokoro TTS.

Usage:
    python runner.py                        # reads ./station_config.json
    python runner.py --config /path/to/cfg  # explicit config path
"""

import argparse
import asyncio
import http.server
import json
import os
import pathlib
import queue
import random
import struct
import subprocess
import sys
import threading
import time

# Windows: reconfigure stdout to UTF-8 so emoji/Unicode in LLM output never crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import requests
import soundfile as sf
from kokoro import KPipeline

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Globals (set after config load)
# ---------------------------------------------------------------------------

cfg           = None
BASE_DIR      = None  # station directory (absolute)
HLS_DIR       = None
CLIPS_DIR     = None
MUSIC_DIR     = None

OLLAMA_URL    = None
OLLAMA_MODEL  = None
DJ_PERSONA    = None
VOICE         = None
KOKORO_SPEED  = 1.15
STREAM_PORT   = None
SAMPLE_RATE   = 24000
CHANNELS      = 1
FFMPEG        = "ffmpeg"

audio_queue      = queue.Queue()
ffmpeg_proc      = None
_kokoro_pipeline = None
clip_counter     = 0
playback_end_time = 0.0


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def silence_pcm(secs: float) -> bytes:
    n = int(SAMPLE_RATE * CHANNELS * secs)
    return struct.pack(f"<{n}h", *[random.randint(-4, 4) for _ in range(n)])


def _kokoro_synth(text: str) -> np.ndarray:
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        _kokoro_pipeline = KPipeline(lang_code="a")
    chunks = []
    for _, _, audio in _kokoro_pipeline(text, voice=VOICE, speed=KOKORO_SPEED):
        chunks.append(audio)
    return np.concatenate(chunks) if chunks else np.zeros(100, dtype=np.float32)


async def make_clip(text: str) -> pathlib.Path:
    global clip_counter
    clip_counter += 1
    path = CLIPS_DIR / f"clip_{clip_counter:06d}.wav"
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, _kokoro_synth, text)
    sf.write(str(path), audio, SAMPLE_RATE)
    return path


def audio_to_pcm(audio_path: pathlib.Path) -> bytes:
    """Convert any audio file to raw PCM s16le at SAMPLE_RATE."""
    result = subprocess.run(
        [FFMPEG, "-i", str(audio_path),
         "-f", "s16le", "-acodec", "pcm_s16le",
         "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
         "-"],
        capture_output=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# FFmpeg HLS encoder
# ---------------------------------------------------------------------------

def start_ffmpeg_hls() -> subprocess.Popen:
    cmd = [
        FFMPEG, "-y",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-i", "pipe:0",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "30",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", str(HLS_DIR / "seg%06d.ts"),
        str(HLS_DIR / "stream.m3u8"),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# PCM writer thread
# ---------------------------------------------------------------------------

def pcm_writer():
    global ffmpeg_proc, playback_end_time
    BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * 2
    CHUNK_SECS    = 0.05
    CHUNK_SIZE    = int(BYTES_PER_SEC * CHUNK_SECS)
    GAP_PCM       = silence_pcm(0.3)
    buf           = bytearray()

    while True:
        try:
            while len(buf) < CHUNK_SIZE * 4:
                try:
                    clip_path = audio_queue.get_nowait()
                    pcm = audio_to_pcm(clip_path)
                    buf.extend(pcm)
                    buf.extend(GAP_PCM)
                    playback_end_time = time.time() + len(buf) / BYTES_PER_SEC
                except queue.Empty:
                    break

            chunk = bytes(buf[:CHUNK_SIZE]) if buf else silence_pcm(CHUNK_SECS)
            if buf:
                del buf[:CHUNK_SIZE]
            else:
                playback_end_time = time.time()

            ffmpeg_proc.stdin.write(chunk)
            ffmpeg_proc.stdin.flush()
            time.sleep(CHUNK_SECS * 0.9)

        except (BrokenPipeError, OSError):
            print("[pcm] FFmpeg pipe closed")
            break
        except Exception as e:
            print(f"[pcm] error: {e}")


# ---------------------------------------------------------------------------
# HLS HTTP server
# ---------------------------------------------------------------------------

def serve_hls():
    class CORSHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            super().end_headers()
        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass

    # Change directory ONLY for this thread's server root, using absolute path
    os.chdir(str(HLS_DIR))
    httpd = http.server.HTTPServer(("0.0.0.0", STREAM_PORT), CORSHandler)
    print(f"[stream] http://localhost:{STREAM_PORT}/stream.m3u8")
    httpd.serve_forever()


# ---------------------------------------------------------------------------
# LLM DJ drop generation
# ---------------------------------------------------------------------------

def call_ollama(prompt: str) -> str:
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": DJ_PERSONA},
            {"role": "user",   "content": prompt},
        ],
        "options": {"num_predict": 400},
    }, timeout=120)
    resp.raise_for_status()
    text = resp.json()["message"]["content"].strip()
    # Fallback if model refused
    if not text or "cannot" in text.lower()[:30]:
        text = "Keeping the vibes alive. Stay tuned."
    return text


async def queue_drop(prompt: str, label: str = "drop"):
    text = await asyncio.get_event_loop().run_in_executor(None, call_ollama, prompt)
    print(f"[{label}] {text[:80]}...")
    clip = await make_clip(text)
    audio_queue.put(clip)

    # Follow every speech drop with a music track if available
    music_tracks = list(MUSIC_DIR.glob("*.wav")) + list(MUSIC_DIR.glob("*.mp3"))
    if music_tracks:
        audio_queue.put(random.choice(music_tracks))


# ---------------------------------------------------------------------------
# Segment schedule
# ---------------------------------------------------------------------------

def get_segment(minute: int) -> str:
    schedule_type = cfg.get("schedule", {}).get("type", "balanced")

    if schedule_type == "music_heavy":
        segments = [
            (0,  5,  "commentary"),
            (5,  20, "music"),
            (20, 25, "commentary"),
            (25, 40, "music"),
            (40, 45, "commentary"),
            (45, 60, "music"),
        ]
    elif schedule_type == "talk_heavy":
        segments = [
            (0,  3,  "commentary"),
            (3,  6,  "music"),
            (6,  20, "commentary"),
            (20, 22, "music"),
            (22, 40, "commentary"),
            (40, 43, "music"),
            (43, 60, "commentary"),
        ]
    else:  # balanced
        segments = [
            (0,  2,  "commentary"),
            (2,  8,  "music"),
            (8,  20, "commentary"),
            (20, 22, "music"),
            (22, 35, "commentary"),
            (35, 40, "music"),
            (40, 55, "commentary"),
            (55, 60, "music"),
        ]

    m = minute % 60
    for start, end, seg in segments:
        if start <= m < end:
            return seg
    return "commentary"


# ---------------------------------------------------------------------------
# Music generation (optional — requires torch + transformers)
# ---------------------------------------------------------------------------

MUSIC_LIBRARY_TARGET = 20  # grow library up to this many tracks then rotate


def _run_musicgen(prompt: str, wav_path: pathlib.Path):
    """Generate a ~30s track with facebook/musicgen-small. Runs in a thread."""
    try:
        import torch
        import scipy.io.wavfile as wav_io
        from transformers import MusicgenForConditionalGeneration, AutoProcessor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = MusicgenForConditionalGeneration.from_pretrained(
                "facebook/musicgen-small", local_files_only=True).to(device)
            processor = AutoProcessor.from_pretrained(
                "facebook/musicgen-small", local_files_only=True)
        except Exception:
            model = MusicgenForConditionalGeneration.from_pretrained(
                "facebook/musicgen-small").to(device)
            processor = AutoProcessor.from_pretrained("facebook/musicgen-small")

        inputs = processor(text=[prompt], padding=True, return_tensors="pt").to(model.device)
        with torch.no_grad():
            audio = model.generate(**inputs, max_new_tokens=1500)

        audio_np = audio[0, 0].cpu().numpy()
        sr       = model.config.audio_encoder.sampling_rate
        duration = len(audio_np) / sr
        # Normalize to [-1, 1] then write as int16 — float32 WAVs with out-of-range
        # values produce high-pitched noise when decoded by ffmpeg
        peak = max(abs(audio_np).max(), 1e-6)
        audio_int16 = (audio_np / peak * 32767).clip(-32768, 32767).astype("int16")
        wav_io.write(str(wav_path), sr, audio_int16)
        print(f"[musicgen] Generated {wav_path.name} ({duration:.1f}s)")
        del model

        # Loop the track ~3 minutes worth into the queue
        loops = max(1, int(180 / duration) + 1)
        for _ in range(loops):
            audio_queue.put(wav_path)
    except ImportError:
        print("[musicgen] torch/transformers not installed — skipping generation")
    except Exception as e:
        print(f"[musicgen] generation failed: {e}")


def _get_music_prompt() -> str:
    """Build a music generation prompt from station content config."""
    topics = cfg.get("content", {}).get("params", {}).get("topics", [])
    dj_name = cfg.get("dj", {}).get("name", "")
    base = "upbeat electronic music, synthesizer, energetic radio background"
    if topics:
        base = f"upbeat electronic music inspired by {', '.join(topics[:3])}"
    if dj_name:
        base += f", radio station background for {dj_name}"
    return base


async def handle_music_segment():
    """Play from music_library; grow library in background if not full yet."""
    tracks = sorted(MUSIC_DIR.glob("*.wav")) + sorted(MUSIC_DIR.glob("*.mp3"))

    if tracks:
        if audio_queue.empty():
            track = random.choice(tracks)
            # Loop it to fill ~3 minutes
            try:
                import scipy.io.wavfile as wav_io
                sr, data = wav_io.read(str(track))
                duration = len(data) / sr
            except Exception:
                duration = 30.0
            loops = max(1, int(180 / duration) + 1)
            for _ in range(loops):
                audio_queue.put(track)
            print(f"[music] Playing {track.name} x{loops}")

        # Grow library in background if not full
        if len(tracks) < MUSIC_LIBRARY_TARGET:
            idx      = len(tracks) + 1
            new_path = MUSIC_DIR / f"track_{idx:03d}.wav"
            prompt   = _get_music_prompt()
            print(f"[music] Growing library ({len(tracks)}/{MUSIC_LIBRARY_TARGET}) — generating in background")
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _run_musicgen, prompt, new_path)

    else:
        # Empty library — generate first track (blocking)
        print("[music] Library empty — generating first track (this takes a few minutes)...")
        new_path = MUSIC_DIR / "track_001.wav"
        prompt   = _get_music_prompt()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_musicgen, prompt, new_path)


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------

async def watcher_loop():
    from content import build_source
    source = build_source(cfg)
    print(f"[content] {source.describe()}")

    POLL_INTERVAL = cfg.get("poll_interval_secs", 45)
    last_drop     = 0.0

    # Intro drop
    station_name = cfg.get("name", "AI Radio")
    intro_prompt  = (
        f"You are now going live on {station_name}. "
        f"Welcome the listeners with energy. Keep it under 10 sentences."
    )
    await queue_drop(intro_prompt, label="intro")

    while True:
        now    = time.time()
        minute = int(time.strftime("%M"))
        seg    = get_segment(minute)

        if seg == "music":
            await handle_music_segment()
            await asyncio.sleep(10)
            continue

        # Commentary segment — only drop if queue is clear and enough time has passed
        if (now - last_drop) < POLL_INTERVAL:
            await asyncio.sleep(10)
            continue

        if not audio_queue.empty():
            await asyncio.sleep(5)
            continue

        events = await asyncio.get_event_loop().run_in_executor(None, source.fetch_events)
        if events:
            prompt = "Events happening right now:\n" + "\n".join(f"- {e}" for e in events)
        else:
            topics = cfg.get("content", {}).get("params", {}).get("topics", ["the current vibe"])
            prompt = f"Talk about: {random.choice(topics)}"

        await queue_drop(prompt)
        last_drop = time.time()
        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    global cfg, BASE_DIR, HLS_DIR, CLIPS_DIR, MUSIC_DIR
    global OLLAMA_URL, OLLAMA_MODEL, DJ_PERSONA, VOICE, KOKORO_SPEED
    global STREAM_PORT, FFMPEG, ffmpeg_proc

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="station_config.json")
    args = parser.parse_args()

    cfg      = load_config(args.config)
    BASE_DIR = pathlib.Path(args.config).parent.resolve()

    HLS_DIR   = BASE_DIR / "hls_output"
    CLIPS_DIR = BASE_DIR / "clips"
    MUSIC_DIR = BASE_DIR / "music_library"
    for d in (HLS_DIR, CLIPS_DIR, MUSIC_DIR):
        d.mkdir(exist_ok=True)

    # Clear stale HLS segments
    for f in HLS_DIR.glob("*.ts"):
        f.unlink()
    m3u8 = HLS_DIR / "stream.m3u8"
    if m3u8.exists():
        m3u8.unlink()

    ollama_cfg   = cfg.get("ollama", {})
    OLLAMA_URL   = ollama_cfg.get("url",   "http://localhost:11434/api/chat")
    OLLAMA_MODEL = ollama_cfg.get("model", "mistral-nemo:latest")
    STREAM_PORT  = cfg.get("stream", {}).get("port", 8234)
    FFMPEG       = cfg.get("ffmpeg", "ffmpeg")

    dj_cfg      = cfg.get("dj", {})
    DJ_PERSONA  = dj_cfg.get("persona", "You are an AI radio DJ. Keep it fun and engaging.")
    VOICE       = dj_cfg.get("voice",   "am_michael")
    KOKORO_SPEED = float(dj_cfg.get("speaking_speed", 1.15))

    # Start HTTP server thread
    import socket
    s = socket.socket()
    try:
        s.connect(("localhost", STREAM_PORT))
        s.close()
        print(f"[stream] HTTP already on port {STREAM_PORT}")
    except ConnectionRefusedError:
        s.close()
        threading.Thread(target=serve_hls, daemon=True).start()

    # Start FFmpeg
    ffmpeg_proc = start_ffmpeg_hls()
    print("[ffmpeg] HLS encoder started")

    # Start PCM writer
    threading.Thread(target=pcm_writer, daemon=True).start()
    print("[pcm] PCM writer started")

    print(f"[radio] {cfg.get('name', 'AI Radio')} going live...")
    await watcher_loop()


if __name__ == "__main__":
    asyncio.run(main())
