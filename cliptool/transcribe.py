import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")

HF_CACHE_ENVS = ("HF_HOME", "HF_HUB_CACHE")
MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v3"}


def pick_device(cfg):
    if cfg.get("device") != "auto":
        return cfg.get("device", "cpu")
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def model_cached(model):
    if not model:
        return False
    base = None
    for env in HF_CACHE_ENVS:
        if os.environ.get(env):
            base = Path(os.environ[env]) / "hub"
            break
    if base is None:
        base = Path.home() / ".cache" / "huggingface" / "hub"
    return (base / ("models--Systran--faster-whisper-%s" % model)).exists()


_MODEL_CACHE = {}


def transcribe(wav_path, cfg, progress_cb):
    from faster_whisper import WhisperModel

    model = cfg.get("whisper_model", "small")
    device = pick_device(cfg)
    compute = "int8" if device == "cpu" else "auto"
    cache_key = (model, device, compute)
    if cache_key not in _MODEL_CACHE:
        if progress_cb and not model_cached(model):
            progress_cb(0.0, 0, "Downloading Whisper model (%s)" % model)
        _MODEL_CACHE[cache_key] = WhisperModel(model, device=device, compute_type=compute)
    wmodel = _MODEL_CACHE[cache_key]
    beam = 3 if device == "cpu" else 5
    segments, info = wmodel.transcribe(
        wav_path,
        beam_size=beam,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    segs = []
    words = []
    last_end = 0.0
    count = 0
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        segs.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": text})
        for w in (s.words or []):
            t = (w.word or "").strip()
            if t:
                words.append([round(w.start, 3), round(w.end, 3), t])
        last_end = max(last_end, s.end)
        count += 1
        if progress_cb:
            progress_cb(last_end, count, None)
    return segs, words
