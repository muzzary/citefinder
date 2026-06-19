"""
Generate the README banner (docs/banner.png) - the CiteFinder wordmark next to
the quotation-mark glyph on the app's near-black background with the emerald
accent. Build-time tool only (Pillow); re-run if the brand changes:

    venv\\Scripts\\python.exe make_banner.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (11, 11, 13, 255)         # --bg        #0b0b0d
ACCENT = (70, 200, 154, 255)   # --accent    #46c89a
DARK = (6, 35, 26, 255)        # accent foreground #06231a
TEXT = (237, 237, 240, 255)    # near-white wordmark
MUTED = (138, 140, 146, 255)   # tagline

W, H = 1380, 360
FONTS_BOLD = ["segoeuib.ttf", "arialbd.ttf", "timesbd.ttf"]
FONTS_REG = ["segoeui.ttf", "arial.ttf", "georgia.ttf"]
SERIF_BOLD = ["georgiab.ttf", "timesbd.ttf", "arialbd.ttf"]


def _font(names, px):
    for name in names:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), px)
        except Exception:
            continue
    return None


img = Image.new("RGBA", (W, H), BG)
d = ImageDraw.Draw(img)

# emerald tile with the quotation-mark glyph (matches the app icon)
tile = 200
tx, ty = 90, (H - tile) // 2
d.rounded_rectangle([tx, ty, tx + tile, ty + tile], radius=int(tile * 0.22), fill=ACCENT)
qfont = _font(SERIF_BOLD, int(tile * 0.95))
if qfont:
    l, t, r, b = d.textbbox((0, 0), "“", font=qfont)
    qx = tx + (tile - (r - l)) / 2 - l
    qy = ty + (tile - (b - t)) / 2 - t
    d.text((qx, qy), "“", font=qfont, fill=DARK)

# wordmark + tagline
wx = tx + tile + 56
word = _font(FONTS_BOLD, 132)
tag = _font(FONTS_REG, 33)
if word is None or tag is None:
    print("Required Windows fonts not found.", file=sys.stderr)
    sys.exit(1)

wl, wt, wr, wb = d.textbbox((0, 0), "CiteFinder", font=word)
word_h = wb - wt
tag_h = 48
gap = 18
block_h = word_h + gap + tag_h
top = (H - block_h) / 2
d.text((wx, top - wt), "CiteFinder", font=word, fill=TEXT)
d.text((wx + 4, top + word_h + gap),
       "Answers only from your own PDFs, with honest citations.",
       font=tag, fill=MUTED)

# a thin emerald underline accent beneath the wordmark
uy = int(top + word_h + 6)
d.rectangle([wx + 4, uy, wx + 4 + 360, uy + 4], fill=ACCENT)

Path("docs").mkdir(exist_ok=True)
img.convert("RGB").save("docs/banner.png")
print("wrote docs/banner.png", (W, H))
