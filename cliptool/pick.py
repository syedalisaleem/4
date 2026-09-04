import json
import re

import requests


def _build_prompt(segments, cfg):
    k = max(1, min(12, int(cfg.get("max_clips", 6))))
    mn = max(8, int(cfg.get("min_len", 12)))
    mx = max(mn, int(cfg.get("max_len", 45)))
    rows = []
    for i, s in enumerate(segments):
        rows.append("[%d] %.1f-%.1f: %s" % (i, s["start"], s["end"], s["text"][:160]))
    prompt = """You are an expert short-form video editor for TikTok, Reels and Shorts.

Below is a timestamped transcript of a longer video. Choose the strongest %d moments to turn into vertical 9:16 clips that will perform well as standalone short-form videos.

Each clip must:
- be a complete, self-contained mini-story: a hook that lands in the first 3 seconds, then a payoff
- be between %d and %d seconds long
- start at the beginning of a sentence or thought, never in the middle of a word
- avoid rambling, filler words, technical digressions and long silences
- vary across the whole video - never pick adjacent moments

Transcript:
%s

Respond ONLY with a JSON object, no other text:
{"clips": [{"start": 12.3, "end": 27.8, "title": "catchy title under 40 chars", "reason": "one sentence why this will perform", "score": 9}]}
Use start/end in seconds as floating point numbers, score 1-10.""" % (
        k, mn, mx, "\n".join(rows)
    )
    return [
        {"role": "system", "content": "You are a precise JSON-returning video editing AI."},
        {"role": "user", "content": prompt},
    ]


def _parse_json(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    else:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
    return json.loads(text)


def _via_ollama(cfg, messages):
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/")
    r = requests.post(
        url + "/api/chat",
        json={
            "model": cfg.get("ollama_model", "llama3.1"),
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        },
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError("Ollama error %s: %s" % (r.status_code, r.text[:300]))
    data = r.json()
    return _parse_json(data.get("message", {}).get("content", "{}"))


def _via_api(cfg, messages):
    key = cfg.get("api_key", "")
    if not key:
        raise RuntimeError("No API key configured")
    base = cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": "Bearer %s" % key}
    body = {
        "model": cfg.get("api_model", "gpt-4o-mini"),
        "messages": messages,
        "temperature": 0.2,
    }
    url = base + "/chat/completions"
    payload = body
    if base.endswith("/v1"):
        payload = {**body, "response_format": {"type": "json_object"}}
    r = requests.post(url, json=payload, headers=headers, timeout=300)
    if r.status_code == 400 and payload is not body:
        r = requests.post(url, json=body, headers=headers, timeout=300)
    if r.status_code != 200:
        raise RuntimeError("API error %s: %s" % (r.status_code, r.text[:400]))
    return _parse_json(r.json()["choices"][0]["message"]["content"])


def _ollama_up(cfg):
    try:
        r = requests.get(cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def pick_clips(segments, words, duration, cfg):
    max_clips = max(1, min(12, int(cfg.get("max_clips", 6))))
    min_len = max(6, int(cfg.get("min_len", 12)))
    max_len = max(min_len, int(cfg.get("max_len", 45)))

    mode = cfg.get("mode", "auto")
    if mode == "auto":
        if cfg.get("api_key"):
            mode = "api"
        elif _ollama_up(cfg):
            mode = "ollama"
        else:
            mode = "heuristic"

    cands = []
    err = None
    if mode in ("api", "ollama"):
        segs = [s for s in segments if s["end"] - s["start"] >= 0.4]
        if len(segs) > 150:
            step = len(segs) / 150
            segs = [segs[int(i * step)] for i in range(150)]
        if not segs:
            return _heuristic(segments, words, max_clips, min_len, max_len)
        messages = _build_prompt(segs, cfg)
        try:
            if mode == "ollama":
                data = _via_ollama(cfg, messages)
            else:
                data = _via_api(cfg, messages)
            for c in data.get("clips", []):
                try:
                    cands.append({
                        "start": float(c["start"]),
                        "end": float(c["end"]),
                        "title": str(c.get("title", ""))[:60],
                        "reason": str(c.get("reason", ""))[:200],
                        "score": min(10.0, max(1.0, float(c.get("score", 5)))),
                    })
                except Exception:
                    continue
        except Exception as e:
            err = e
    if not cands:
        cands = _heuristic(segments, words, max_clips, min_len, max_len)
        if mode in ("api", "ollama") and err:
            for c in cands:
                c["reason"] = "%s — fallback: %s" % (c["reason"], err)
    return _finalize(cands, words, duration, max_clips, min_len, max_len)


def _heuristic(segments, words, max_clips, min_len, max_len):
    spans = []
    cur = None
    for s in segments:
        if cur is None or s["start"] - cur["end"] > 1.0:
            if cur:
                spans.append(cur)
            cur = {"start": s["start"], "end": s["end"], "texts": [s["text"]]}
        else:
            cur["end"] = max(cur["end"], s["end"])
            cur["texts"].append(s["text"])
    if cur:
        spans.append(cur)
    scored = []
    for sp in spans:
        d = sp["end"] - sp["start"]
        if d < 4:
            continue
        nwords = sum(len(t.split()) for t in sp["texts"])
        scored.append({"start": sp["start"], "end": sp["end"], "density": nwords / d, "nwords": nwords})
    scored.sort(key=lambda x: -x["density"])
    picked = []
    for sp in scored:
        if len(picked) >= max_clips:
            break
        if any(abs(sp["start"] - p["start"]) < 10 for p in picked):
            continue
        s, e = sp["start"], sp["end"]
        if e - s > max_len:
            e = s + max_len
        if e - s < 6:
            continue
        picked.append(sp)
        yield_ = {
            "start": s,
            "end": e,
            "title": "Highlight %d" % (len(picked),),
            "reason": "Peak talking energy (%.1f words/sec)." % sp["density"],
            "score": min(9.0, 5.0 + sp["density"]),
        }
        picked[-1] = yield_
    return picked


def _finalize(cands, words, duration, max_clips, min_len, max_len):
    ws = [w[0] for w in words]
    we = [w[1] for w in words]

    def snap_start(t):
        best, bd = None, 1e9
        for i, s in enumerate(ws):
            d = abs(s - t)
            if d < bd:
                best, bd = i, d
        if best is None:
            return t
        if abs(ws[best] - t) <= 2.0 and ws[best] <= t + 1.0:
            return ws[best]
        return t

    def snap_end(t):
        best, bd = None, 1e9
        for i, e in enumerate(we):
            d = abs(e - t)
            if d < bd:
                best, bd = i, d
        if best is None:
            return t
        if abs(we[best] - t) <= 2.5:
            return we[best]
        return t

    taken = []
    out = []
    for c in sorted(cands, key=lambda x: -x["score"]):
        if len(out) >= max_clips:
            break
        s = max(0.0, min(duration - 5, snap_start(c["start"])))
        e = max(s + 5, min(duration, snap_end(c["end"])))
        if e - s < 5:
            continue
        if e - s > max_len + 5:
            e = s + max_len
        if any(s < t[1] + 1.5 and e > t[0] - 1.5 for t in taken):
            continue
        taken.append((s, e))
        out.append({**c, "start": round(s, 2), "end": round(e, 2)})
    return out
