import re
from collections import Counter


def detect_issues(segments):
    issues = []
    issues.extend(_detect_repeats(segments))
    issues.extend(_detect_fillers(segments))
    issues.extend(_detect_false_starts(segments))
    issues.extend(_detect_long_pauses(segments))
    return issues


def _detect_repeats(segments):
    issues = []
    for i, seg in enumerate(segments):
        text = seg.get("text", "")
        words = text.lower().split()
        for n in range(2, min(6, len(words) // 2 + 1)):
            for j in range(len(words) - 2 * n + 1):
                phrase = " ".join(words[j:j + n])
                rest = " ".join(words[j + n:j + 2 * n])
                if phrase == rest and len(phrase) > 4:
                    issues.append({
                        "type": "repeat",
                        "severity": "medium",
                        "time": seg["start"],
                        "text": "...%s %s..." % (phrase, phrase),
                        "message": "Repeated phrase detected",
                    })
                    break
    return issues


def _detect_fillers(segments):
    issues = []
    filler_patterns = [
        (r"\b(um|uh|erm|ah)\b", "hesitation filler"),
        (r"\b(like|you know|sort of|kind of)\b", "filler phrase"),
        (r"\b(basically|literally|honestly)\b", "crutch word"),
    ]
    for seg in segments:
        text = seg.get("text", "")
        words = text.split()
        filler_count = 0
        for pattern, label in filler_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            filler_count += len(matches)
        if filler_count >= 3 or (len(words) > 0 and filler_count / len(words) > 0.3):
            issues.append({
                "type": "filler",
                "severity": "low",
                "time": seg["start"],
                "text": text[:80],
                "message": "High filler word density (%d fillers in %d words)" % (filler_count, len(words)),
            })
    return issues


def _detect_false_starts(segments):
    issues = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if re.match(r"^(so|and|but|or|well|now|okay|right|so yeah|so basically),?\s", text, re.IGNORECASE):
            if len(text.split()) > 2:
                first_words = " ".join(text.split()[:4])
                issues.append({
                    "type": "false_start",
                    "severity": "low",
                    "time": seg["start"],
                    "text": first_words,
                    "message": "Possible false start or restart",
                })
    return issues


def _detect_long_pauses(segments):
    issues = []
    for i in range(1, len(segments)):
        gap = segments[i]["start"] - segments[i - 1]["end"]
        if gap > 2.0:
            issues.append({
                "type": "pause",
                "severity": "medium",
                "time": segments[i - 1]["end"],
                "text": "%.1fs silence" % gap,
                "message": "Long pause (%.1fs) between segments" % gap,
            })
    return issues


def get_issue_summary(issues):
    if not issues:
        return "No issues detected"
    counts = Counter(i["type"] for i in issues)
    parts = []
    for t, c in counts.most_common():
        parts.append("%d %s%s" % (c, t, "s" if c > 1 else ""))
    return "Found: " + ", ".join(parts)
