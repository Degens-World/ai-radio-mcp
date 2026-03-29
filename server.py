#!/usr/bin/env python3
"""
server.py — AI Radio MCP Server
================================
An MCP server that lets any agent spin up a fully-configured AI radio station
from scratch — including dependency installation, station configuration,
deployment, and hub registration.

Add to your MCP client config:
  {
    "mcpServers": {
      "ai-radio": {
        "command": "python",
        "args": ["/path/to/ai-radio-mcp/server.py"]
      }
    }
  }
"""

import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid

import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "ai-radio",
    instructions=(
        "This server helps you build and deploy a custom AI radio station. "
        "Typical flow:\n"
        "1. check_dependencies() — see what needs installing\n"
        "2. install_dependency(name) — install anything missing\n"
        "3. pull_model(model) — pull the Ollama LLM\n"
        "4. create_station(name, tagline) — start config\n"
        "5. set_personality / set_voice / set_content / set_schedule\n"
        "6. preview_config(station_id) — review before launch\n"
        "7. deploy_station(station_id) — go live\n"
        "8. register_with_hub(station_id, hub_url) — list on main site"
    ),
)

# ---------------------------------------------------------------------------
# Station registry (in-memory + persisted as JSON)
# ---------------------------------------------------------------------------

SERVER_DIR    = pathlib.Path(__file__).parent.resolve()
STATIONS_DIR  = SERVER_DIR / "stations"
REGISTRY_FILE = SERVER_DIR / "registry.json"
STATIONS_DIR.mkdir(exist_ok=True)

_registry: dict[str, dict] = {}


def _load_registry():
    global _registry
    if REGISTRY_FILE.exists():
        try:
            _registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception:
            _registry = {}


def _save_registry():
    REGISTRY_FILE.write_text(
        json.dumps(_registry, indent=2), encoding="utf-8"
    )


_load_registry()


def _get_station(station_id: str) -> dict:
    if station_id not in _registry:
        raise ValueError(f"Unknown station_id: {station_id}")
    return _registry[station_id]


def _next_free_port(start: int = 8300) -> int:
    port = start
    used = {s.get("stream", {}).get("port") for s in _registry.values()}
    while port in used:
        port += 1
    return port


# ---------------------------------------------------------------------------
# ── SETUP TOOLS ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@mcp.tool()
def check_dependencies() -> dict:
    """
    Audit the local machine for all dependencies needed to run an AI radio station.
    Returns a dict with 'installed' and 'missing' lists, plus install instructions.
    """
    results = {}

    def check(name: str, cmd: list[str]) -> bool:
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    results["python"]  = {"ok": sys.version_info >= (3, 10),
                           "version": sys.version.split()[0]}
    results["git"]     = {"ok": check("git",    ["git", "--version"]),
                           "install": "https://git-scm.com/downloads"}
    results["ffmpeg"]  = {"ok": check("ffmpeg", ["ffmpeg", "-version"]),
                           "install": _ffmpeg_install_hint()}
    results["ollama"]  = {"ok": check("ollama", ["ollama", "--version"]),
                           "install": "https://ollama.com/download"}

    # Python packages
    pkgs = {}
    for pkg in ["kokoro", "soundfile", "numpy", "requests"]:
        try:
            __import__(pkg.replace("-", "_"))
            pkgs[pkg] = True
        except ImportError:
            pkgs[pkg] = False
    results["python_packages"] = pkgs

    missing = [k for k, v in results.items()
               if k != "python_packages" and not v.get("ok")]
    missing_pkgs = [k for k, v in pkgs.items() if not v]

    return {
        "status":        "ready" if not missing and not missing_pkgs else "setup_needed",
        "checks":        results,
        "missing":       missing,
        "missing_packages": missing_pkgs,
        "next_step":     (
            "All dependencies installed — ready to create a station!"
            if not missing and not missing_pkgs
            else "Call install_dependency(name) for each item in 'missing', "
                 "then install_python_deps() for missing packages."
        ),
    }


def _ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return "winget install ffmpeg  OR  choco install ffmpeg"
    if system == "Darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg  OR  sudo dnf install ffmpeg"


@mcp.tool()
def install_dependency(name: str) -> dict:
    """
    Install a missing dependency by name.
    Supported: 'ollama', 'ffmpeg'

    For Python packages use install_python_deps() instead.
    """
    system = platform.system()
    name   = name.lower()

    if name == "ffmpeg":
        if system == "Windows":
            cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent"]
        elif system == "Darwin":
            cmd = ["brew", "install", "ffmpeg"]
        else:
            cmd = ["sudo", "apt-get", "install", "-y", "ffmpeg"]

    elif name == "ollama":
        if system == "Windows":
            return {
                "status":  "manual",
                "message": "Download and run the Ollama installer from https://ollama.com/download",
            }
        elif system == "Darwin":
            return {
                "status":  "manual",
                "message": "Download Ollama.app from https://ollama.com/download",
            }
        else:
            cmd = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    else:
        return {"status": "error", "message": f"Unknown dependency: {name}. Try 'ollama' or 'ffmpeg'."}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        ok = result.returncode == 0
        return {
            "status":  "installed" if ok else "error",
            "command": " ".join(cmd),
            "stdout":  result.stdout[-500:],
            "stderr":  result.stderr[-200:],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def install_python_deps() -> dict:
    """
    Install all required Python packages for the radio runner.
    Runs: pip install kokoro soundfile numpy requests python-dotenv
    """
    packages = ["kokoro", "soundfile", "numpy", "requests", "python-dotenv"]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + packages,
            capture_output=True, text=True, timeout=300,
        )
        ok = result.returncode == 0
        return {
            "status":   "installed" if ok else "error",
            "packages": packages,
            "output":   result.stdout[-500:],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def pull_model(model: str = "mistral-nemo:latest") -> dict:
    """
    Pull an Ollama model for use as the radio DJ brain.

    Recommended models:
      mistral-nemo:latest  — best balance of speed and personality (default)
      llama3.1:8b          — more factual, less chaotic
      mistral:7b           — fast, lightweight
      gemma3:4b            — small, runs on low-end hardware
    """
    try:
        result = subprocess.run(
            ["ollama", "pull", model],
            capture_output=True, text=True, timeout=600,
        )
        ok = result.returncode == 0
        return {
            "status": "pulled" if ok else "error",
            "model":  model,
            "output": result.stdout[-300:],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def setup_all(model: str = "mistral-nemo:latest") -> dict:
    """
    Run the full setup chain in one call:
    installs Python deps, checks for ffmpeg/ollama, and pulls the model.

    Call check_dependencies() first if you want to see what's already installed.
    """
    steps = []
    steps.append({"step": "python_deps", **install_python_deps()})
    steps.append({"step": "pull_model",  **pull_model(model)})
    all_ok = all(s.get("status") in ("installed", "pulled", "ok") for s in steps)
    return {
        "status": "ready" if all_ok else "partial",
        "steps":  steps,
        "note":   "ffmpeg and ollama must be installed manually — see check_dependencies() for links.",
    }


# ---------------------------------------------------------------------------
# ── STATION BUILD TOOLS ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@mcp.tool()
def create_station(name: str, tagline: str = "") -> dict:
    """
    Start building a new radio station. Returns a station_id used in all
    subsequent configuration calls.

    Parameters
    ----------
    name    : Display name for the station (e.g. "Degen Vibes Radio")
    tagline : Short one-liner shown on the player (e.g. "24/7 crypto commentary")
    """
    station_id = str(uuid.uuid4())[:8]
    station_dir = STATIONS_DIR / station_id
    station_dir.mkdir(parents=True, exist_ok=True)
    (station_dir / "music_library").mkdir(exist_ok=True)
    (station_dir / "clips").mkdir(exist_ok=True)
    (station_dir / "hls_output").mkdir(exist_ok=True)

    _registry[station_id] = {
        "station_id": station_id,
        "name":       name,
        "tagline":    tagline,
        "status":     "configuring",
        "created_at": int(time.time()),
        "dj":         {},
        "content":    {"source": "freestyle", "params": {}},
        "schedule":   {"type": "balanced"},
        "stream":     {"port": _next_free_port()},
        "ollama":     {"model": "mistral-nemo:latest",
                       "url":   "http://localhost:11434/api/chat"},
        "process_pid": None,
    }
    _save_registry()

    return {
        "station_id": station_id,
        "name":       name,
        "next_steps": [
            "set_personality(station_id, dj_name, personality, speaking_style, topics)",
            "set_voice(station_id, voice)",
            "set_content(station_id, source_type, params)",
            "set_schedule(station_id, type)",
            "preview_config(station_id)",
            "deploy_station(station_id)",
        ],
    }


@mcp.tool()
def set_personality(
    station_id: str,
    dj_name: str,
    personality: str,
    speaking_style: str,
    topics: list[str],
) -> dict:
    """
    Define the DJ's character and focus areas.

    Parameters
    ----------
    station_id     : From create_station()
    dj_name        : The DJ's on-air name (e.g. "DegenBot", "SolaraFM", "TechBeat")
    personality    : 1-3 sentences describing who the DJ is
                     (e.g. "An unhinged crypto degenerate who loves chaos and moon shots")
    speaking_style : Tone and style notes
                     (e.g. "High energy, uses slang, speaks in 6-10 sentences per drop")
    topics         : List of subjects the DJ commentates on
                     (e.g. ["DeFi news", "NFT drops", "market moves", "memes"])
    """
    station = _get_station(station_id)

    persona = (
        f"You are {dj_name}, a radio DJ. {personality} "
        f"Speaking style: {speaking_style} "
        f"Your topics of interest: {', '.join(topics)}. "
        "Keep each broadcast segment to 6-12 sentences. "
        "Never break character. Never say you are an AI."
    )

    station["dj"] = {
        "name":          dj_name,
        "personality":   personality,
        "speaking_style": speaking_style,
        "topics":        topics,
        "persona":       persona,
        "voice":         station.get("dj", {}).get("voice", "am_michael"),
        "speaking_speed": 1.15,
    }
    _save_registry()
    return {"status": "ok", "dj_name": dj_name, "persona_preview": persona[:200] + "..."}


@mcp.tool()
def set_voice(station_id: str, voice: str) -> dict:
    """
    Set the TTS voice for the DJ.

    Available voices:
      am_michael  — deep, authoritative (default)
      am_adam     — warm, conversational
      am_echo     — crisp, energetic
      am_fenrir   — bold, dramatic
      am_puck     — light, playful
      af_bella    — bright, enthusiastic
      af_heart    — warm, expressive
      af_nicole   — clear, professional
    """
    valid = {"am_michael", "am_adam", "am_echo", "am_fenrir",
             "am_puck", "af_bella", "af_heart", "af_nicole"}
    if voice not in valid:
        return {"status": "error", "message": f"Invalid voice. Choose from: {', '.join(sorted(valid))}"}

    station = _get_station(station_id)
    station.setdefault("dj", {})["voice"] = voice
    _save_registry()
    return {"status": "ok", "voice": voice}


@mcp.tool()
def set_content(station_id: str, source_type: str, params: dict) -> dict:
    """
    Configure what data the DJ commentates on.

    source_type options and their params:

    "blockchain"
      explorer_url      Base URL of the blockchain explorer REST API
      coin_symbol       Ticker shown in drops (e.g. "ERG", "BTC", "ETH")
      coin_id           CoinGecko coin ID for price data (e.g. "ergo", "bitcoin")
      whale_threshold   Value in coin units that triggers a whale alert (default 10000)

    "rss"
      feeds             List of RSS feed URLs
      max_items         Max headlines per fetch (default 5)

    "freestyle"
      topics            List of topic strings the DJ riffs on with no live data
                        (e.g. ["music", "tech news", "motivational thoughts"])
    """
    valid_sources = {"blockchain", "rss", "freestyle"}
    if source_type not in valid_sources:
        return {"status": "error",
                "message": f"Invalid source_type. Choose from: {', '.join(valid_sources)}"}

    station = _get_station(station_id)
    station["content"] = {"source": source_type, "params": params}
    _save_registry()
    return {"status": "ok", "source": source_type, "params": params}


@mcp.tool()
def set_schedule(station_id: str, schedule_type: str, poll_interval_secs: int = 45) -> dict:
    """
    Set the segment rotation schedule.

    schedule_type options:
      "balanced"      — alternates talk and music evenly (default)
      "talk_heavy"    — mostly commentary with short music breaks
      "music_heavy"   — mostly music with periodic DJ drops

    poll_interval_secs: how often (in seconds) to fetch new content events (default 45)
    """
    valid = {"balanced", "talk_heavy", "music_heavy"}
    if schedule_type not in valid:
        return {"status": "error",
                "message": f"Invalid schedule_type. Choose from: {', '.join(valid)}"}

    station = _get_station(station_id)
    station["schedule"]            = {"type": schedule_type}
    station["poll_interval_secs"]  = poll_interval_secs
    _save_registry()
    return {"status": "ok", "schedule_type": schedule_type}


@mcp.tool()
def set_ollama_model(station_id: str, model: str, ollama_url: str = "http://localhost:11434/api/chat") -> dict:
    """
    Override the Ollama model and endpoint for a specific station.
    Defaults to mistral-nemo:latest on localhost.
    """
    station = _get_station(station_id)
    station["ollama"] = {"model": model, "url": ollama_url}
    _save_registry()
    return {"status": "ok", "model": model, "url": ollama_url}


@mcp.tool()
def preview_config(station_id: str) -> dict:
    """
    Preview the full station configuration before deploying.
    Returns the complete config that will be written to station_config.json.
    """
    return dict(_get_station(station_id))


# ---------------------------------------------------------------------------
# ── DEPLOY TOOLS ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@mcp.tool()
def deploy_station(station_id: str) -> dict:
    """
    Generate station_config.json, launch the runner, and return the stream URL.

    The station must have at minimum a DJ personality set via set_personality().
    Ollama and ffmpeg must be installed and Ollama must be running.
    """
    station    = _get_station(station_id)
    station_dir = STATIONS_DIR / station_id

    # Write config
    config_path = station_dir / "station_config.json"
    config_path.write_text(json.dumps(station, indent=2), encoding="utf-8")

    # Copy runner and content package
    runner_src  = SERVER_DIR / "runner.py"
    content_src = SERVER_DIR / "content"
    shutil.copy2(str(runner_src), str(station_dir / "runner.py"))
    content_dst = station_dir / "content"
    if content_dst.exists():
        shutil.rmtree(str(content_dst))
    shutil.copytree(str(content_src), str(content_dst))

    # Launch
    proc = subprocess.Popen(
        [sys.executable, str(station_dir / "runner.py"),
         "--config", str(config_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    station["status"]      = "running"
    station["process_pid"] = proc.pid
    station["started_at"]  = int(time.time())
    _save_registry()

    port       = station["stream"]["port"]
    stream_url = f"http://localhost:{port}/stream.m3u8"

    # Generate player HTML
    player_path = station_dir / "player.html"
    _write_player(player_path, station, stream_url)

    return {
        "status":        "running",
        "station_id":    station_id,
        "name":          station["name"],
        "pid":           proc.pid,
        "stream_url":    stream_url,
        "player_html":   str(player_path),
        "note":          "Allow 15-30 seconds for the first drop to generate.",
        "next_step":     f"Call register_with_hub('{station_id}', hub_url) to list on the main site.",
    }


def _write_player(path: pathlib.Path, station: dict, stream_url: str):
    name    = station.get("name", "AI Radio")
    tagline = station.get("tagline", "Live AI Radio")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name}</title>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ background:#0a0a0f; color:#e0e0e0; font-family:'Courier New',monospace;
           display:flex; flex-direction:column; align-items:center;
           justify-content:center; min-height:100vh; }}
    .card {{ background:#12121a; border:1px solid #2a2a40; border-radius:16px;
             padding:40px; text-align:center; max-width:480px; width:90%;
             box-shadow:0 0 40px rgba(100,80,255,0.15); }}
    h1 {{ font-size:1.6rem; color:#a78bfa; margin-bottom:4px; }}
    .sub {{ font-size:0.85rem; color:#666; margin-bottom:30px; }}
    .pulse {{ width:60px; height:60px; background:#a78bfa; border-radius:50%;
              margin:0 auto 24px; animation:pulse 2s infinite; }}
    @keyframes pulse {{
      0%,100% {{ box-shadow:0 0 0 0 rgba(167,139,250,0.5); }}
      50%      {{ box-shadow:0 0 0 20px rgba(167,139,250,0); }}
    }}
    audio {{ width:100%; margin-bottom:20px; }}
    .status {{ font-size:0.8rem; color:#888; margin-bottom:16px; }}
    .live {{ color:#4ade80; font-weight:bold; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="pulse" id="pulse"></div>
    <h1>{name}</h1>
    <p class="sub">{tagline}</p>
    <audio id="player" controls autoplay></audio>
    <p class="status" id="status">Connecting...</p>
  </div>
  <script>
    const STREAM = "{stream_url}";
    const audio = document.getElementById("player");
    const status = document.getElementById("status");
    if (Hls.isSupported()) {{
      const hls = new Hls({{ lowLatencyMode: true }});
      hls.loadSource(STREAM);
      hls.attachMedia(audio);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {{
        audio.play();
        status.innerHTML = '<span class="live">LIVE</span>';
      }});
      hls.on(Hls.Events.ERROR, (_, d) => {{
        if (d.fatal) {{
          status.textContent = "Stream error — retrying...";
          setTimeout(() => {{ hls.loadSource(STREAM); hls.startLoad(); }}, 5000);
        }}
      }});
    }}
  </script>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


@mcp.tool()
def get_embed_code(station_id: str, public_stream_url: str = "") -> str:
    """
    Get an HTML snippet to embed this station's player on any website.

    public_stream_url: the publicly accessible URL for the stream (e.g. via ngrok or Cloudflare Tunnel).
    Leave blank to use localhost (only works on the local machine).
    """
    station    = _get_station(station_id)
    port       = station["stream"]["port"]
    stream_url = public_stream_url or f"http://localhost:{port}/stream.m3u8"
    name       = station.get("name", "AI Radio")
    tagline    = station.get("tagline", "")

    return f"""<!-- {name} embed player -->
<div id="ai-radio-player-{station_id}" style="max-width:420px">
  <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
  <audio id="ar-{station_id}" controls style="width:100%"></audio>
  <p style="font-size:12px;color:#888">{name} — {tagline}</p>
  <script>
    (function(){{
      var audio = document.getElementById("ar-{station_id}");
      if(Hls.isSupported()){{
        var hls = new Hls();
        hls.loadSource("{stream_url}");
        hls.attachMedia(audio);
        hls.on(Hls.Events.MANIFEST_PARSED, function(){{ audio.play(); }});
      }}
    }})();
  </script>
</div>"""


@mcp.tool()
def register_with_hub(station_id: str, hub_url: str, public_stream_url: str) -> dict:
    """
    Register this station with the main AI Radio hub website.

    Parameters
    ----------
    station_id        : Station to register
    hub_url           : Base URL of the hub API (e.g. "https://hub.airadio.world")
    public_stream_url : Publicly accessible HLS stream URL (ngrok, Cloudflare Tunnel, etc.)
    """
    station = _get_station(station_id)
    payload = {
        "station_id":   station_id,
        "name":         station.get("name"),
        "tagline":      station.get("tagline"),
        "stream_url":   public_stream_url,
        "dj_name":      station.get("dj", {}).get("name"),
        "content_type": station.get("content", {}).get("source"),
        "registered_at": int(time.time()),
    }
    try:
        resp = requests.post(f"{hub_url}/api/stations/register",
                             json=payload, timeout=15)
        resp.raise_for_status()
        station["hub_url"]    = hub_url
        station["stream_url"] = public_stream_url
        _save_registry()
        return {"status": "registered", "hub_response": resp.json()}
    except Exception as e:
        return {"status": "error", "message": str(e),
                "payload": payload,
                "note": "Hub may not be running yet — save this payload for when it is."}


# ---------------------------------------------------------------------------
# ── MANAGEMENT TOOLS ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@mcp.tool()
def list_stations() -> list[dict]:
    """List all stations (running and stopped)."""
    return [
        {
            "station_id": sid,
            "name":       s.get("name"),
            "status":     s.get("status"),
            "port":       s.get("stream", {}).get("port"),
            "content":    s.get("content", {}).get("source"),
            "dj":         s.get("dj", {}).get("name"),
        }
        for sid, s in _registry.items()
    ]


@mcp.tool()
def station_status(station_id: str) -> dict:
    """Get the current status and config summary for a station."""
    station = _get_station(station_id)
    port    = station.get("stream", {}).get("port")

    # Check if port is still listening
    live = False
    if port:
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("localhost", port))
            s.close()
            live = True
        except Exception:
            pass

    return {
        "station_id":   station_id,
        "name":         station.get("name"),
        "status":       "live" if live else station.get("status", "unknown"),
        "port":         port,
        "stream_url":   f"http://localhost:{port}/stream.m3u8" if port else None,
        "dj":           station.get("dj", {}).get("name"),
        "content":      station.get("content", {}).get("source"),
        "schedule":     station.get("schedule", {}).get("type"),
        "pid":          station.get("process_pid"),
        "started_at":   station.get("started_at"),
    }


@mcp.tool()
def stop_station(station_id: str) -> dict:
    """Stop a running radio station."""
    station = _get_station(station_id)
    pid     = station.get("process_pid")

    if pid:
        try:
            import signal as _signal
            os.kill(pid, _signal.SIGTERM)
            station["status"]      = "stopped"
            station["process_pid"] = None
            _save_registry()
            return {"status": "stopped", "station_id": station_id, "pid": pid}
        except ProcessLookupError:
            station["status"] = "stopped"
            _save_registry()
            return {"status": "already_stopped", "station_id": station_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return {"status": "not_running", "station_id": station_id}


@mcp.tool()
def delete_station(station_id: str) -> dict:
    """
    Stop and permanently delete a station and all its files.
    This cannot be undone.
    """
    stop_station(station_id)
    station_dir = STATIONS_DIR / station_id
    if station_dir.exists():
        shutil.rmtree(str(station_dir))
    _registry.pop(station_id, None)
    _save_registry()
    return {"status": "deleted", "station_id": station_id}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
