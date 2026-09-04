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
    "pop": "{\\fad(50,50)\\t(0,120,\\fscx115\\fscy115)}",
    "fade": "{\\fad(100,100)}",
    "slam": "{\\fad(40,80)\\t(0,100,\\fscx150\\fscy150)}",
    "bounce": "{\\fad(30,30)\\t(0,60,\\fscx130\\fscy130)\\t(60,120,\\fscx100\\fscy100)}",
    "zoom": "{\\fad(20,20)\\t(0,150,\\fscx108\\fscy108)}",
    "shake": "{\\fad(30,30)\\t(0,40,\\fsp3)\\t(40,80,\\fsp-3)\\t(80,120,\\fsp2)\\t(120,160,\\fsp0)}",
    "glow": "{\\fad(60,60)\\3c&H00FFFF&\\3a&H40&}",
    "slide": "{\\fad(40,40)\\t(0,120,\\fscx105\\fscy105)}",
    "punch": "{\\fad(20,40)\\t(0,50,\\fscx160\\fscy160)\\t(50,100,\\fscx100\\fscy100)}",
    "wave": "{\\fad(50,50)\\t(0,80,\\frz2)\\t(80,160,\\frz-2)\\t(160,240,\\frz0)}",
    "spin": "{\\fad(30,30)\\t(0,200,\\frz360)}",
    "jello": "{\\fad(30,30)\\t(0,50,\\fscx120\\fscy80)\\t(50,100,\\fscx85\\fscy115)\\t(100,150,\\fscx105\\fscy95)\\t(150,200,\\fscx100\\fscy100)}",
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


def build_ass(words, style, W, H, size, position, shift=0.0, headline=None, headline_on=True):
    fs = int(round(108 * float(size) * W / 1080))
    outline = max(2, int(round(8 * float(size) * W / 1080)))
    shadow = max(1, int(round(2 * float(size) * W / 1080)))
    margin_v = int(round(180 * W / 1080))
    align = {"bottom": 2, "top": 8, "center": 5}[position]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\n"
        "PlayResY: %d\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Arial Black,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,%d,%d,%d,40,40,%d,1\n"
        "Style: Head,Arial Black,%d,&H0000FFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,105,105,0,0,3,0,3,8,40,40,%d,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        % (W, H, fs, outline, shadow, align, margin_v,
           int(round(72 * W / 1080)), int(round(40 * W / 1080)))
    )
    anim = ANIMS.get(style, ANIMS["pop"])
    chunks = []
    cur = []
    for s, e, t in words:
        if cur and (len(cur) >= 8 or (s - cur[-1][1]) > 0.4 or sum(len(x[2]) for x in cur) > 40):
            chunks.append(cur)
            cur = []
        cur.append((s, e, t))
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        text = " ".join(x[2] for x in c).strip()
        text = wrap(text, 20)
        text = text.replace("{", "(").replace("}", ")")
        text = text.replace("\\N", "\x00N").replace("\\", "/").replace("\x00N", "\\N")
        out.append(
            "Dialogue: 0,%s,%s,Cap,,0,0,0,,%s%s\n"
            % (fmt_ts(c[0][0] - shift), fmt_ts(c[-1][1] - shift), anim, text)
        )
    if headline and headline_on:
        htext = str(headline).replace("{", "(").replace("}", ")").replace("\\", "/")
        if chunks:
            hstart = fmt_ts(max(0, chunks[0][0][0] - shift))
            hend = fmt_ts(chunks[-1][-1][1] - shift)
        else:
            hstart = "0:00:00.00"
            hend = "0:00:01.00"
        out.append(
            "Dialogue: 0,%s,%s,Head,,0,0,0,,{\\fad(200,200)}%s\n"
            % (hstart, hend, htext)
        )
    return header + "".join(out)
