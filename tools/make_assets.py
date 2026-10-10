"""Draws Landing Rate Insights' app icon with Pillow.

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
ACCENT = (250, 178, 25)   # amber touchdown point (the dashboard's "warning" token)


def app_icon(size=512):
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # rounded square with a vertical gradient: sky at the top, darker toward the ground
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=tuple(int(BLUE_LIGHT[i] * (1 - t) + BLUE_DARK[i] * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # side view: ground strip with a runway along it
    d = ImageDraw.Draw(img)
    rim = int(S * 0.22)
    ground_top = S * 0.70
    gl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gld = ImageDraw.Draw(gl)
    gld.rectangle([0, ground_top, S, S], fill=GROUND + (255,))
    rw_top, rw_bot = ground_top + S * 0.035, ground_top + S * 0.105
    gld.rectangle([S * 0.30, rw_top, S, rw_bot], fill=RUNWAY + (255,))
    x = S * 0.36
    while x < S:                       # centreline dashes
        gld.rectangle([x, (rw_top + rw_bot) / 2 - S * 0.008, x + S * 0.07, (rw_top + rw_bot) / 2 + S * 0.008],
                      fill=GROUND + (255,))
        x += S * 0.12
    m2 = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m2).rounded_rectangle([0, 0, S - 1, S - 1], radius=rim, fill=255)
    img.paste(gl, (0, 0), Image.composite(gl.getchannel("A"), Image.new("L", (S, S), 0), m2))
    d = ImageDraw.Draw(img)

    # flight path: steady approach that rounds out (the flare) and touches down on the runway
    x0, y0 = S * 0.13, S * 0.17
    tx, ty = S * 0.62, rw_top
    c1, c2 = (S * 0.36, S * 0.47), (S * 0.44, rw_top)
    pts = []
    for k in range(1201):
        u = k / 1200
        x = (1 - u) ** 3 * x0 + 3 * (1 - u) ** 2 * u * c1[0] + 3 * (1 - u) * u ** 2 * c2[0] + u ** 3 * tx
        y = (1 - u) ** 3 * y0 + 3 * (1 - u) ** 2 * u * c1[1] + 3 * (1 - u) * u ** 2 * c2[1] + u ** 3 * ty
        pts.append((x, y))
    w = int(S * 0.05)
    r0 = w / 2
    for x, y in pts:  # a chain of round dabs gives a smoother stroke than line() joints
        d.ellipse([x - r0, y - r0, x + r0, y + r0], fill=WHITE + (255,))
    r = S * 0.062
    d.ellipse([tx - r * 1.3, ty - r * 1.3, tx + r * 1.3, ty + r * 1.3], fill=BLUE_DARK + (255,))
    d.ellipse([tx - r, ty - r, tx + r, ty + r], fill=ACCENT + (255,))
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
