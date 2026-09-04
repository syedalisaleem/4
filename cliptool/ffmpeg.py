import json
import os
import re
import shutil
import subprocess

import imageio_ffmpeg

CREATE_NO_WINDOW = 0x08000000

_FFMPEG = None
_FFPROBE = None


def ffmpeg_bin():
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    return _FFMPEG


def ffprobe_bin():
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = shutil.which("ffprobe")
    return _FFPROBE


def _flags():
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def version():
    try:
        r = subprocess.run(
            [ffmpeg_bin(), "-version"], capture_output=True, text=True, timeout=10, creationflags=_flags()
        )
        return r.stdout.splitlines()[0]
    except Exception as e:
        return str(e)


def probe(path):
    fb = ffprobe_bin()
    if fb:
        try:
            r = subprocess.run(
                [fb, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=90, creationflags=_flags(),
            )
            d = json.loads(r.stdout)
            fmt = d.get("format", {})
            streams = d.get("streams", [])
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            if v:
                fps = 30.0
                fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or ""
                if "/" in fr:
                    try:
                        num, den = fr.split("/")
                        fps = float(num) / max(1.0, float(den))
                    except Exception:
                        pass
                return {
                    "duration": float(fmt.get("duration") or 0),
                    "width": int(v.get("width") or 0),
                    "height": int(v.get("height") or 0),
                    "fps": fps,
                    "has_audio": a is not None,
                    "codec": v.get("codec_name"),
                }
        except Exception:
            pass
    r = subprocess.run([ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True, timeout=90, creationflags=_flags())
    err = r.stderr
    dur = 0.0
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    w = h = 0
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    return {"duration": dur, "width": w, "height": h, "fps": 30.0, "has_audio": "Audio:" in err, "codec": None}


def run(args, progress_cb=None, duration=None):
    cmd = [ffmpeg_bin(), "-nostdin", "-y", "-hide_banner"] + args
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=_flags(),
    )
    tail = []
    while True:
        line = p.stdout.readline()
        if not line:
            break
        tail.append(line)
        if len(tail) > 30:
            tail.pop(0)
        m = re.search(r"out_time_ms=(\d+)", line)
        if m and duration and progress_cb:
            pct = int(m.group(1)) / 1e6 / duration
            progress_cb(max(0.0, min(0.99, pct)))
    rc = p.wait()
    if rc != 0:
        raise RuntimeError("ffmpeg failed (code %s): %s" % (rc, "".join(tail[-12:]).strip()))
