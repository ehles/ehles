"""Builds the profile panels in assets/ — the d3d4.ru LCD, frozen into SVG.

GitHub strips CSS and scripts from a README, so the panel is drawn ahead of
time: display words and the portrait are rasterised into 3px ink cells the
same way script.js does it, small text is turned into outlines, and the
phrase cycle is a CSS animation inside the SVG.

    python3 -m venv .venv && .venv/bin/pip install fonttools brotli pillow skia-pathops
    .venv/bin/python tools/build.py [path/to/ehles.github.io]
"""

import io
import random
import sys
from pathlib import Path

import pathops
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "ehles.github.io"
OUT = ROOT / "assets"

CELL = 3  # the pixel pitch, as --cell on the site
W = 960
GLASS, GLASS_DEEP = "#c4c4c0", "#b6b6b2"
GAP = "rgba(232,232,228,0.42)"
INK, INK_SOFT = "#0d0d0d", "#3a3a38"
SHADOW = 0.15
M = 36  # side margin


# ---------- fonts ----------

def load(name, wght=None):
    font = TTFont(SITE / "assets" / "fonts" / f"{name}-latin.woff2")
    font.flavor = None
    if wght is not None and "fvar" in font:
        font = instantiateVariableFont(font, {"wght": wght})
    return font


def pil_font(font, size):
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, size)


HANDJET = {w: load("handjet", w) for w in (400, 600)}
UNBOUNDED = load("unbounded")


def advance(font, text, size, tracking=0.0):
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    return sum(hmtx[cmap[ord(c)]][0] * scale + tracking * size for c in text) - tracking * size


_simple = {}


def simple_glyph(font, name):
    """Handjet builds each glyph from many overlapping elements; merge them into one clean outline."""
    key = (id(font), name)
    if key not in _simple:
        path = pathops.Path()
        glyphs = font.getGlyphSet()
        glyphs[name].draw(path.getPen(glyphSet=glyphs))
        path.simplify()
        _simple[key] = path
    return _simple[key]


def outline(font, text, x, y, size, tracking=0.0, anchor="start"):
    """Text as one path; y is the baseline."""
    if anchor == "end":
        x -= advance(font, text, size, tracking)
    cmap, glyphs, hmtx = font.getBestCmap(), font.getGlyphSet(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
    for ch in text:
        name = cmap[ord(ch)]
        simple_glyph(font, name).draw(TransformPen(pen, (scale, 0, 0, -scale, x, y)))
        x += hmtx[name][0] * scale + tracking * size
    return pen.getCommands()


def wrap(font, text, size, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and advance(font, trial, size) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


# ---------- ink cells ----------

def cells_to_path(cells, dx=0, dy=0):
    """Ink cells (a set of (x, y)) as horizontal runs, in cell units; draw it with ink()."""
    out = []
    for y in sorted({y for _, y in cells}):
        xs = sorted(x for x, yy in cells if yy == y)
        start = prev = xs[0]
        for x in xs[1:] + [None]:
            if x == prev + 1:
                prev = x
                continue
            out.append(f"M{start + dx} {y + dy + 0.5}h{prev - start + 1}")
            if x is not None:
                start = prev = x
    return "".join(out)


def ink(d, attrs=""):
    """A cell path stroked one cell thick and scaled up to the pixel pitch."""
    return f'<path transform="scale({CELL})" fill="none" stroke="{INK}" stroke-width="1"{attrs} d="{d}"/>'


def rasterize_lines(lines, max_w, max_h, gap=0.18):
    """Display words in Unbounded, scaled to fit max_w × max_h cells, left-aligned."""
    s = 8  # supersampling
    probe = pil_font(UNBOUNDED, 100)
    cap = probe.getbbox("H")[3] - probe.getbbox("H")[1]
    widest = max(probe.getlength(l) for l in lines)
    # the height a line of capitals takes, in cells
    h_cap = min(max_h / (len(lines) + gap * (len(lines) - 1)), max_w * cap / widest)
    size = h_cap * s * 100 / cap
    font = pil_font(UNBOUNDED, size)
    top = font.getbbox("H")[1]
    w, h = max_w * s, max_h * s
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((0, i * h_cap * (1 + gap) * s - top), line, font=font, fill=255)
    img = img.resize((max_w, max_h), Image.BOX)
    cells = {(x, y) for y in range(max_h) for x in range(max_w) if img.getpixel((x, y)) >= 128}
    used_h = round(h_cap * (len(lines) + gap * (len(lines) - 1)))
    return cells, used_h


def rasterize_picture(path, w, h):
    """The portrait, error-diffused into cells (Floyd–Steinberg), as rasterizePicture() does."""
    img = Image.open(path).convert("RGBA").resize((w, h), Image.LANCZOS)
    px = img.load()
    lum = [[1 - px[x, y][3] / 255 * (1 - px[x, y][0] / 255) for x in range(w)] for y in range(h)]
    cells = set()
    for y in range(h):
        for x in range(w):
            ink = lum[y][x] < 0.5
            if ink:
                cells.add((x, y))
            err = lum[y][x] - (0 if ink else 1)
            if x + 1 < w:
                lum[y][x + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    lum[y + 1][x - 1] += err * 3 / 16
                lum[y + 1][x] += err * 5 / 16
                if x + 1 < w:
                    lum[y + 1][x + 1] += err / 16
    return cells


# ---------- the panel ----------

def panel(height, body, style="", label=""):
    """Glass, ink with its hard shadow, the pixel grid and the sheen on top."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" role="img" aria-label="{label}">
<title>{label}</title>
<defs>
<linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{GLASS}"/><stop offset="1" stop-color="{GLASS_DEEP}"/></linearGradient>
<linearGradient id="sheen" x1="0" y1="0" x2="1" y2="0.6"><stop offset="0" stop-color="#fff" stop-opacity="0.22"/><stop offset="0.45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.06"/></linearGradient>
<pattern id="grid" width="{CELL}" height="{CELL}" patternUnits="userSpaceOnUse"><path d="M0 0h{CELL}M0 0v{CELL}" stroke="{GAP}" stroke-width="2"/></pattern>
<filter id="shadow" x="-2%" y="-2%" width="104%" height="104%"><feOffset in="SourceAlpha" dx="{CELL}" dy="{CELL}" result="o"/><feComponentTransfer in="o" result="s"><feFuncA type="linear" slope="{SHADOW}"/></feComponentTransfer><feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7"/><feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.05 0"/></filter>
<clipPath id="frame"><rect width="{W}" height="{height}" rx="12"/></clipPath>
</defs>
<style>{style}</style>
<g clip-path="url(#frame)">
<rect width="{W}" height="{height}" fill="url(#glass)"/>
<rect width="{W}" height="{height}" filter="url(#grain)"/>
<g filter="url(#shadow)" fill="{INK}">
{body}
</g>
<rect width="{W}" height="{height}" fill="url(#grid)"/>
<rect width="{W}" height="{height}" fill="url(#sheen)"/>
</g>
</svg>
"""


def label_row(y, left, right=None, size=24, color=INK):
    """A status-style line: Handjet 600, tracked; pass it in capitals, the handle keeps its case."""
    d = outline(HANDJET[600], left, M, y, size, 0.04)
    if right:
        d += outline(HANDJET[600], right, W - M, y, size, 0.04, "end")
    return f'<path fill="{color}" d="{d}"/>'


def rule(y, dotted=False):
    """A 3px line; dotted is the spec leader on the site."""
    if not dotted:
        return f'<rect x="{M}" y="{y}" width="{W - 2 * M}" height="{CELL}"/>'
    return f'<rect x="{M}" y="{y}" width="{W - 2 * M}" height="{CELL}" fill="url(#dots)"/>'


DOTS = f'<pattern id="dots" width="{CELL * 2}" height="{CELL}" patternUnits="userSpaceOnUse"><rect width="{CELL}" height="{CELL}" fill="{INK}"/></pattern>'


def spec(rows, y, x0=M, x1=W - M, big=False):
    """dt on the left in small caps, dd on the right, a dotted rule under each row."""
    out = []
    step = 54 if big else 46
    for dt, dd in rows:
        base = y + (34 if big else 30)
        out.append(f'<path d="{outline(HANDJET[600], dt.upper(), x0, base, 22, 0.04)}"/>')
        out.append(f'<path d="{outline(HANDJET[400], dd, x1, base, 34 if big else 28, 0, "end")}"/>')
        out.append(f'<rect x="{x0}" y="{y + step - CELL}" width="{x1 - x0}" height="{CELL}" fill="url(#dots)"/>')
        y += step
    return "".join(out), y


def title(word, y, rows=22):
    """A section title in ink cells, like .ink-title."""
    cells, h = rasterize_lines([word], (W - 2 * M) // CELL, rows)
    return ink(cells_to_path(cells, M // CELL, y // CELL)), y + h * CELL


def paragraph(text, y, size=30, width=W - 2 * M, font=None, x=M, leading=1.2):
    font = font or HANDJET[400]
    out = []
    for line in wrap(font, text, size, width):
        y += size * leading
        out.append(outline(font, line, x, y - size * 0.22, size))
    return f'<path d="{"".join(out)}"/>', y


# ---------- header: the intro screen ----------

PHRASES = [
    (["DENIS", "0xD3D4"], "Software engineer"),
    (["STORAGE"], "File systems for SSDs, object and block storage, storage arrays"),
    (["NETWORK"], "Packet processing at line rate"),
    (["TELECOM"], "VoIP call routing over SIP and H.323, real-time VoIP and LTE decoding"),
    (["SECURITY"], "Malware scanning and sandboxes"),
    (["NIZHNY", "NOVGOROD"], "Where the Oka meets the Volga"),
]
HOLD = 3.2  # seconds per phrase
WAVES = 4  # each phrase lands and leaves in this many scattered waves of cells


def header():
    height = 456
    top, bottom = 72, 380  # the display between the status bar and the foot
    rows = (bottom - top) // CELL
    cols = (W - 2 * M) // CELL
    n = len(PHRASES)
    period = HOLD * n
    on = 100 / n
    style = [
        f"@keyframes on{{0%,{on - 0.01:.2f}%{{opacity:1}}{on:.2f}%,100%{{opacity:0}}}}",
        f".p{{opacity:0;animation:on {period}s step-end infinite}}",
        "@keyframes blink{50%{opacity:0}}.blink{animation:blink 1.2s step-end infinite}",
        "@media (prefers-reduced-motion:reduce){.p{animation:none}.p0{opacity:1}}",
    ]
    rnd = random.Random(73)
    body = []

    # status bar
    body.append(label_row(36, "D3D4.RU"))
    nav = outline(HANDJET[600], "ABOUT   PROJECTS   73", W / 2 - 92, 36, 24, 0.04)
    body.append(f'<path d="{nav}"/>')
    clock = outline(HANDJET[600], "NN UTC+3", W - M, 36, 24, 0.04, "end")
    sx = W - M - advance(HANDJET[600], "NN UTC+3", 24, 0.04) - 34
    bars = "".join(f'<rect x="{sx + i * 6}" y="{36 - 6 - i * 3}" width="3" height="{6 + i * 3}"/>' for i in range(4))
    body.append(f'<path d="{clock}"/>{bars}')

    portrait = rasterize_picture(SITE / "assets" / "avatar.webp", 74, rows + 4)
    for i, (lines, caption) in enumerate(PHRASES):
        words_w = cols - (78 if i == 0 else 0)
        cells, h = rasterize_lines(lines, words_w, rows if len(lines) > 1 else rows * 2 // 3)
        dy = top // CELL + (rows - h) // 2
        cells = {(x + M // CELL, y + dy) for x, y in cells}
        if i == 0:
            cells |= {(x + M // CELL + cols - 74, y + top // CELL) for x, y in portrait}
        # scatter the cells into waves, block by block, so the phrase dissolves in
        waves = [set() for _ in range(WAVES)]
        blocks = {}
        for x, y in cells:
            block = blocks.setdefault((x // 6, y // 6), rnd.randrange(WAVES))
            waves[block].add((x, y))
        for k, wave in enumerate(waves):
            delay = i * HOLD - period + k * 0.07
            body.append(ink(cells_to_path(wave), f' class="p p{i}" style="animation-delay:{delay:.2f}s"'))
        # foot: caption and the counter
        cap = outline(HANDJET[400], caption, M, 428, 32)
        count = outline(HANDJET[600], f"{i + 1}/{n}", W - M - 74, 426, 24, 0.04, "end")
        body.append(f'<path class="p p{i}" style="animation-delay:{i * HOLD - period:.2f}s" d="{cap}{count}"/>')

    hint = outline(HANDJET[600], "LIVE", W - M, 426, 24, 0.04, "end")
    body.append(f'<path class="blink" d="{hint}"/>')
    body.insert(0, f'<rect x="{M}" y="54" width="{W - 2 * M}" height="{CELL}"/>')
    return panel(height, "\n".join(body), "".join(style),
                 "Denis, 0xD3D4. Software engineer in Nizhny Novgorod: storage, network, telecom, security.")


# ---------- sections ----------

def about():
    body = []
    t, y = title("ABOUT", 36)
    body.append(t)
    y += 40
    col = 460
    lead, ly = paragraph("I build the layer nobody sees until it breaks.", y, 34, col, UNBOUNDED, leading=1.15)
    body.append(lead)
    text, ly = paragraph(
        "File systems for SSDs. Object and block storage for clouds and for storage arrays. "
        "Packets at line rate. VoIP call routing and real-time decoding of VoIP and LTE traffic. "
        "Sandboxes that make malware show its hand. Go and Python, always on Linux.", ly + 20, 30, col)
    body.append(text)
    text, ly = paragraph(
        "Off the clock I am R3TBE on the air, and the author of an app that gets new radio "
        "amateurs through their licence exam.", ly + 20, 30, col)
    body.append(text)
    rows, sy = spec([
        ("Call sign", "R3TBE"),
        ("QTH", "Nizhny Novgorod"),
        ("Works at", "YADRO"),
        ("On GitHub", "since 2011"),
        ("Handle", "0xD3D4"),
        ("Languages", "Go, Python, Shell"),
        ("Runs on", "Kubernetes, ClickHouse, Linux"),
    ], y, M + col + 48)
    body.append(rows)
    height = max(ly, sy) + 40
    return panel(height, f"<defs>{DOTS}</defs>" + "\n".join(body), label=
                 "About: I build the layer nobody sees until it breaks. Storage, networking, telecom and security software in Go and Python on Linux. Call sign R3TBE.")


def projects():
    body = []
    t, y = title("PROJECTS", 36)
    body.append(t)
    y += 44
    col = 470
    body.append(f'<path d="{outline(UNBOUNDED, "HamTutor", M, y + 30, 34)}"/>')
    text, ty = paragraph(
        "An Android app for the Russian amateur radio operator exam, from category 4 up to "
        "category 1. Tests follow the real exam: the same number of questions, the same pass mark.",
        y + 44, 30, col)
    body.append(text)
    # the key: inverse video, like .key
    label = "INSTALL FROM RUSTORE"
    kw = advance(HANDJET[600], label, 26, 0.04) + 36
    ky = ty + 28
    body.append(f'<rect x="{M}" y="{ky}" width="{kw:.0f}" height="48"/>')
    body.append(f'<path fill="{GLASS}" d="{outline(HANDJET[600], label, M + 18, ky + 33, 26, 0.04)}"/>')
    rows, sy = spec([
        ("Categories", "4"),
        ("Modes", "test, study, training"),
        ("Questions, category 1", "426"),
        ("Rating on RuStore", "4.9 (12)"),
        ("Installs", "1k+"),
    ], y, M + col + 60)
    body.append(rows)
    height = max(ky + 48, sy) + 40
    return panel(height, f"<defs>{DOTS}</defs>" + "\n".join(body), label=
                 "Projects: HamTutor, an Android app for the Russian amateur radio exam, categories 4 to 1. Install from RuStore.")


def contact():
    body = []
    t, y = title("73", 36)
    body.append(t)
    note, y = paragraph("Radio amateurs end a contact with 73: best regards.", y + 16, 30)
    body.append(note)
    rows, y = spec([
        ("Site", "d3d4.ru"),
        ("RuStore", "ru.d3d4.ham"),
        ("QTH", "Nizhny Novgorod"),
        ("Time", "UTC+3"),
    ], y + 20, big=True)
    body.append(rows)
    y += 30
    body.append(rule(y))
    body.append(label_row(y + 42, "© DENIS, 0xD3D4", "NIZHNY NOVGOROD", 22, INK_SOFT))
    return panel(y + 66, f"<defs>{DOTS}</defs>" + "\n".join(body), label=
                 "73: best regards. d3d4.ru, Nizhny Novgorod, UTC+3.")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, build in [("panel", header), ("about", about), ("projects", projects), ("73", contact)]:
        svg = build()
        (OUT / f"{name}.svg").write_text(svg)
        print(f"assets/{name}.svg  {len(svg) // 1024} KB")
