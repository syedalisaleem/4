# ClipForge

Local-first web app that turns a long video (or YouTube link) into short 9:16 clips ready for TikTok, Reels, and Shorts.

- Transcribes the video with Whisper (fully offline via faster-whisper)
- Picks the best highlight moments with an LLM — Ollama (offline) or any OpenAI-compatible API key
- Reframes each clip to 1080x1920, tracking the speaking person with face detection (YuNet, bundled)
- Burns in animated captions (word-level timing, pop/fade/slam styles)
- Review screen: previews, per-clip trim, captions/focus/zoom settings, export selected clips as clean MP4s (no watermark)
- No accounts, no uploads, nothing leaves your machine

## Quick start

```bat
pip install -r requirements.txt
run.bat
```

Then open http://127.0.0.1:8000 and start on the **Setup** tab — it checks everything for you:

| Check | What happens if missing |
|---|---|
| Python + packages | `pip install -r requirements.txt` |
| ffmpeg / ffprobe | Not needed — a bundled ffmpeg is used automatically if absent |
| Whisper model | Downloads on first transcription (one-time, needs internet once) |
| Ollama | Optional. Install [Ollama](https://ollama.com) and `ollama pull llama3.1` for offline AI clip picking |
| API key | Optional. Any OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, LM Studio…) |

## Choosing the clip-picking AI (Setup tab)

- **Auto (default)** — uses your API key if set, otherwise Ollama if running, otherwise a built-in heuristic
- **Ollama** — fully offline. JSON mode is used automatically
- **API key** — best quality. Works with any OpenAI-compatible `/chat/completions` endpoint
- **Heuristic** — no AI at all: picks high talking-energy moments by words-per-second

## Tips

- Whisper model sizes: `tiny`/`base` (fast, CPU), `small` (recommended), `medium`/`large-v3` (accurate, slow). CPU-only machines should stick to `small` or below.
- The review screen lets you switch which detected face to follow, disable face tracking (center crop), change zoom, caption style/position/size, and trim start/end.
- Exports are H.264 + AAC at 1080x1920 (9:16), saved under `data/jobs/<id>/clip_*/`.
- YouTube support needs internet (yt-dlp); everything else works offline.

## Project layout

```
server.py            FastAPI server + job manager + setup check
cliptool/
  pipeline.py        job orchestration (download → transcribe → pick → focus → render)
  transcribe.py      faster-whisper (word timestamps + VAD)
  pick.py            LLM clip picking (Ollama / API / heuristic)
  focus.py           YuNet face detection + smoothing for speaker tracking
  captions.py        animated ASS caption generation
  render.py          ffmpeg moving-crop + caption burn-in + scaling
  ffmpeg.py          ffmpeg/ffprobe resolution + helpers
  download.py        yt-dlp / local file handling
static/              the web UI (vanilla JS, no build step)
data/                config, jobs, uploads, exports (all local)
```
