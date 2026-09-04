import json
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "mode": "auto",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.1",
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "api_model": "gpt-4o-mini",
    "whisper_model": "small",
    "device": "auto",
    "max_clips": 6,
    "min_len": 12,
    "max_len": 45,
}

_lock = threading.Lock()


def ensure_dirs():
    for d in (DATA_DIR, DATA_DIR / "jobs", DATA_DIR / "uploads"):
        d.mkdir(parents=True, exist_ok=True)


def load():
    ensure_dirs()
    with _lock:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        return cfg


def save(cfg):
    ensure_dirs()
    merged = dict(DEFAULTS)
    merged.update(cfg or {})
    with _lock:
        CONFIG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged
