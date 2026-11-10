"""One-time font setup: download, pin weights and subset the fonts the cards use.

Run once (or whenever you want more glyphs):  python scripts/build_fonts.py
Writes small WOFF files into fonts/, which render_readme_cards.py embeds as base64.
All three families are SIL Open Font License 1.1 (licence files are saved next to them).
"""
import io
import pathlib
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts"
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl"

# Printable ASCII, Latin-1, Turkish letters, dashes, quotes, bullets and arrows.
TEXT_RANGES = [(0x20, 0x7E), (0xA0, 0xFF)]
EXTRA = "ğĞıİşŞ–—‘’“”•…·→←↑↓↗×"

FONTS = {
    # out name: (url, {axis: value} for variable fonts, or None)
    "serif-400": (f"{GF}/instrumentserif/InstrumentSerif-Regular.ttf", None),
    "serif-400i": (f"{GF}/instrumentserif/InstrumentSerif-Italic.ttf", None),
    "sans-400": (f"{GF}/geist/Geist%5Bwght%5D.ttf", {"wght": 400}),
    "sans-500": (f"{GF}/geist/Geist%5Bwght%5D.ttf", {"wght": 500}),
    "mono-400": (f"{GF}/geistmono/GeistMono%5Bwght%5D.ttf", {"wght": 400}),
}
LICENCES = {
    "OFL-InstrumentSerif.txt": f"{GF}/instrumentserif/OFL.txt",
    "OFL-Geist.txt": f"{GF}/geist/OFL.txt",
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    unicodes = [c for a, b in TEXT_RANGES for c in range(a, b + 1)] + [ord(c) for c in EXTRA]
    cache: dict[str, bytes] = {}
    for name, (url, axes) in FONTS.items():
        raw = cache.setdefault(url, fetch(url))
        font = TTFont(io.BytesIO(raw))
        if axes:
            font = instancer.instantiateVariableFont(font, axes)
        # Google has served the italic under the regular's name before: check, don't trust filenames.
        style = font["name"].getDebugName(2) or ""
        want_italic = name.endswith("i")
        if want_italic != ("Italic" in style or font["post"].italicAngle != 0):
            raise SystemExit(f"{name}: got style '{style}' — wrong file?")
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = ["kern", "liga", "tnum", "lnum"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=unicodes)
        sub.subset(font)
        font.flavor = "woff"
        path = OUT / f"{name}.woff"
        font.save(path)
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} KB  ({style})")
    for fname, url in LICENCES.items():
        (OUT / fname).write_bytes(fetch(url))


if __name__ == "__main__":
    main()
