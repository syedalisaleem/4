def fmt_ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs = 0
        s += 1
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


ANIMS = {
    "pop": "{\\fad(70,70)\\t(0,140,\\fscx112\\fscy112)}",
    "fade": "{\\fad(130,130)}",
    "slam": "{\\fad(60,90)\\t(0,110,\\fscx140\\fscy140)}",
}


def wrap(text, n):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:
        mid = int(round(len(lines) / 2))
        lines = [" ".join(lines[:mid]), " ".join(lines[mid:])]
    return "\\N".join(lines)


def build_ass(words, style, W, H, size, position, shift=0.0):
    fs = int(round(96 * float(size) * W / 1080))
    outline = max(1, int(round(6 * float(size) * W / 1080)))
    margin_v = int(round(150 * W / 1080))
    align = {"bottom": 2, "top": 8, "center": 5}[position]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\n"
        "PlayResY: %d\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Arial,%d,&H00FFFFFF,&H000000FF,&H00141414,&H80000000,-1,0,0,0,100,100,0,0,1,%d,0,%d,30,30,%d,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        % (W, H, fs, outline, align, margin_v)
    )
    anim = ANIMS.get(style, ANIMS["pop"])
    chunks = []
    cur = []
    for s, e, t in words:
        if cur and (len(cur) >= 6 or (s - cur[-1][1]) > 0.4 or sum(len(x[2]) for x in cur) > 30):
            chunks.append(cur)
            cur = []
        cur.append((s, e, t))
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        text = " ".join(x[2] for x in c).strip()
        text = wrap(text, 16)
        text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
        out.append(
            "Dialogue: 0,%s,%s,Cap,,0,0,0,,%s%s\n"
            % (fmt_ts(c[0][0] - shift), fmt_ts(c[-1][1] - shift), anim, text)
        )
    return header + "".join(out)
