import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cliptool import config as cfgmod
from cliptool import ffmpeg, focus, pipeline, render

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"
cfgmod.ensure_dirs()
DATA_DIR = cfgmod.DATA_DIR
JOBS_DIR = DATA_DIR / "jobs"

app = FastAPI(title="ClipForge")


class Job:
    def __init__(self, id, kind, source, options):
        self.id = id
        self.kind = kind
        self.source = source
        self.options = options or {}
        self.dir = JOBS_DIR / id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status = "queued"
        self.phase = "Queued"
        self.progress = 0
        self.error = None
        self.info = {}
        self.words = []
        self.transcript = []
        self.clips = []
        self.video_path = None
        self.created = time.time()
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self):
        return {
            "id": self.id, "kind": self.kind, "source": self.source, "options": self.options,
            "status": self.status, "phase": self.phase, "progress": self.progress, "error": self.error,
            "info": self.info, "words": self.words, "transcript": self.transcript,
            "created": self.created, "video_path": self.video_path,
            "clips": [{
                "cid": c.cid, "start": c.start, "end": c.end, "title": c.title,
                "reason": c.reason, "score": c.score, "words": c.words, "settings": c.settings,
                "faces": c.faces, "samples": c.samples, "window": c.window, "primary": c.primary,
                "preview": c.preview, "export": c.export,
            } for c in self.clips],
        }

    @classmethod
    def from_snapshot(cls, s):
        job = cls(s["id"], s["kind"], s["source"], s.get("options") or {})
        job.status = s.get("status", "queued")
        job.phase = s.get("phase", "done")
        job.progress = s.get("progress", 0)
        job.error = s.get("error")
        job.info = s.get("info", {})
        job.words = s.get("words", [])
        job.transcript = s.get("transcript", [])
        job.created = s.get("created", time.time())
        job.video_path = s.get("video_path")
        job.clips = []
        for c in s.get("clips", []):
            clip = JobClip(job, c["cid"], c, job.words)
            clip.start = c["start"]
            clip.end = c["end"]
            clip.title = c["title"]
            clip.reason = c["reason"]
            clip.score = c["score"]
            clip.words = c["words"]
            clip.settings = c["settings"]
            clip.faces = c["faces"]
            clip.samples = c["samples"]
            clip.window = c["window"]
            clip.primary = c["primary"]
            clip.preview = c["preview"]
            clip.export = c["export"]
            job.clips.append(clip)
        return job

    def params_key(self, clip):
        s = clip.settings
        return (clip.start, clip.end, s["zoom"], s["focus_mode"], s["face_idx"])

    def new_clip(self, cid, cand):
        return JobClip(self, cid, cand, self.words)

    def clip_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "error": self.error,
            "created": self.created,
            "info": self.info,
            "transcript": self.transcript,
            "clips": [c.to_dict(self) for c in self.clips],
        }


class JobClip:
    def __init__(self, job, cid, cand, all_words):
        self.cid = cid
        self.dir = job.dir / ("clip_%d" % cid)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.start = float(cand["start"])
        self.end = float(cand["end"])
        self.title = cand.get("title") or ("Clip %d" % cid)
        self.reason = cand.get("reason") or ""
        self.score = float(cand.get("score", 5))
        self.words = [w for w in all_words if w[0] >= self.start - 0.05 and w[1] <= self.end + 0.05]
        self.settings = {
            "captions": True,
            "style": "pop",
            "position": "bottom",
            "size": 1.0,
            "zoom": 1.0,
            "focus_mode": "auto",
            "face_idx": -1,
        }
        self.faces = []
        self.samples = []
        self.window = None
        self.preview = {"rev": 0, "state": "pending", "progress": 0, "error": None}
        self.export = {"state": "idle", "progress": 0, "error": None}

    def dur(self):
        return max(0.5, self.end - self.start)

    def preview_path(self):
        return self.dir / "preview.mp4"

    def export_path(self):
        safe = re.sub(r"[^A-Za-z0-9 _-]", "", self.title)[:40].strip() or "clip"
        return self.dir / ("export_%s.mp4" % safe)

    def to_dict(self, job):
        return {
            "cid": self.cid,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "title": self.title,
            "reason": self.reason,
            "score": self.score,
            "text": " ".join(w[2] for w in self.words),
            "word_count": len(self.words),
            "settings": self.settings,
            "faces": self.faces,
            "preview": {**self.preview, "url": "/data/jobs/%s/clip_%d/preview.mp4" % (job.id, self.cid)},
            "export": {**self.export, "url": ("/data/jobs/%s/clip_%d/%s" % (job.id, self.cid, os.path.basename(self.export_path())) if self.export.get("state") == "done" and self.export_path().exists() else None)},
        }


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        self._load()

    def config_snapshot(self):
        return cfgmod.load()

    def save(self, job):
        try:
            with self.lock:
                (job.dir / "job.json").write_text(json.dumps(job.snapshot(), ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load(self):
        try:
            for d in JOBS_DIR.iterdir():
                f = d / "job.json"
                if d.is_dir() and f.exists():
                    try:
                        job = Job.from_snapshot(json.loads(f.read_text(encoding="utf-8")))
                        self.jobs[job.id] = job
                    except Exception:
                        continue
        except Exception:
            pass

    def create(self, kind, source, options):
        jid = uuid.uuid4().hex[:10]
        job = Job(jid, kind, source, options)
        with self.lock:
            self.jobs[jid] = job
        return job

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)

    def delete(self, jid):
        with self.lock:
            job = self.jobs.pop(jid, None)
        if job:
            shutil.rmtree(job.dir, ignore_errors=True)
        return job

    def start(self, job):
        def run():
            try:
                pipeline.run(job, self)
            except Exception:
                job.set(status="failed", error=traceback.format_exc())
            finally:
                job.set(phase="done")
                self.save(job)

        threading.Thread(target=run, daemon=True).start()

    def refresh_clip(self, job, clip, reanalyze):
        def work():
            with job._render_lock:
                try:
                    clip.preview.update(state="rendering", progress=0, error=None)
                    clip.preview["rev"] += 1
                    if reanalyze:
                        focus.analyze(job, clip)
                    render.render_clip(job, clip, clip.preview_path(), True, lambda pct: clip.preview.__setitem__("progress", round(100 * pct)))
                    clip.preview.update(state="ready", progress=100)
                except Exception as e:
                    clip.preview.update(state="error", error=str(e))

        threading.Thread(target=work, daemon=True).start()

    def export(self, job, clip_ids):
        def work():
            targets = [c for c in job.clips if c.cid in clip_ids]
            for clip in targets:
                try:
                    clip.export.update(state="rendering", progress=0, error=None)
                    render.render_clip(job, clip, clip.export_path(), False, lambda pct, c=clip: c.export.__setitem__("progress", round(100 * pct)))
                    clip.export.update(state="done", progress=100)
                except Exception as e:
                    clip.export.update(state="error", error=str(e))
            job.set(phase="done")
            self.save(job)

        threading.Thread(target=work, daemon=True).start()


manager = JobManager()


def _run_setup():
    checks = []
    import platform

    checks.append({
        "key": "python",
        "name": "Python",
        "status": "ok",
        "detail": platform.python_version(),
    })

    def imp(name, label, required=True, detail=""):
        try:
            m = __import__(name)
            ver = getattr(m, "__version__", "") or ""
            checks.append({"key": name, "name": label, "status": "ok", "detail": ("%s %s" % (ver, detail)).strip()})
            return True
        except Exception as e:
            status = "fail" if required else "warn"
            checks.append({"key": name, "name": label, "status": status, "detail": str(e).splitlines()[0]})
            return False

    imp("fastapi", "FastAPI", True)
    imp("faster_whisper", "faster-whisper (Whisper)", True)
    imp("yt_dlp", "yt-dlp (YouTube)", True)
    imp("cv2", "OpenCV (face tracking)", True)
    imp("imageio_ffmpeg", "imageio-ffmpeg (bundled ffmpeg)", True, detail="(fallback)")

    fb = ffmpeg.ffmpeg_bin()
    if fb and os.path.exists(fb):
        checks.append({"key": "ffmpeg", "name": "ffmpeg", "status": "ok", "detail": ffmpeg.version()})
    else:
        checks.append({"key": "ffmpeg", "name": "ffmpeg", "status": "fail", "detail": "ffmpeg not found"})
    if ffmpeg.ffprobe_bin():
        checks.append({"key": "ffprobe", "name": "ffprobe", "status": "ok", "detail": "available"})
    else:
        checks.append({"key": "ffprobe", "name": "ffprobe", "status": "warn", "detail": "missing — using ffmpeg fallback probe"})

    cfg = cfgmod.load()
    model = cfg.get("whisper_model", "small")
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    cached = (hub / ("models--Systran--faster-whisper-%s" % model)).exists()
    detail = "cached" if cached else "not cached — will download on first run (one-time)"
    checks.append({"key": "whisper_model", "name": "Whisper model (%s)" % model, "status": "ok" if cached else "warn", "detail": detail})

    gpu = 0
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count()
    except Exception:
        pass
    checks.append({
        "key": "gpu",
        "name": "NVIDIA GPU (CUDA)",
        "status": "ok" if gpu else "warn",
        "detail": ("%d device(s)" % gpu) if gpu else "none — CPU mode (slower but works)",
    })

    ollama = None
    try:
        import requests
        r = requests.get(cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/tags", timeout=3)
        if r.status_code == 200:
            tags = [t.get("name") for t in r.json().get("models", [])]
            ollama = ", ".join(tags[:6]) + ("…" if len(tags) > 6 else "") if tags else "running, no models pulled"
    except Exception as e:
        ollama = None
    if ollama:
        checks.append({"key": "ollama", "name": "Ollama (local AI)", "status": "ok", "detail": ollama})
    else:
        checks.append({"key": "ollama", "name": "Ollama (local AI)", "status": "warn", "detail": "not running — clip picking will use heuristic mode unless an API key is set"})

    if cfg.get("api_key"):
        checks.append({"key": "apikey", "name": "API key", "status": "ok", "detail": "%s (%s)" % (cfg.get("api_model", ""), cfg.get("api_base", ""))})
    else:
        checks.append({"key": "apikey", "name": "API key", "status": "warn", "detail": "not set — optional, for better clip picking"})

    free_gb = shutil.disk_usage(str(DATA_DIR)).free / 1e9
    status = "ok" if free_gb > 2 else "warn"
    checks.append({"key": "disk", "name": "Disk space", "status": status, "detail": "%.1f GB free" % free_gb})

    failed = [c for c in checks if c["status"] == "fail"]
    return {"checks": checks, "ok": len(failed) == 0, "failures": [c["name"] for c in failed]}


class ConfigIn(BaseModel):
    mode: str = "auto"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_model: str = "gpt-4o-mini"
    whisper_model: str = "small"
    device: str = "auto"
    max_clips: int = 6
    min_len: int = 12
    max_len: int = 45


class JobCreate(BaseModel):
    kind: str
    source: str
    options: dict = {}


class ClipSettingsIn(BaseModel):
    start: float | None = None
    end: float | None = None
    captions: bool | None = None
    style: str | None = None
    position: str | None = None
    size: float | None = None
    zoom: float | None = None
    focus_mode: str | None = None
    face_idx: int | None = None


class ExportIn(BaseModel):
    clip_ids: list[int]


@app.get("/api/setup")
def api_setup():
    return _run_setup()


@app.get("/api/config")
def api_get_config():
    return cfgmod.load()


@app.post("/api/config")
def api_save_config(cfg: ConfigIn):
    saved = cfgmod.save(cfg.dict())
    return {"ok": True, "config": saved}


@app.post("/api/jobs")
def api_create_job(body: JobCreate):
    job = manager.create(body.kind, body.source, body.options)
    manager.start(job)
    return job.clip_dict()


@app.post("/api/jobs/file")
def api_create_job_file(file: UploadFile = File(...), options: str = Form("{}")):
    try:
        opts = json.loads(options)
    except Exception:
        opts = {}
    ext = os.path.splitext(file.filename or "")[1].lower()
    job = manager.create("file", file.filename or "upload", opts)
    dest = job.dir / ("upload" + (ext or ".mp4"))
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    job.source = str(dest)
    manager.start(job)
    return job.clip_dict()


@app.get("/api/jobs")
def api_list_jobs():
    return {"jobs": [j.clip_dict() for j in manager.jobs.values()]}


@app.get("/api/jobs/{jid}")
def api_get_job(jid: str):
    job = manager.get(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.clip_dict()


@app.get("/api/jobs/{jid}/transcript")
def api_transcript(jid: str):
    job = manager.get(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"transcript": job.transcript}


@app.delete("/api/jobs/{jid}")
def api_delete_job(jid: str):
    manager.delete(jid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/clips/{cid}/settings")
def api_clip_settings(jid: str, cid: int, body: ClipSettingsIn):
    job = manager.get(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    clip = next((c for c in job.clips if c.cid == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    data = body.dict(exclude_none=True)
    key_params = {"start", "end", "zoom", "focus_mode", "face_idx"}
    dur_total = job.info.get("duration", clip.end + 5)
    if "start" in data:
        clip.start = min(max(0.0, data["start"]), max(0.0, dur_total - 5))
    if "end" in data:
        clip.end = min(max(clip.start + 5, data["end"]), max(clip.start + 5, dur_total))
    for k in ("captions", "style", "position", "size", "zoom", "focus_mode", "face_idx"):
        if k in data and k not in ("start", "end"):
            if k in ("size", "zoom"):
                clip.settings[k] = min(1.6, max(0.5, float(data[k])))
            elif k in ("face_idx",):
                clip.settings[k] = int(data[k])
            else:
                clip.settings[k] = data[k]
    clip.words = [w for w in job.words if w[0] >= clip.start - 0.05 and w[1] <= clip.end + 0.05]
    reanalyze = any(k in data for k in key_params)
    manager.save(job)
    manager.refresh_clip(job, clip, reanalyze)
    return clip.to_dict(job)


@app.post("/api/jobs/{jid}/export")
def api_export(jid: str, body: ExportIn):
    job = manager.get(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "ready":
        raise HTTPException(409, "Job is not ready yet")
    ids = set(body.clip_ids)
    targets = [c for c in job.clips if c.cid in ids]
    if not targets:
        raise HTTPException(400, "No matching clips")
    manager.export(job, ids)
    return {"ok": True}


app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    cfgmod.ensure_dirs()
    port = int(os.environ.get("PORT", "8000"))
    print("ClipForge running at http://127.0.0.1:%d" % port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
