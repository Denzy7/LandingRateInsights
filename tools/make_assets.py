"""Draws Landing Rate Insights' app icon with Pillow: a PAPI on the glide path (two white, two red).

    python tools/make_assets.py

Writes landingrate/assets/icon_*.png and landingrate.ico. Shapes are drawn at 4x and scaled
down, so they stay crisp. Run again after changing the brand colours (and commit the output).
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "landingrate", "assets")

# blue from the dashboard's chart palette, deepened for the gradient
BLUE_LIGHT = (57, 135, 229)
BLUE_DARK = (14, 52, 110)
WHITE = (255, 255, 255)
RUNWAY = (232, 238, 247)
GROUND = (10, 36, 78)
PAPI_WHITE = (255, 250, 235)
PAPI_RED = (235, 52, 52)
HOUSING = (8, 24, 54)


def _rounded(S):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    return m


def _paste_clipped(img, layer, mask):
    img.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), Image.new("L", img.size, 0), mask))


def app_icon(size=512):
    """Dusk sky, a runway in perspective and a PAPI showing two white, two red: on the glide path."""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = _rounded(S)
    # sky gradient, light at the top
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=tuple(int(BLUE_LIGHT[i] * (1 - t) + BLUE_DARK[i] * t) for i in range(3)) + (255,))
    img.paste(grad, (0, 0), mask)

    # ground and runway in perspective
    horizon = S * 0.60
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gdr = ImageDraw.Draw(g)
    gdr.rectangle([0, horizon, S, S], fill=GROUND + (255,))
    gdr.polygon([(S * 0.18, S), (S * 0.82, S), (S * 0.535, horizon), (S * 0.465, horizon)], fill=RUNWAY + (255,))
    y, h = S * 0.985, S * 0.09                       # centreline dashes shrinking into the distance
    while y - h > horizon + S * 0.015:
        t = (S - y) / (S - horizon)
        w = S * 0.026 * (1 - t * 0.75)
        gdr.rectangle([S / 2 - w / 2, y - h, S / 2 + w / 2, y], fill=GROUND + (255,))
        y -= h * 1.8
        h *= 0.68
    _paste_clipped(img, g, mask)

    # PAPI: housing bar with four lights, glow drawn first on its own layer
    cy, r, gap = S * 0.36, S * 0.08, S * 0.195
    xs = [S / 2 + (k - 1.5) * gap for k in range(4)]
    colours = [PAPI_WHITE, PAPI_WHITE, PAPI_RED, PAPI_RED]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gw = ImageDraw.Draw(glow)
    for x, c in zip(xs, colours):
        for k in range(10, 0, -1):                   # soft halo, fading outward
            rr = r * (1 + k * 0.06)
            gw.ellipse([x - rr, cy - rr, x + rr, cy + rr], fill=c + (int(8 + (10 - k) * 4),))
    _paste_clipped(img, glow, mask)
    d = ImageDraw.Draw(img)
    pad = S * 0.035
    d.rounded_rectangle([xs[0] - r - pad, cy - r - pad, xs[-1] + r + pad, cy + r + pad],
                        radius=int(r + pad), fill=HOUSING + (255,))
    for x, c in zip(xs, colours):
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=c + (255,))
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    big = app_icon(512)
    big.save(os.path.join(OUT, "icon_512.png"))
    for s in (256, 128, 64, 48, 32, 16):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(OUT, "icon_%d.png" % s))
    big.save(os.path.join(OUT, "landingrate.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                            (128, 128), (256, 256)])
    print("assets written to", OUT)


if __name__ == "__main__":
    main()
