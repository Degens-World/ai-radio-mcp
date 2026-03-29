# AI Radio MCP

An MCP server that lets any AI agent spin up a fully-configured, live-streaming radio station from scratch — including dependency installation, station personality, content source, and deployment.

Agents call tools to configure and launch a station, then optionally register it with a central hub site where listeners can discover all running stations.

Built on [ai-blockchain-radio](https://github.com/Degens-World/ai-blockchain-radio) — generalized to support any topic, not just blockchain.

---

## What It Does

```
Agent calls MCP tools
  → checks + installs dependencies (Ollama, ffmpeg, Python packages)
  → pulls an LLM model via Ollama
  → configures station: DJ name, personality, voice, content source, schedule
  → deploys: generates config, launches HLS stream
  → returns stream URL + embed code
  → optionally registers station with the hub aggregator site
```

All audio synthesis runs **locally** — Kokoro TTS + Ollama LLM, no cloud APIs.

---

## Installation

```bash
git clone https://github.com/Degens-World/ai-radio-mcp
cd ai-radio-mcp
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### Prerequisites (installed separately)

| Dependency | Install |
|------------|---------|
| **Python 3.10+** | [python.org](https://python.org) |
| **Ollama** | [ollama.com/download](https://ollama.com/download) |
| **ffmpeg** | Windows: `winget install ffmpeg` · macOS: `brew install ffmpeg` · Linux: `apt install ffmpeg` |
| **git** | [git-scm.com](https://git-scm.com) |

The MCP server can install ffmpeg and pull Ollama models for you (see tools below). Ollama itself must be installed manually on Windows/macOS.

---

## Add to Your MCP Client

### Claude Code / Claude Desktop

Add to your `claude_desktop_config.json` or `.claude/settings.json`:

```json
{
  "mcpServers": {
    "ai-radio": {
      "command": "python",
      "args": ["/absolute/path/to/ai-radio-mcp/server.py"]
    }
  }
}
```

---

## Available Tools

### Setup Tools

| Tool | Description |
|------|-------------|
| `check_dependencies()` | Audit installed tools — Python, git, ffmpeg, Ollama, packages |
| `install_dependency(name)` | Install `"ffmpeg"` or get Ollama download link |
| `install_python_deps()` | `pip install` all required Python packages |
| `pull_model(model)` | Pull an Ollama model (default: `mistral-nemo:latest`) |
| `setup_all(model)` | Run all setup steps in one call |

### Station Build Tools

| Tool | Description |
|------|-------------|
| `create_station(name, tagline)` | Start a new station, returns `station_id` |
| `set_personality(station_id, dj_name, personality, speaking_style, topics)` | Define the DJ character |
| `set_voice(station_id, voice)` | Pick a Kokoro TTS voice |
| `set_content(station_id, source_type, params)` | Set content source: `blockchain`, `rss`, or `freestyle` |
| `set_schedule(station_id, type)` | `balanced`, `talk_heavy`, or `music_heavy` |
| `set_ollama_model(station_id, model)` | Override LLM model for this station |
| `preview_config(station_id)` | View full config before deploying |

### Deploy Tools

| Tool | Description |
|------|-------------|
| `deploy_station(station_id)` | Launch the station, returns stream URL |
| `get_embed_code(station_id, public_stream_url)` | HTML embed snippet for any website |
| `register_with_hub(station_id, hub_url, public_stream_url)` | Register on the main hub site |

### Management Tools

| Tool | Description |
|------|-------------|
| `list_stations()` | All stations and their status |
| `station_status(station_id)` | Detailed status + live port check |
| `stop_station(station_id)` | Stop a running station |
| `delete_station(station_id)` | Stop and permanently remove a station |

---

## Typical Agent Flow

```
1. check_dependencies()
   → sees ffmpeg is missing

2. install_dependency("ffmpeg")
   → installs via winget/brew/apt

3. pull_model("mistral-nemo:latest")
   → downloads the LLM

4. create_station("Crypto Vibes Radio", "Where the chain never sleeps")
   → returns station_id: "a1b2c3d4"

5. set_personality(
     "a1b2c3d4",
     dj_name="DegenBot",
     personality="An unhinged crypto DJ who loves chaos and moon shots",
     speaking_style="High energy, uses slang, 8-10 sentences per drop",
     topics=["DeFi", "NFTs", "market moves", "whale alerts"]
   )

6. set_voice("a1b2c3d4", "am_michael")

7. set_content("a1b2c3d4", "blockchain", {
     "explorer_url": "https://api.ergoplatform.com/api/v1",
     "coin_symbol": "ERG",
     "coin_id": "ergo",
     "whale_threshold": 10000
   })

8. set_schedule("a1b2c3d4", "balanced")

9. preview_config("a1b2c3d4")
   → review everything looks right

10. deploy_station("a1b2c3d4")
    → stream live at http://localhost:8300/stream.m3u8

11. register_with_hub("a1b2c3d4", "https://hub.airadio.world", "https://my-ngrok-url/stream.m3u8")
    → station listed on main site
```

---

## Content Sources

### `blockchain`

Monitors a blockchain via public explorer API and generates live commentary.

```python
set_content("id", "blockchain", {
    "explorer_url": "https://api.ergoplatform.com/api/v1",  # or any compatible API
    "coin_symbol":  "ERG",
    "coin_id":      "ergo",       # CoinGecko ID for price data
    "whale_threshold": 10000,
})
```

### `rss`

Reads RSS feeds and has the DJ commentate on headlines.

```python
set_content("id", "rss", {
    "feeds": [
        "https://feeds.feedburner.com/TechCrunch",
        "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    ],
    "max_items": 5,
})
```

### `freestyle`

No live data — the DJ generates commentary purely from its persona and topics.

```python
set_content("id", "freestyle", {
    "topics": ["music theory", "AI news", "philosophy", "motivation"],
})
```

---

## Voices

| Voice | Character |
|-------|-----------|
| `am_michael` | Deep, authoritative (default) |
| `am_adam` | Warm, conversational |
| `am_echo` | Crisp, energetic |
| `am_fenrir` | Bold, dramatic |
| `am_puck` | Light, playful |
| `af_bella` | Bright, enthusiastic |
| `af_heart` | Warm, expressive |
| `af_nicole` | Clear, professional |

---

## Recommended LLM Models

| Model | Best for |
|-------|----------|
| `mistral-nemo:latest` | Best personality + speed (recommended) |
| `llama3.1:8b` | More factual, less creative |
| `mistral:7b` | Lightweight, fast |
| `gemma3:4b` | Minimal hardware requirements |

---

## Hub Integration

The `register_with_hub` tool POSTs station metadata to a central hub API:

```json
{
  "station_id":   "a1b2c3d4",
  "name":         "Crypto Vibes Radio",
  "tagline":      "Where the chain never sleeps",
  "stream_url":   "https://my-tunnel.ngrok-free.app/stream.m3u8",
  "dj_name":      "DegenBot",
  "content_type": "blockchain",
  "registered_at": 1711720000
}
```

The hub site can index all registered stations and serve a discovery page.

For public streams, use [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):

```bash
# ngrok
ngrok http 8300

# Cloudflare Tunnel (free, persistent URL)
cloudflared tunnel --url http://localhost:8300
```

---

## Project Structure

```
server.py            — MCP server (all tools)
runner.py            — Generic station runner (reads station_config.json)
content/
  __init__.py        — Source factory (build_source)
  base.py            — ContentSource base class
  blockchain.py      — Blockchain explorer data source
  rss.py             — RSS feed data source
  freestyle.py       — Topic-driven no-data source
stations/            — Created at runtime, one dir per station
  <station_id>/
    station_config.json
    runner.py        — Copy of runner for this station
    content/         — Copy of content package
    hls_output/      — Live HLS segments (.ts + .m3u8)
    clips/           — TTS audio clips (auto-cleaned)
    music_library/   — WAV/MP3 tracks to play between drops
    player.html      — Auto-generated web player
registry.json        — Persisted station registry
```

---

## Adding Music

Drop `.wav` or `.mp3` files into a station's `music_library/` directory before or after deploying. The runner picks them up automatically for music segments.

---

## License

MIT — build your own radio empire.

---

*Part of the [Degens.World](https://degens.world) ecosystem.*
