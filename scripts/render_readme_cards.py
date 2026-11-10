"""Render every README card in cards/ as an animated SVG.

    python scripts/render_readme_cards.py            # animated cards
    STATIC=1 python scripts/render_readme_cards.py   # final frame only, for screenshots
    PREVIEW=1 python scripts/render_readme_cards.py  # also writes preview.html (gitignored)

The cards are shown through <img>, so everything they need lives inside them: an inline
<style> with CSS keyframes, a little SMIL, and fonts subset to the exact characters each
card uses and embedded as base64 WOFF. Delays are staggered across cards so the page plays
top to bottom: hero, blue graph, monthly bars, tiles, repos. Every animation is switched off
under prefers-reduced-motion, which shows the same final frame as STATIC=1.

Edit CONFIG below to change the words, links and featured repos.
"""
from __future__ import annotations

import base64
import hashlib
import calendar
import datetime as dt
import html
import io
import json
import os
import pathlib
import re
from collections import defaultdict
from functools import lru_cache

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS, FONTS, DATA = ROOT / "cards", ROOT / "fonts", ROOT / "data"
STATIC = os.environ.get("STATIC") == "1"

# ───────────────────────────── content ─────────────────────────────
USER = "BUAksakal"
GH = f"https://github.com/{USER}"
CONFIG = {
    "name": ("Berke Uğur", "Aksakal"),  # line 1 upright, line 2 italic with the blue shine
    "kicker": "GITHUB.COM/BUAKSAKAL",
    "about": "Electrical engineer turned AI builder: computer vision, robotics and embedded systems.",
    "status": "M.Sc. AI for Smart Sensors and Actuators, TH Deggendorf",
    # Shown on the right of the hero. Put a photo at assets/portrait.jpg to get an ASCII portrait instead.
    "terminal": [
        ("role", "M.Sc. AI, TH Deggendorf"),
        ("focus", "vision, robotics, embedded"),
        ("education", "B.Sc. EEE, Afyon Kocatepe"),
        ("location", "Bavaria, Germany"),
        ("contact", "berkeuguraksakal.com"),
    ],
    # The three tiles. "{repos}" is replaced by your live public repo count and counts up.
    "tiles": [
        {"key": "hardware", "label": "HARDWARE", "color": "blue",
         "head": ("B.Sc. ", "EEE"),
         "body": "Electrical and electronics engineer. Arduino robotic hand, Raspberry Pis over SPI, LiDAR.",
         "href": f"{GH}/ai-vision-robotic-hand"},
        {"key": "software", "label": "SOFTWARE", "color": "red",
         "head": ("{repos}", " repos"),
         "body": "Python, C++ and Docker. Desktop tools, Flutter apps and data pipelines.",
         "href": f"{GH}?tab=repositories"},
        {"key": "ai", "label": "AI", "color": "yellow",
         "head": ("M.Sc. ", "in AI"),
         "body": "YOLOv8 vision, LangGraph agents and RAG. Studying at TH Deggendorf.",
         "href": f"{GH}/low-altitude-aerial-detection"},
    ],
    # Featured repos, one row of three, in this order. "blurb" overrides the GitHub description.
    "repos": [
        {"name": "reqlens", "area": "AI", "color": "yellow",
         "blurb": "AI agents that review automotive requirement specs and flag conflicts and vague wording.",
         "tags": ["LangGraph", "LlamaIndex", "RAG"]},
        {"name": "low-altitude-aerial-detection", "area": "AI", "color": "yellow",
         "blurb": "YOLOv8 on custom-annotated drone footage, 3 to 9 m up. With Fraunhofer IVI.",
         "tags": ["YOLOv8", "PyTorch"]},
        {"name": "journey-resilience-agent", "area": "SOFTWARE", "color": "red",
         "blurb": "Live transfer-risk monitoring for German rail journeys on the DB Timetables API.",
         "tags": ["LangGraph", "LightGBM"]},
    ],
    "section_title": ("What I'm ", "building"),
}

# ───────────────────────────── design ─────────────────────────────
C = {
    "blue": "#3854F6", "red": "#DC4B3A", "yellow": "#F6CA4A",
    "paper": "#F1F0EC", "ink": "#141413", "muted": "#6E6D67", "line": "#DDDBD3",
    "empty": "#E2E0D8", "night": "#18181A",
}
ON = {"blue": "#FFFFFF", "red": "#FFFFFF", "yellow": C["ink"]}
BLUES = [C["empty"], "#C8D1FD", "#8E9FFA", "#5D76F8", C["blue"]]  # graph levels 0..4
W, PAD, R = 1200, 8, 22  # full-width viewBox, baked-in margin, corner radius
EASE = "cubic-bezier(.2,.75,.2,1)"

# Global timeline (seconds): each card starts where the one above it is finishing.
T = {"hero": 0.1, "graph": 1.0, "activity": 2.0, "tiles": 3.0, "title": 3.7, "repos": 3.9}

FACES = {  # face -> file. CSS names get an "x-" prefix: a family called plain "serif" is the generic keyword

    "serif": "serif-400.woff", "serifi": "serif-400i.woff",
    "sans": "sans-400.woff", "sansm": "sans-500.woff", "mono": "mono-400.woff",
}

KEYFRAMES = """
@keyframes up{from{opacity:0;transform:translateY(26px)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes show{from{opacity:0}to{opacity:1}}
@keyframes flash{0%,100%{opacity:1}}
@keyframes pop{0%{opacity:0;transform:scale(.3)}65%{opacity:1;transform:scale(1.18)}100%{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleY(0)}to{transform:none}}
@keyframes swing{0%{opacity:0;transform:translateY(150px) rotate(-8deg)}55%{opacity:1;transform:translateY(-12px) rotate(1.6deg)}78%{transform:translateY(4px) rotate(-.5deg)}100%{opacity:1;transform:none}}
@keyframes ring{0%{opacity:.65;transform:scale(1)}100%{opacity:0;transform:scale(4)}}
@keyframes nudge{0%,72%,100%{transform:none}82%{transform:translateX(6px)}91%{transform:translateX(-1px)}}
@keyframes sweep{0%{transform:translateX(-260px) skewX(-18deg)}22%,100%{transform:translateX(560px) skewX(-18deg)}}
@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
@keyframes dot{0%,100%{opacity:1}50%{opacity:.35}}
"""


# ───────────────────────────── helpers ─────────────────────────────
@lru_cache(maxsize=None)
def font(face: str) -> TTFont:
    return TTFont(FONTS / FACES[face])


def measure(text: str, face: str, size: float, ls: float = 0) -> float:
    f = font(face)
    cmap, hmtx, upm = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    adv = sum(hmtx[cmap.get(ord(ch), ".notdef")][0] for ch in text)
    return adv * size / upm + ls * max(len(text) - 1, 0)


def wrap(text: str, face: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial, face, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def a(name: str, dur: float, delay: float, ease: str = EASE, extra: str = "") -> str:
    """Inline animation style; empty in STATIC mode so the base (final) state shows."""
    if STATIC:
        return ""
    return f"animation:{name} {dur:.3f}s {ease} {delay:.3f}s {extra or 'both'};"


@lru_cache(maxsize=None)
def embedded_font(face: str, chars: frozenset) -> str:
    f = TTFont(FONTS / FACES[face], recalcTimestamp=False)  # deterministic output
    opts = subset.Options()
    opts.flavor = "woff"
    opts.layout_features = ["kern", "liga", "tnum"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(text="".join(sorted(chars)) + " ")
    sub.subset(f)
    buf = io.BytesIO()
    f.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


class Card:
    def __init__(self, name: str, w: int, h: int, label: str):
        self.name, self.w, self.h, self.label = name, w, h, label
        self.used: dict[str, set] = defaultdict(set)
        self.defs: list[str] = []
        self.body: list[str] = []
        self.css: list[str] = []

    def text(self, x, y, s, face="sans", size=18, fill=C["ink"], anchor="start", ls=0, style="", cls="", attrs=""):
        self.used[face].update(s)
        ls_attr = f' letter-spacing="{ls}"' if ls else ""
        cls_attr = f' class="{cls}"' if cls else ""
        st = f' style="{style}"' if style else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="x-{face}" font-size="{size}" fill="{fill}"'
                f' text-anchor="{anchor}" xml:space="preserve"{ls_attr}{cls_attr}{st}{attrs}>{esc(s)}</text>')

    def add(self, *parts: str):
        self.body.extend(parts)

    def svg(self) -> str:
        faces = "".join(
            f"@font-face{{font-family:'x-{face}';src:url(data:font/woff;base64,"
            f"{embedded_font(face, frozenset(chars))}) format('woff')}}"
            for face, chars in sorted(self.used.items()))
        base = ("text{font-synthesis:none;font-kerning:normal}"
                "*{transform-box:fill-box;transform-origin:center}")
        motion = "" if STATIC else (KEYFRAMES + "".join(self.css) +
                                    "@media (prefers-reduced-motion:reduce){*{animation:none!important}.m{display:none}}")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
                f"<title>{esc(self.label)}</title><style>{faces}{base}{motion}</style>"
                f"<defs>{''.join(self.defs)}</defs>{''.join(self.body)}</svg>")


def panel(w: int, h: int, fill: str = C["paper"]) -> str:
    return f'<rect x="{PAD}" y="{PAD}" width="{w - 2 * PAD}" height="{h - 2 * PAD}" rx="{R}" fill="{fill}"/>'


def arrow(x: float, y: float, color: str, delay: float, s: float = 1.0) -> str:
    """A small → drawn as a path (fonts vary), nudging every few seconds."""
    p = f"M{x - 9 * s:.1f} {y:.1f}H{x + 7 * s:.1f}M{x + 1 * s:.1f} {y - 6 * s:.1f}L{x + 7 * s:.1f} {y:.1f}L{x + 1 * s:.1f} {y + 6 * s:.1f}"
    return (f'<path d="{p}" fill="none" stroke="{color}" stroke-width="{2.2 * s:.1f}" stroke-linecap="round" '
            f'stroke-linejoin="round" style="{a("nudge", 3.2, delay, "ease-in-out", "infinite")}"/>')


def countup(card: Card, x, y, value: int, face, size, fill, start: float, frames: int = 18, step: float = 0.055) -> str:
    """Number that counts up from 0. CSS can't change text, so each value is its own element,
    flashed for one step; the final value is the resting state (and what reduced motion shows)."""
    card.used[face].update("0123456789")
    if STATIC or value <= 0:
        return card.text(x, y, str(value), face, size, fill, anchor="end")
    vals, out = [], []
    for k in range(frames):
        v = round(value * (1 - (1 - k / (frames - 1)) ** 3))
        if v != value and (not vals or v != vals[-1]):
            vals.append(v)
    for i, v in enumerate(vals):
        out.append(card.text(x, y, str(v), face, size, fill, anchor="end", cls="m",
                             style=f"opacity:0;animation:flash {step:.3f}s linear {start + i * step:.3f}s"))
    out.append(card.text(x, y, str(value), face, size, fill, anchor="end",
                         style=a("show", 0.001, start + len(vals) * step, "linear")))
    return "".join(out)


# ───────────────────────────── data ─────────────────────────────
def load_data():
    cal = json.loads((DATA / "contributions.json").read_text())
    days = [(dt.date.fromisoformat(d["date"]), int(d["count"])) for d in cal["days"]]
    days.sort()
    repos = json.loads((DATA / "repos.json").read_text())
    return days, repos


def stats(days):
    counts = {d: c for d, c in days}
    last = days[-1][0]
    # current streak: today may still be empty, so count back from yesterday in that case
    cur, d = 0, last if counts[last] else last - dt.timedelta(days=1)
    while counts.get(d, 0) > 0:
        cur += 1
        d -= dt.timedelta(days=1)
    longest = run = 0
    for _, c in days:
        run = run + 1 if c else 0
        longest = max(longest, run)
    busiest = max(days, key=lambda x: (x[1], x[0]))
    months = []  # last 12 calendar months, oldest first
    y, m = last.year, last.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    months.reverse()
    per_month = {ym: 0 for ym in months}
    for d, c in days:
        if (d.year, d.month) in per_month:
            per_month[(d.year, d.month)] += c
    return {
        "total": sum(c for _, c in days), "active": sum(1 for _, c in days if c),
        "current": cur, "longest": longest, "busiest": busiest,
        "months": [(ym, per_month[ym]) for ym in months], "first": days[0][0], "last": last,
    }


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ───────────────────────────── cards ─────────────────────────────
def hero(st) -> Card:
    h = 456
    first, second = CONFIG["name"]
    card = Card("hero", W, h, f"{first} {second}. {CONFIG['about']}")
    t0 = T["hero"]
    card.add(f'<g style="{a("fade", 0.5, t0)}">{panel(W, h)}</g>')
    x = 56
    card.add(card.text(x, 74, CONFIG["kicker"], "mono", 15, C["muted"], ls=2.2, style=a("up", 0.6, t0 + 0.1)))

    # Name: fit both lines into the left column.
    size = 132
    while max(measure(first, "serif", size), measure(second, "serifi", size)) > 600:
        size -= 2
    card.add(card.text(x - 4, 190, first, "serif", size, C["ink"], style=a("up", 0.8, t0 + 0.2)))
    shine = ""
    if not STATIC:
        sw = measure(second, "serifi", size)
        card.defs.append(
            f'<linearGradient id="shine" gradientUnits="userSpaceOnUse" x1="{x}" y1="0" x2="{x + 160}" y2="0">'
            f'<stop offset="0" stop-color="{C["blue"]}" stop-opacity="0"/>'
            f'<stop offset=".5" stop-color="{C["blue"]}"/>'
            f'<stop offset="1" stop-color="{C["blue"]}" stop-opacity="0"/>'
            f'<animateTransform attributeName="gradientTransform" type="translate" '
            f'values="-220 0;{sw + 80:.0f} 0;{sw + 80:.0f} 0" keyTimes="0;.32;1" dur="5.5s" '
            f'begin="{t0 + 1.6:.1f}s" repeatCount="indefinite"/></linearGradient>')
        shine = card.text(x - 4, 296, second, "serifi", size, "url(#shine)", cls="m")
    card.add(f'<g style="{a("up", 0.8, t0 + 0.42)}">'
             + card.text(x - 4, 296, second, "serifi", size, C["ink"]) + shine + "</g>")

    for i, line in enumerate(wrap(CONFIG["about"], "sans", 23, 600)[:2]):
        card.add(card.text(x, 344 + i * 31, line, "sans", 23, C["ink"], style=a("up", 0.7, t0 + 0.75 + i * 0.08)))
    sy = 410
    card.add(f'<g style="{a("fade", 0.6, t0 + 1.0)}">'
             f'<circle cx="{x + 6}" cy="{sy - 6}" r="5.5" fill="{C["blue"]}" style="{a("dot", 2.4, t0 + 1.6, "ease-in-out", "infinite")}"/>'
             + ("" if STATIC else
                f'<circle class="m" cx="{x + 6}" cy="{sy - 6}" r="5.5" fill="{C["blue"]}" '
                f'style="animation:ring 2.4s ease-out {t0 + 1.6:.1f}s infinite;opacity:0"/>')
             + card.text(x + 22, sy, CONFIG["status"], "sans", 18, C["muted"]) + "</g>")

    # Right: ASCII portrait if there's a photo, otherwise the identity block from the old README.
    px, py, pw, ph = 700, 48, 452, 360
    portrait = next((p for p in (ROOT / "assets").glob("portrait.*")), None) if (ROOT / "assets").exists() else None
    if portrait:
        card.add(ascii_portrait(card, portrait, 728, 72, 374, 312, t0 + 0.5))  # x, y, width, height
    else:
        card.add(f'<g style="{a("up", 0.8, t0 + 0.55)}"><rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="16" fill="{C["night"]}"/></g>')
        ty = py + 54
        lx = px + 30
        card.add(card.text(lx, ty, "$ cat about.ini", "mono", 16, "#8E9FFA", style=a("fade", 0.3, t0 + 0.9)))
        keyw = max(len(k) for k, _ in CONFIG["terminal"])
        for i, (k, v) in enumerate(CONFIG["terminal"]):
            yy = ty + 48 + i * 42
            d = t0 + 1.05 + i * 0.14
            card.add(f'<g style="{a("up", 0.45, d)}">'
                     + card.text(lx, yy, k.ljust(keyw), "mono", 16, C["yellow"])
                     + card.text(lx + measure(" " * (keyw + 1), "mono", 16), yy, "=", "mono", 16, "#77766F")
                     + card.text(lx + measure(" " * (keyw + 3), "mono", 16), yy, v, "mono", 16, "#F1F0EC")
                     + "</g>")
        cy = ty + 48 + len(CONFIG["terminal"]) * 42
        card.add(card.text(lx, cy, "$", "mono", 16, "#8E9FFA", style=a("fade", 0.3, t0 + 1.9)))
        card.add(f'<rect x="{lx + 18}" y="{cy - 14}" width="10" height="18" fill="#F1F0EC" '
                 f'style="{a("blink", 1.05, t0 + 1.9, "steps(1)", "infinite")}"/>')
    return card


def ascii_portrait(card: Card, path, px, py, pw, ph, start) -> str:
    """Ink-on-paper ASCII portrait that prints in row by row. Works best with a photo on a
    plain, light background: anything near white turns into empty space."""
    from PIL import Image, ImageFilter, ImageOps  # only needed when a portrait exists

    ramp = "@%#*+=-:. "  # dark → light
    rows = round(ph / 4.3)  # ~4.3 units per row keeps the dense look at any size
    lh = ph / rows
    size = round(lh / (measure("M", "mono", 1)), 2)  # square-ish character cells
    cw = measure("M", "mono", size)
    cols = int(pw / cw)
    img = ImageOps.grayscale(ImageOps.exif_transpose(Image.open(path)))
    # crop to the area's real proportions, then sample one pixel per character
    aspect = (cols * cw) / (rows * lh)
    iw, ih = img.size
    if iw / ih > aspect:
        nw = int(ih * aspect)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / aspect)
        top = int((ih - nh) * 0.3)
        img = img.crop((0, top, iw, top + nh))
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=120))
    img = ImageOps.autocontrast(img.resize((cols, rows), Image.LANCZOS), cutoff=1)
    ox = px + (pw - cols * cw) / 2
    out = []
    for r in range(rows):
        # len(ramp) buckets, so the lightest ~10% (the background) becomes a space
        line = "".join(ramp[img.getpixel((c, r)) * len(ramp) // 256] for c in range(cols)).rstrip()
        if line:
            out.append(card.text(ox, py + (r + 1) * lh, line, "mono", size, C["ink"],
                                 style=a("fade", 0.2, start + r * 0.018)))
    return "".join(out)


def graph(days, st) -> Card:
    h = 330
    card = Card("heatmap", W, h, f"{st['total']} contributions in the last year")
    t0 = T["graph"]
    card.add(f'<g style="{a("fade", 0.5, t0)}">{panel(W, h)}</g>')
    x0 = 70
    head = f"{st['total']:,} contributions"
    card.add(f'<g style="{a("up", 0.7, t0 + 0.1)}">'
             + card.text(x0 - 2, 90, head, "serif", 44, C["ink"])
             + card.text(x0 - 2 + measure(head + " ", "serif", 44), 90, "in the last year", "serifi", 44, C["ink"])
             + "</g>")
    rng = f"{st['first']:%b %Y} — {st['last']:%b %Y}".upper()
    card.add(card.text(W - 70, 88, rng, "mono", 14, C["muted"], anchor="end", ls=1.8, style=a("fade", 0.6, t0 + 0.3)))

    nz = sorted(c for _, c in days if c)
    q = [nz[int(len(nz) * p)] for p in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]

    def level(c):
        return 0 if c == 0 else 1 + sum(c > t for t in q)

    pitch, cell, gy = 20, 16, 140
    offset = (days[0][0].weekday() + 1) % 7  # GitHub weeks start on Sunday
    seen = set()
    cells = []
    for i, (d, c) in enumerate(days):
        col, row = divmod(i + offset, 7)
        x, y = x0 + col * pitch, gy + row * pitch
        delay = t0 + 0.35 + col * 0.022 + row * 0.006
        cells.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3.5" fill="{BLUES[level(c)]}" '
                     f'style="{a("pop", 0.42, delay)}"/>')
        if d.day <= 7 and d.weekday() == 6 and (d.year, d.month) not in seen and col < 52:
            seen.add((d.year, d.month))
            card.add(card.text(x, gy - 12, f"{d:%b}", "mono", 13, C["muted"], style=a("fade", 0.4, delay)))
    card.add("".join(cells))
    lx = W - 70 - 5 * 20 - 46
    ly = 304
    card.add(f'<g style="{a("fade", 0.5, t0 + 1.6)}">'
             + card.text(lx - 10, ly, "Less", "mono", 13, C["muted"], anchor="end")
             + "".join(f'<rect x="{lx + i * 20}" y="{ly - 12}" width="14" height="14" rx="3" fill="{BLUES[i]}"/>' for i in range(5))
             + card.text(lx + 5 * 20 + 4, ly, "More", "mono", 13, C["muted"]) + "</g>")
    card.add(card.text(x0, ly, f"refreshed {st['last']:%d %b %Y}", "mono", 13, C["muted"], style=a("fade", 0.5, t0 + 1.6)))
    return card


def activity(st) -> Card:
    h = 380
    card = Card("activity", W, h, "Contributions per month, with streaks and busiest day")
    t0 = T["activity"]
    card.add(f'<g style="{a("fade", 0.5, t0)}">{panel(W, h)}</g>')
    x0 = 56
    months = st["months"]
    best = max(range(12), key=lambda i: (months[i][1], i))
    best_name = calendar.month_name[months[best][0][1]]
    card.add(card.text(x0, 70, "CONTRIBUTIONS PER MONTH", "mono", 14, C["muted"], ls=2, style=a("up", 0.6, t0 + 0.1)))
    card.add(f'<g style="{a("up", 0.7, t0 + 0.18)}">'
             + card.text(x0 - 2, 118, f"{best_name} was the ", "serif", 40, C["ink"])
             + card.text(x0 - 2 + measure(f"{best_name} was the ", "serif", 40), 118, "busiest", "serifi", 40, C["ink"])
             + "</g>")
    base, top, area = 318, 168, 700
    slot = area / 12
    bw = 34
    peak = max(c for _, c in months) or 1
    card.add(f'<line x1="{x0}" y1="{base + 0.5}" x2="{x0 + area}" y2="{base + 0.5}" stroke="{C["line"]}" stroke-width="1"/>')
    for i, ((y, m), c) in enumerate(months):
        bx = x0 + i * slot + (slot - bw) / 2
        bh = max((base - top) * c / peak, 3 if c else 0)
        fill = C["blue"] if i == best else C["ink"]
        d = t0 + 0.35 + i * 0.06
        if bh:
            card.add(f'<rect x="{bx:.1f}" y="{base - bh:.1f}" width="{bw}" height="{bh:.1f}" rx="5" fill="{fill}" '
                     f'style="transform-origin:bottom;{a("grow", 0.7, d)}"/>')
        else:
            card.add(f'<rect x="{bx:.1f}" y="{base - 2}" width="{bw}" height="2" rx="1" fill="{C["line"]}"/>')
        card.add(card.text(bx + bw / 2, base + 28, calendar.month_abbr[m], "mono", 14,
                           C["blue"] if i == best else C["muted"], anchor="middle", style=a("fade", 0.4, d)))
        if i == best:
            card.add(card.text(bx + bw / 2, base - bh - 12, str(c), "serif", 30, C["blue"], anchor="middle",
                               style=a("pop", 0.5, t0 + 1.25)))
    # stats, 2 x 2
    card.add(f'<line x1="800.5" y1="56" x2="800.5" y2="{h - 56}" stroke="{C["line"]}" stroke-width="1"/>')
    bd, bc = st["busiest"]
    items = [
        (str(st["current"]), " days" if st["current"] != 1 else " day", "CURRENT STREAK"),
        (str(st["longest"]), " days" if st["longest"] != 1 else " day", "LONGEST STREAK"),
        (str(st["active"]), " days", "ACTIVE DAYS"),
        (str(bc), f" on {bd:%b} {bd.day}", "BUSIEST DAY"),
    ]
    for i, (num, unit, lab) in enumerate(items):
        cx, cy = 840 + (i % 2) * 170, 150 + (i // 2) * 130
        d = t0 + 0.5 + i * 0.1
        card.add(f'<g style="{a("up", 0.6, d)}">'
                 + card.text(cx, cy, num, "serif", 54, C["ink"])
                 + card.text(cx + measure(num, "serif", 54) + 2, cy, unit, "serifi", 24, C["muted"])
                 + card.text(cx, cy + 32, lab, "mono", 12.5, C["muted"], ls=1.6)
                 + "</g>")
    return card


def tile(cfg, i, repos) -> Card:
    w, h = 400, 300
    color, on = C[cfg["color"]], ON[cfg["color"]]
    head_a, head_b = cfg["head"]
    n = repos.get("public_repos", 0)
    label_text = f"{cfg['label']}: {head_a.replace('{repos}', str(n))}{head_b}"
    card = Card(cfg["key"], w, h, label_text)
    t0 = T["tiles"] + i * 0.16
    card.defs.append(f'<clipPath id="clip"><rect x="{PAD}" y="{PAD}" width="{w - 2 * PAD}" height="{h - 2 * PAD}" rx="{R}"/></clipPath>')
    inner = [panel(w, h, color)]
    if not STATIC:
        inner.append(f'<rect class="m" x="0" y="-40" width="70" height="{h + 80}" fill="#FFFFFF" opacity=".16" '
                     f'style="transform:translateX(-260px);animation:sweep 6s ease-in-out {t0 + 1.4:.2f}s infinite"/>')
    inner.append(card.text(40, 66, cfg["label"], "mono", 16, on, ls=2.4))
    inner.append(arrow(352, 60, on, t0 + 1.2))
    if not STATIC:
        inner.append(f'<circle class="m" cx="352" cy="60" r="12" fill="none" stroke="{on}" stroke-width="1.5" '
                     f'style="opacity:0;animation:ring 1.4s ease-out {t0 + 0.75:.2f}s 2"/>')
    size = 62
    hy = 168
    if "{repos}" in head_a:
        numw = measure(str(n), "serif", size)
        inner.append(countup(card, 38 + numw, hy, n, "serif", size, on, t0 + 0.45))
        inner.append(card.text(38 + numw, hy, head_b, "serifi", size, on))
    else:
        while measure(head_a, "serif", size) + measure(head_b, "serifi", size) > 330:
            size -= 2
        inner.append(card.text(38, hy, head_a, "serif", size, on))
        inner.append(card.text(38 + measure(head_a, "serif", size), hy, head_b, "serifi", size, on))
    for j, line in enumerate(wrap(cfg["body"], "sans", 19, 322)[:3]):
        inner.append(card.text(40, 214 + j * 27, line, "sans", 19, on))
    card.add(f'<g clip-path="url(#clip)"><g style="transform-origin:50% 100%;{a("swing", 1.05, t0, "cubic-bezier(.3,.7,.3,1)")}">'
             + "".join(inner) + "</g></g>")
    return card


def section_title(dark: bool) -> Card:
    h = 104
    a_, b_ = CONFIG["section_title"]
    card = Card(f"title-{'dark' if dark else 'light'}", W, h, a_ + b_)
    fill = "#F1F0EC" if dark else C["ink"]
    card.add(f'<g style="{a("up", 0.7, T["title"])}">'
             + card.text(48, 76, a_, "serif", 46, fill)
             + card.text(48 + measure(a_, "serif", 46), 76, b_, "serifi", 46, fill) + "</g>")
    return card


def name_lines(name: str, size: float, width: float) -> list[str]:
    """Split a long repo name after hyphens so it fits on at most two lines."""
    if measure(name, "serif", size) <= width:
        return [name]
    parts = name.split("-")
    best = None
    for k in range(1, len(parts)):
        a_, b_ = "-".join(parts[:k]) + "-", "-".join(parts[k:])
        worst = max(measure(a_, "serif", size), measure(b_, "serif", size))
        if best is None or worst < best[0]:
            best = (worst, [a_, b_])
    return best[1]


def repo_card(cfg, i, repos) -> Card:
    w, h = 400, 300  # same size as the tiles, three to a row
    info = repos.get("repos", {}).get(cfg["name"], {})
    blurb = cfg.get("blurb") or info.get("description", "")
    card = Card(f"repo-{cfg['name']}", w, h, f"{cfg['name']}: {blurb}")
    t0 = T["repos"] + i * 0.1
    accent = C[cfg["color"]]
    inner = [panel(w, h)]
    lang = info.get("language") or ""
    label = f"{cfg['area']} · {lang.upper()}" if lang else cfg["area"]
    inner.append(f'<circle cx="42" cy="53" r="5.5" fill="{accent}"/>')
    inner.append(card.text(56, 58, label, "mono", 13, C["muted"], ls=1.6))
    inner.append(arrow(354, 53, C["ink"], t0 + 1.0, 0.9))
    size, maxw = 36, 326
    lines = name_lines(cfg["name"], size, maxw)
    while max(measure(l, "serif", size) for l in lines) > maxw:
        size -= 1
        lines = name_lines(cfg["name"], size, maxw)
    y = 106
    for l in lines:
        inner.append(card.text(36, y, l, "serif", size, C["ink"]))
        y += size * 0.98
    body = wrap(blurb, "sans", 16.5, maxw)
    room = 3 if len(lines) == 1 else 2
    if len(body) > room:
        body = body[:room]
        body[-1] = body[-1].rstrip(".,;:") + "…"
    by = y + 14
    for j, line in enumerate(body):
        inner.append(card.text(38, by + j * 23, line, "sans", 16.5, C["muted"]))
    tags = list(cfg.get("tags", []))
    if info.get("stars"):
        tags.append(plural(info["stars"], "star"))
    tx = 38
    for tg in tags:
        tw = measure(tg, "mono", 12) + 22
        if tx + tw > w - 36:
            break
        inner.append(f'<rect x="{tx}" y="244" width="{tw:.1f}" height="26" rx="13" fill="none" stroke="{C["line"]}" stroke-width="1.2"/>'
                     + card.text(tx + 11, 261.5, tg, "mono", 12, C["ink"]))
        tx += tw + 7
    card.add(f'<g style="{a("up", 0.75, t0)}">' + "".join(inner) + "</g>")
    return card


# ───────────────────────────── main ─────────────────────────────
def main():
    days, repos = load_data()
    st = stats(days)
    cards = [hero(st), graph(days, st), activity(st)]
    cards += [tile(t, i, repos) for i, t in enumerate(CONFIG["tiles"])]
    cards += [section_title(False), section_title(True)]
    cards += [repo_card(r, i, repos) for i, r in enumerate(CONFIG["repos"])]
    CARDS.mkdir(exist_ok=True)
    versions = {}
    for card in cards:
        out = CARDS / f"{card.name}.svg"
        svg = card.svg()
        out.write_text(svg)
        versions[card.name] = hashlib.sha1(svg.encode()).hexdigest()[:8]
        print(f"cards/{out.name:42s} {out.stat().st_size / 1024:6.1f} KB")
    # Cache-busting: GitHub and browsers cache images by URL, so give every changed card a new
    # ?v=<hash> in the README. Unchanged cards keep their URL and stay cached.
    readme = ROOT / "README.md"
    if readme.exists():
        text = readme.read_text()
        new = re.sub(r"\./cards/([\w-]+)\.svg(?:\?v=[0-9a-f]+)?",
                     lambda m: f"./cards/{m[1]}.svg?v={versions[m[1]]}" if m[1] in versions else m[0], text)
        if new != text:
            readme.write_text(new)
            print("README.md card versions updated")
    if os.environ.get("PREVIEW") == "1":
        readme = (ROOT / "README.md").read_text()
        (ROOT / "preview.html").write_text(
            "<!doctype html><meta charset=utf-8><title>README preview</title>"
            "<style>body{margin:0;background:#fff;font:16px -apple-system,Segoe UI,sans-serif}"
            "main{max-width:830px;margin:32px auto;padding:0 16px}img{max-width:100%;vertical-align:top}"
            "a{color:#0969da}p{text-align:center}</style><main>" + readme + "</main>")
        print("preview.html")


if __name__ == "__main__":
    main()
