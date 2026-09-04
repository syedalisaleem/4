import os

from . import captions, ffmpeg, focus


def esc_path(p):
    return "'%s'" % str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def crop_filter(info, samples, zoom, window):
    sw, sh = info["width"], info["height"]
    cw0, ch0 = window
    cw = cw0 / zoom
    ch = ch0 / zoom
    if cw >= sw - 1 and ch >= sh - 1:
        return None
    pts = []
    for t, cx, cy in samples:
        x = min(max(cx, cw / 2), sw - cw / 2) - cw / 2
        y = min(max(cy, ch / 2), sh - ch / 2) - ch / 2
        pts.append((t, x, y))

    def build(idx):
        if (idx == 0 and cw >= sw - 1) or (idx == 1 and ch >= sh - 1):
            return "0"
        pieces = []
        for i in range(len(pts) - 1):
            t0, a0, b0 = pts[i]
            t1, a1, b1 = pts[i + 1]
            if t1 - t0 <= 1e-6:
                continue
            v0 = a0 if idx == 0 else b0
            v1 = a1 if idx == 0 else b1
            pieces.append((t0, t1, "(%f+(%f)*((t-%f)/%f))" % (v0, v1 - v0, t0, t1 - t0)))
        expr = pieces[-1][2]
        for t0, t1, lx in reversed(pieces[:-1]):
            expr = "if(between(t,%f,%f),%s,%s)" % (t0, t1, lx, expr)
        return expr.replace(",", "\\,")

    return "crop=%f:%f:%s:%s" % (cw, ch, build(0), build(1))


def render_clip(job, clip, out_path, preview, progress_cb):
    info = job.info
    s = clip.settings
    W, H = (270, 480) if preview else (1080, 1920)
    if clip.window is None:
        clip.window = focus.window_size(info)
    filters = []
    crop = crop_filter(info, clip.samples, float(s.get("zoom", 1.0)), clip.window)
    if crop:
        filters.append(crop)
    filters.append("scale=%d:%d:flags=bicubic" % (W, H))
    if s.get("captions", True) and clip.words:
        ass_path = os.path.join(clip.dir, "caps_%dx%d.ass" % (W, H))
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(captions.build_ass(
                clip.words, s.get("style", "pop"), W, H,
                float(s.get("size", 1.0)), s.get("position", "bottom"),
                shift=clip.start,
                headline=s.get("headline_text", clip.headline),
                headline_on=s.get("headline", True),
            ))
        filters.append("ass=%s" % esc_path(ass_path))
    args = [
        "-ss", "%.3f" % clip.start, "-t", "%.3f" % clip.dur(), "-i", job.video_path,
    ]
    if filters:
        args += ["-vf", ",".join(filters)]
    args += [
        "-c:v", "libx264",
        "-preset", "ultrafast" if preview else "medium",
        "-crf", "28" if preview else "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-threads", "0",
    ]
    if preview:
        args += ["-r", "24"]
    afilters = []
    fade_in = float(s.get("audio_fade_in", 0))
    fade_out = float(s.get("audio_fade_out", 0))
    if fade_in > 0:
        afilters.append("afade=t=in:st=0:d=%.2f" % min(fade_in, clip.dur()))
    if fade_out > 0:
        fo = max(0, clip.dur() - min(fade_out, clip.dur()))
        afilters.append("afade=t=out:st=%.2f:d=%.2f" % (fo, min(fade_out, clip.dur() - fo)))
    if info.get("has_audio"):
        args += ["-c:a", "aac", "-b:a", "128k"]
        if afilters:
            args += ["-af", ",".join(afilters)]
    else:
        args += ["-an"]
    args.append(str(out_path))
    ffmpeg.run(args, progress_cb=progress_cb, duration=clip.dur())
