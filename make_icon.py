"""
Generate the CiteFinder app icon (citefinder.ico) — the Windows taskbar / window
/ installer icon used by desktop.py and CiteFinder.spec.

The mark is a bold quotation mark — the universal symbol for a citation — in the
app's dark accent-foreground (#06231a) on the emerald accent tile (#46c89a),
matching the in-app primary-glyph styling. Pillow is a BUILD-TIME tool only (not a
runtime dependency); re-run this if the brand colours change:

    venv\\Scripts\\python.exe make_icon.py

To flip the mark's orientation, change GLYPH_CHAR ("“" opening / "”" closing).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (70, 200, 154, 255)     # --accent  #46c89a
GLYPH = (6, 35, 26, 255)         # the dark used on accent buttons (#06231a)
R = 1024                         # render big, downsample crisp
GLYPH_CHAR = "“"            # “ left double quotation mark (the citation mark)

# A bold serif renders the classic quotation-mark shape; fall back across the
# fonts Windows ships until one loads.
_FONTS = ["georgiab.ttf", "timesbd.ttf", " arialbd.ttf".strip(), "seguisb.ttf",
          "segoeuib.ttf", "arialbd.ttf"]


def _load_font(px):
    for name in _FONTS:
        for base in (Path("C:/Windows/Fonts"), Path(name)):
            try:
                return ImageFont.truetype(str(base / name) if base.is_dir() else name, px)
            except Exception:
                continue
    return None


img = Image.new("RGBA", (R, R), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded-square emerald tile
d.rounded_rectangle([0, 0, R - 1, R - 1], radius=int(R * 0.22), fill=ACCENT)

font = _load_font(int(R * 0.95))
if font is None:
    print("No bold serif font found; cannot render the quotation mark.", file=sys.stderr)
    sys.exit(1)

# Centre the glyph's actual ink (quote marks sit high in the em box, so centring
# the bounding box, not the baseline, keeps it visually centred on the tile).
l, t, r, b = d.textbbox((0, 0), GLYPH_CHAR, font=font)
x = (R - (r - l)) / 2 - l
y = (R - (b - t)) / 2 - t
d.text((x, y), GLYPH_CHAR, font=font, fill=GLYPH)

sizes = [16, 24, 32, 48, 64, 128, 256]
# Save the 256px master and let Pillow embed every listed size (downsampled).
img.resize((256, 256), Image.LANCZOS).save(
    "citefinder.ico", format="ICO", sizes=[(s, s) for s in sizes])
print("wrote citefinder.ico", sizes)
