import os
import shutil

from . import ffmpeg

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".mpg", ".mpeg", ".flv", ".wmv"}


def is_url(s):
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def download_url(url, out_dir, progress_cb):
    import yt_dlp

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress_cb(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for f in os.listdir(out_dir):
        if f.startswith("source."):
            return os.path.join(out_dir, f)
    raise RuntimeError("Download finished but no video file was found")


def prepare_local(path, out_dir):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise RuntimeError("File not found: %s" % path)
    ext = os.path.splitext(path)[1].lower() or ".mp4"
    if ext not in VIDEO_EXTS:
        ext = ".mp4"
    dest = os.path.join(out_dir, "source" + ext)
    if os.path.abspath(dest) != path:
        shutil.copy2(path, dest)
    if ext != ".mp4":
        conv = os.path.join(out_dir, "source.mp4")
        ffmpeg.run(["-i", dest, "-c", "copy", "-movflags", "+faststart", conv])
        os.remove(dest)
        return conv
    return dest


def ensure_mp4(video_path, out_dir):
    ext = os.path.splitext(video_path)[1].lower()
    if ext == ".mp4":
        return video_path
    conv = os.path.join(out_dir, "source.mp4")
    ffmpeg.run(["-i", video_path, "-c", "copy", "-movflags", "+faststart", conv])
    return conv
