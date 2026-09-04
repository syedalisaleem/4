import os

from . import download, ffmpeg, focus, pick, render, transcribe


def resolve_source(job, cfg):
    if job.kind == "url":
        if not download.is_url(job.source):
            raise RuntimeError("Not a valid URL")
        job.set(phase="Downloading from YouTube", progress=4)

        def hook(pct):
            job.set(progress=4 + 5 * pct)

        path = download.download_url(job.source, job.dir, hook)
        return download.ensure_mp4(path, job.dir)
    return download.prepare_local(job.source, job.dir)


def run(job, mgr):
    cfg = {**mgr.config_snapshot(), **(job.options or {})}
    try:
        job.set(phase="Preparing", progress=2)
        video = resolve_source(job, cfg)
        job.video_path = video
        info = ffmpeg.probe(video)
        if not info["width"] or not info["duration"]:
            raise RuntimeError("Could not read the video file. Is it a valid video?")
        job.info = info

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
        job.set(phase="Finding highlight moments", progress=48)

        cands = pick.pick_clips(segs, words, info["duration"], cfg)
        if not cands:
            raise RuntimeError("Could not find any highlight moments. Try a longer video or more clips.")
        job.clips = [job.new_clip(i + 1, c) for i, c in enumerate(cands)]

        job.set(phase="Analyzing speaker focus", progress=56)
        for clip in job.clips:
            focus.analyze(job, clip)
        n = max(1, len(job.clips))
        for i, clip in enumerate(job.clips):
            base = 60 + 36 * i / n
            job.set(phase="Rendering preview %d/%d" % (i + 1, n), progress=base)
            clip.preview.update(state="rendering", progress=0)

            def cb(pct, c=clip, b=base):
                c.preview["progress"] = round(100 * pct)
                job.set(progress=b + 0.9 * pct)

            render.render_clip(job, clip, clip.preview_path(), True, cb)
            clip.preview.update(state="ready", progress=100)

        job.set(phase="Done", progress=100)
        job.status = "ready"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.phase = "done"
