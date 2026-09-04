import os
import shutil

import cv2

from . import ffmpeg

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet.onnx")
CASCADE_PATH = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")

SMOOTH = 5


class FaceDetector:
    def __init__(self):
        self._yunet = None
        self._cascade = None
        self._size = None

    def _load_yunet(self, w, h):
        if self._yunet is None and os.path.exists(MODEL_PATH):
            try:
                self._yunet = cv2.FaceDetectorYN_create(MODEL_PATH, "", (w, h), 0.5, 0.3, 5000)
            except Exception:
                self._yunet = None
        if self._yunet is not None and self._size != (w, h):
            try:
                self._yunet.setInputSize((w, h))
                self._size = (w, h)
            except Exception:
                pass

    def detect(self, img):
        h, w = img.shape[:2]
        if self._yunet is None and self._cascade is None:
            self._load_yunet(w, h)
        if self._yunet is not None:
            self._load_yunet(w, h)
            try:
                res = self._yunet.detect(img)
                faces = res[1] if isinstance(res, tuple) else res
            except Exception:
                faces = None
            out = []
            if faces is not None and len(faces):
                for f in faces:
                    try:
                        x, y, fw, fh, score = int(f[0]), int(f[1]), float(f[2]), float(f[3]), float(f[14])
                    except Exception:
                        continue
                    if fw >= 24 and score >= 0.5:
                        out.append((x, y, fw, fh))
            return out
        if self._cascade is None and hasattr(cv2, "CascadeClassifier") and os.path.exists(CASCADE_PATH):
            try:
                self._cascade = cv2.CascadeClassifier(CASCADE_PATH)
            except Exception:
                self._cascade = None
        if self._cascade is not None:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = self._cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(30, 30))
            return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]
        return []


def window_size(info):
    sw, sh = info["width"], info["height"]
    if sw <= 0 or sh <= 0:
        return 0, 0
    aspect = sw / sh
    target = 9 / 16
    if aspect > target:
        cw, ch = sh * target, sh
    else:
        cw, ch = sw, sw / target
    return min(cw, sw), min(ch, sh)


def analyze(job, clip):
    video = job.video_path
    info = job.info
    sw, sh = info["width"], info["height"]
    clip.window = window_size(info)
    if clip.window[0] <= 0:
        clip.samples = [(0.0, sw / 2, sh / 2)]
        clip.faces = []
        clip.primary = -1
        return
    start, end = clip.start, clip.end
    dur = max(0.5, end - start)
    dt = max(0.35, min(1.2, dur / 40))
    n = max(2, int(round(dur / dt)))
    frames_dir = os.path.join(clip.dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    ffmpeg.run([
        "-ss", "%.3f" % start, "-t", "%.3f" % dur, "-i", video,
        "-vf", "fps=1/%.6f,scale=480:-2" % dt, "-q:v", "4",
        os.path.join(frames_dir, "f_%04d.jpg"),
    ])
    detector = FaceDetector()
    tracks = {}
    last_pos = {}
    next_id = 0
    factor_x = sw / 480.0
    frames_done = False
    for i in range(n):
        fp = os.path.join(frames_dir, "f_%04d.jpg" % (i + 1))
        if not os.path.exists(fp):
            break
        img = cv2.imread(fp)
        if img is None:
            continue
        frames_done = True
        hh, ww = img.shape[:2]
        factor_y = sh / float(hh)
        faces = detector.detect(img)
        for (x, y, w, h) in faces:
            cx = (x + w / 2.0) * factor_x
            cy = (y + h / 2.0) * factor_y
            best, best_d = None, 1e18
            for tid, lp in last_pos.items():
                d = (lp[0] - cx) ** 2 + (lp[1] - cy) ** 2
                if d < best_d:
                    best, best_d = tid, d
            if best is not None and best_d < (0.35 * sw) ** 2:
                tid = best
            else:
                tid = next_id
                next_id += 1
                tracks[tid] = {"idx": [], "boxes": []}
            tracks[tid]["idx"].append(i)
            tracks[tid]["boxes"].append((cx, cy, x / factor_x, y / factor_y, w / factor_x, h / factor_y))
            last_pos[tid] = (cx, cy)
        for tid in list(last_pos.keys()):
            if tracks[tid]["idx"] and i - tracks[tid]["idx"][-1] > 2:
                del last_pos[tid]
    if not frames_done or not tracks:
        clip.samples = [((k + 0.5) * dt, sw / 2, sh / 2) for k in range(n)]
        clip.faces = []
        clip.primary = -1
        shutil.rmtree(frames_dir, ignore_errors=True)
        return
    order = sorted(tracks.keys(), key=lambda t: -len(tracks[t]["idx"]))
    clip.primary = order[0] if order else -1
    faces = []
    for k, tid in enumerate(order):
        tr = tracks[tid]
        i0 = tr["idx"][0]
        fp = os.path.join(frames_dir, "f_%04d.jpg" % (i0 + 1))
        img = cv2.imread(fp)
        thumb_path = os.path.join(clip.dir, "face_%d.jpg" % k)
        if img is not None:
            bx, by, bw, bh = tr["boxes"][0][2], tr["boxes"][0][3], tr["boxes"][0][4], tr["boxes"][0][5]
            pad = 0.3
            x0 = max(0, int(bx - bw * pad))
            y0 = max(0, int(by - bh * pad))
            x1 = min(img.shape[1], int(bx + bw * (1 + pad)))
            y1 = min(img.shape[0], int(by + bh * (1 + pad)))
            crop = img[y0:y1, x0:x1]
            if crop.size and max(crop.shape[:2]) > 4:
                scale = 160 / float(max(crop.shape[:2]))
                crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
                cv2.imwrite(thumb_path, crop)
        src_boxes = [[round((idx + 0.5) * dt, 3), round(b[0], 1), round(b[1], 1)] for idx, b in zip(tr["idx"], tr["boxes"])]
        faces.append({
            "label": "Person %d" % (k + 1),
            "thumb": "/data/jobs/%s/clip_%d/face_%d.jpg" % (job.id, clip.cid, k),
            "count": len(tr["idx"]),
            "pct": round(100.0 * len(tr["idx"]) / max(1, n)),
            "boxes": src_boxes,
        })
    clip.faces = faces
    clip.samples = build_samples(faces, clip.settings, n, dt, sw, sh)
    shutil.rmtree(frames_dir, ignore_errors=True)


def build_samples(faces, settings, n, dt, sw, sh):
    fmode = settings.get("focus_mode", "auto")
    fidx = int(settings.get("face_idx", -1))
    if fmode == "center" or not faces:
        return [((k + 0.5) * dt, sw / 2, sh / 2) for k in range(n)]
    if fmode == "face":
        fidx = min(max(fidx, 0), len(faces) - 1)
    else:
        fidx = 0
    target = {}
    for idx, cx, cy in faces[fidx]["boxes"]:
        target[idx] = (cx, cy)
    pts = []
    last = None
    for k in range(n):
        if k in target:
            last = target[k]
        if last:
            pts.append(last)
        else:
            pts.append((sw / 2, sh / 2))
    pts = _smooth(pts, SMOOTH, sw, sh)
    return [((k + 0.5) * dt, pts[k][0], pts[k][1]) for k in range(n)]


def _smooth(pts, w, sw, sh):
    out = []
    for k in range(len(pts)):
        lo = max(0, k - w)
        hi = min(len(pts), k + w + 1)
        xs = [p[0] for p in pts[lo:hi]]
        ys = [p[1] for p in pts[lo:hi]]
        out.append((min(max(sum(xs) / len(xs), 0), sw), min(max(sum(ys) / len(ys), 0), sh)))
    return out
