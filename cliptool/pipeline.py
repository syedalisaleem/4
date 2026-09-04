import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import analyze, download, ffmpeg, focus, pick, render, transcribe


def resolve_source(job, cfg):
    if job.kind == "url":
        if not download.is_url(job.source):
            raise RuntimeError("Not a valid URL")
        job.set(phase="Downloading from YouTube", progress=4)

        def hook(pct):
            job.set(progress=4 + 5 * pct)

        try:
            path = download.download_url(job.source, job.dir, hook)
        except Exception as e:
            err = str(e)
            if "403" in err or "sign in" in err.lower() or "forbidden" in err.lower():
                raise RuntimeError(
                    "YouTube blocked the download (requires sign-in).\n\n"
                    "Workaround: Download the video yourself, then use the File Upload tab "
                    "to upload it directly (.mp4, .mov, etc.)."
                )
            raise
        return download.ensure_mp4(path, job.dir)
    return download.prepare_local(job.source, job.dir)


def analyze_reference(ref_path, cfg):
    info = ffmpeg.probe(ref_path)
    if not info["duration"]:
        return {}
    wav = os.path.join(os.path.dirname(ref_path), "ref_audio.wav")
    ffmpeg.run(["-i", ref_path, "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav])
    segs, words = transcribe.transcribe(wav, cfg, None)
    if not words:
        return {}
    avg_words_per_sec = len(words) / max(1.0, info["duration"])
    seg_durations = [s["end"] - s["start"] for s in segs if s["end"] > s["start"]]
    avg_seg_dur = sum(seg_durations) / max(1, len(seg_durations))
    return {
        "duration": info["duration"],
        "avg_words_per_sec": round(avg_words_per_sec, 2),
        "avg_seg_dur": round(avg_seg_dur, 2),
        "total_words": len(words),
        "total_segs": len(segs),
    }


def run(job, mgr):
    cfg = {**mgr.config_snapshot(), **(job.options or {})}
    t0 = time.time()
    try:
        job.set(phase="Preparing", progress=2)
        video = resolve_source(job, cfg)
        job.video_path = video
        info = ffmpeg.probe(video)
        if not info["width"] or not info["duration"]:
            raise RuntimeError("Could not read the video file. Is it a valid video?")
        job.info = info

        ref_data = {}
        ref_url = cfg.get("reference_url", "")
        if ref_url:
            job.set(phase="Analyzing reference video", progress=3)
            try:
                ref_dir = os.path.join(job.dir, "reference")
                os.makedirs(ref_dir, exist_ok=True)
                def ref_hook(pct):
                    job.set(progress=3 + 3 * pct)
                ref_path = download.download_url(ref_url, ref_dir, ref_hook)
                ref_path = download.ensure_mp4(ref_path, ref_dir)
                ref_data = analyze_reference(ref_path, cfg)
                job.reference = ref_data
            except Exception:
                ref_data = {}

        job.set(phase="Extracting audio", progress=8)
        wav = os.path.join(job.dir, "audio.wav")
        ffmpeg.run(["-i", video, "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", wav])

        device = transcribe.pick_device(cfg)
        model = cfg.get("whisper_model", "small")
        job.set(
            phase="Transcribing with Whisper (%s · %s)"
            % (model, "CPU" if device == "cpu" else "GPU"),
            progress=10,
        )
        if not transcribe.model_cached(model):
            job.set(phase="Downloading Whisper model (%s) — one-time download" % model, progress=10)

        def tcb(ratio, count, status):
            if status:
                job.set(phase=status)
            else:
                job.set(progress=10 + 36 * min(1.0, ratio))

        segs, words = transcribe.transcribe(wav, cfg, tcb)
        job.words = words
        job.transcript = segs
        if not words:
            raise RuntimeError("No speech detected in the video. Try a video with clear speech.")

        job.set(phase="Analyzing transcript quality", progress=46)
        job.issues = analyze.detect_issues(segments=segs)

        job.set(phase="Finding highlight moments", progress=48)

        cands = pick.pick_clips(segs, words, info["duration"], cfg, ref_data=ref_data)
        if not cands:
            raise RuntimeError("Could not find any highlight moments. Try a longer video or more clips.")
        job.clips = [job.new_clip(i + 1, c) for i, c in enumerate(cands)]

        job.set(phase="Analyzing speaker focus", progress=56)
        with ThreadPoolExecutor(max_workers=min(4, len(job.clips))) as pool:
            futs = {pool.submit(focus.analyze, job, clip): clip for clip in job.clips}
            for fut in as_completed(futs):
                fut.result()

        n = max(1, len(job.clips))
        with ThreadPoolExecutor(max_workers=min(4, len(job.clips))) as pool:
            futs = {}
            for i, clip in enumerate(job.clips):
                base = 60 + 36 * i / n
                clip.preview.update(state="rendering", progress=0)

                def render_one(c=clip, b=base):
                    def cb(pct):
                        c.preview["progress"] = round(100 * pct)
                    render.render_clip(job, c, c.preview_path(), True, cb)
                    c.preview.update(state="ready", progress=100)
                    return c

                futs[pool.submit(render_one)] = (i, clip)
            for fut in as_completed(futs):
                fut.result()

        job.set(phase="Done", progress=100)
        job.status = "ready"
        job.elapsed = round(time.time() - t0, 1)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.phase = "done"
