import os
import re
import shutil

from . import ffmpeg

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".mpg", ".mpeg", ".flv", ".wmv"}


def is_url(s):
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def _normalize_url(url):
    m = re.match(r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return "https://drive.google.com/uc?export=download&id=" + m.group(1)
    m = re.match(r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)", url)
    if m:
        return "https://drive.google.com/uc?export=download&id=" + m.group(1)
    return url


def download_url(url, out_dir, progress_cb):
    import yt_dlp

    url = _normalize_url(url)

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress_cb(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/bestvideo[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "progress_hooks": [hook],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
    }
    cookies_file = os.path.join(os.path.dirname(out_dir), "..", "data", "cookies.txt")
    if os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    else:
        cookies_file2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cookies.txt")
        if os.path.exists(cookies_file2):
            opts["cookiefile"] = cookies_file2

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception:
        opts["extractor_args"] = {"youtube": {"player_client": ["web_creator", "web"]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception:
            opts.pop("extractor_args", None)
            opts["extractor_args"] = {"youtube": {"player_client": ["mweb"]}}
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
