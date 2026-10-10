"""Draws Landing Rate Insights' app icon with Pillow.

    python tools/make_assets.py

A night approach in IMC: cloud and mist, precision approach lights leading to a marked runway,
a PAPI each side showing on-glide-path (two white, two red) and BUTTER across the top.
Sizes of 48 px and up get the full scene; 16 and 32 px get a simplified one (runway, approach
lights, PAPIs) that stays readable. Writes landingrate/assets/icon_*.png and landingrate.ico.
Shapes are drawn at 4x and scaled down. Needs a bold font for the text (FONTS below).
Run again after changing anything here and commit the output.
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "landingrate", "assets")

SKY_TOP = (22, 30, 52)
SKY_BOTTOM = (6, 9, 18)
GROUND = (5, 7, 12)
ASPHALT = (28, 31, 38)
PAINT = (226, 230, 236)
LIGHT_WHITE = (255, 247, 226)
LIGHT_RED = (255, 60, 52)
LIGHT_GREEN = (70, 235, 120)
MIST = (120, 132, 158)
BUTTER = (255, 214, 79)
FONTS = ["/usr/share/fonts/noto/NotoSans-Black.ttf", "/usr/share/fonts/noto/NotoSans-Bold.ttf",
         "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf"]


class View:
    """Pinhole perspective for a runway seen from the approach.

    Runway coordinates: u across (-1 = left edge, +1 = right edge), z = distance from the eye
    (z = 1 is the bottom of the icon). The threshold is at z_thr, the far end at z_end.
    """

    def __init__(self, S, horizon, bottom, half_width, z_thr, z_end):
        self.S, self.h, self.z_thr, self.z_end = S, horizon, z_thr, z_end
        self.k = bottom - horizon
        self.w = half_width

    def y(self, z):
        return self.h + self.k / z

    def x(self, u, z):
        return self.S / 2 + u * self.w / z

    def pt(self, u, z):
        return self.x(u, z), self.y(z)

    def quad(self, u0, u1, z0, z1):
        return [self.pt(u0, z0), self.pt(u1, z0), self.pt(u1, z1), self.pt(u0, z1)]

    def size(self, base, z):
        return base / z


def _rounded_mask(S, radius=0.22):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * radius), fill=255)
    return m


def _gradient(S, top, bottom, y0=0.0, y1=1.0):
    g = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(g)
    for y in range(S):
        t = min(1.0, max(0.0, (y / S - y0) / (y1 - y0)))
        d.line([(0, y), (S, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,))
    return g


def _fade(layer, k):
    r, g, b, a = layer.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda p: int(p * k))))


def _font(px):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, px)
    return ImageFont.load_default(px)


class Lights:
    """Point lights drawn as a sharp core plus a blurred glow layer."""

    def __init__(self, S):
        self.core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        self.halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        self.cd, self.hd = ImageDraw.Draw(self.core), ImageDraw.Draw(self.halo)

    def add(self, x, y, r, colour, glow=2.6):
        self.hd.ellipse([x - r * glow, y - r * glow, x + r * glow, y + r * glow], fill=colour + (150,))
        self.cd.ellipse([x - r, y - r, x + r, y + r], fill=colour + (255,))
        hr = r * 0.45  # hot white centre
        self.cd.ellipse([x - hr, y - hr, x + hr, y + hr], fill=(255, 255, 255, 230))

    def onto(self, img, blur):
        img.alpha_composite(self.halo.filter(ImageFilter.GaussianBlur(blur)))
        img.alpha_composite(self.core)


def _runway(img, v, detailed):
    d = ImageDraw.Draw(img)
    zt, ze = v.z_thr, v.z_end
    d.polygon(v.quad(-1, 1, zt, ze), fill=ASPHALT + (255,))
    # edge lines
    for u0, u1 in ((-0.975, -0.92), (0.92, 0.975)):
        d.polygon(v.quad(u0, u1, zt, ze), fill=PAINT + (255,))
    # threshold piano keys
    keys, span = (8, 0.82) if detailed else (4, 0.78)
    kw = span * 2 / (keys * 2 - 1)
    for k in range(keys):
        u0 = -span + k * 2 * kw
        d.polygon(v.quad(u0, u0 + kw, zt + 0.06, zt + 0.55), fill=PAINT + (255,))
    # centreline dashes, shrinking into the distance
    z = zt + 0.75
    while z < ze - 0.2:
        d.polygon(v.quad(-0.035, 0.035, z, z + 0.42), fill=PAINT + (255,))
        z += 0.85
    if detailed:
        # touchdown zone bars (3, 2, 2, 1 pairs) and the two big aiming-point blocks
        for zz, bars in ((zt + 0.95, 3), (zt + 1.45, 2), (zt + 3.2, 2), (zt + 4.1, 1)):
            for b in range(bars):
                u0 = 0.30 + b * 0.16
                for a0, a1 in ((u0, u0 + 0.10), (-u0 - 0.10, -u0)):
                    d.polygon(v.quad(a0, a1, zz, zz + 0.38), fill=PAINT + (255,))
        for a0, a1 in ((0.28, 0.62), (-0.62, -0.28)):
            d.polygon(v.quad(a0, a1, zt + 1.95, zt + 2.7), fill=PAINT + (255,))


def _papi(lights, v, z, side, r_base):
    """Four lights in a row beside the runway; on the glide path the two nearest the runway are red."""
    for k in range(4):
        u = side * (1.32 + k * 0.34)
        lights.add(v.x(u, z), v.y(z), v.size(r_base, z), LIGHT_RED if k < 2 else LIGHT_WHITE, glow=2.4)


def _approach_lights(lights, v, r_base, detailed):
    """ALSF-style: white centreline barrettes, a crossbar, red side rows, green threshold bar."""
    zt = v.z_thr
    steps = 9 if detailed else 5
    for i in range(steps):
        z = 1.22 + i * (zt - 0.12 - 1.22) / (steps - 1)
        n = 5 if detailed else 3
        for j in range(n):
            lights.add(v.x((j - (n - 1) / 2) * 0.085, z), v.y(z), v.size(r_base, z), LIGHT_WHITE)
        if detailed and i == 3:  # crossbar
            for j in range(-6, 7):
                if abs(j) > 2:
                    lights.add(v.x(j * 0.085, z), v.y(z), v.size(r_base, z), LIGHT_WHITE)
        if detailed and i >= steps - 4:  # red side-row barrettes on the inner part
            for side in (-1, 1):
                for j in range(3):
                    lights.add(v.x(side * (0.50 + j * 0.085), z), v.y(z), v.size(r_base * 0.9, z), LIGHT_RED)
    n = 14 if detailed else 7
    for j in range(n):  # green threshold bar, a little wider than the runway
        lights.add(v.x(-1.15 + j * 2.3 / (n - 1), zt), v.y(zt), v.size(r_base * 0.95, zt), LIGHT_GREEN)
    if detailed:  # white runway edge lights receding into the murk
        z = zt + 0.6
        while z < v.z_end:
            for side in (-1, 1):
                lights.add(v.x(side * 1.08, z), v.y(z), v.size(r_base * 0.85, z), LIGHT_WHITE, glow=2.0)
            z += 0.9


def _imc(S, horizon, seed=3):
    """Cloud wisps across the top and a mist bank on the horizon, as translucent blurred layers."""
    rnd = random.Random(seed)
    cloud = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cloud)
    for _ in range(26):
        cx, cy = rnd.uniform(-0.1, 1.1) * S, rnd.uniform(0.0, 0.42) * S
        rx, ry = rnd.uniform(0.12, 0.30) * S, rnd.uniform(0.035, 0.07) * S
        cd.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=MIST + (rnd.randint(28, 55),))
    cloud = cloud.filter(ImageFilter.GaussianBlur(S * 0.03))
    mist = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    md = ImageDraw.Draw(mist)
    for y in range(S):
        sigma = S * (0.16 if y < horizon else 0.07)  # fog thins quickly below the horizon
        a = int(110 * math.exp(-((y - horizon) / sigma) ** 2))
        if a:
            md.line([(0, y), (S, y)], fill=MIST + (a,))
    return cloud, mist


def app_icon(size=512):
    S = size * 4
    img = _gradient(S, SKY_TOP, SKY_BOTTOM, 0.0, 0.55)
    horizon = S * 0.48
    ImageDraw.Draw(img).rectangle([0, horizon, S, S], fill=GROUND + (255,))
    v = View(S, horizon, bottom=S * 1.0, half_width=S * 0.36, z_thr=1.85, z_end=16)
    _runway(img, v, detailed=True)
    cloud, mist = _imc(S, horizon)
    img.alpha_composite(mist)
    lights = Lights(S)
    _approach_lights(lights, v, r_base=S * 0.0082, detailed=True)
    for side in (-1, 1):
        _papi(lights, v, v.z_thr + 2.1, side, r_base=S * 0.036)
    lights.onto(img, blur=S * 0.012)
    img.alpha_composite(_fade(mist, 0.45))  # a thinner veil over the lights: they glow through it
    img.alpha_composite(cloud)

    # BUTTER across the top, with a soft glow so it lifts off the cloud
    d = ImageDraw.Draw(img)
    font = _font(int(S * 0.15))
    text = "BUTTER"
    l, t, r, _ = d.textbbox((0, 0), text, font=font)
    tx, ty = (S - (r - l)) / 2 - l, S * 0.085 - t
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((tx, ty), text, font=font, fill=BUTTER + (170,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.018)))
    d.text((tx, ty), text, font=font, fill=BUTTER + (255,))

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), _rounded_mask(S))
    return out.resize((size, size), Image.LANCZOS)


def small_icon(size):
    """16/32 px: no text, cloud or fine markings - just what still reads at that size."""
    S = size * 16
    img = _gradient(S, SKY_TOP, SKY_BOTTOM, 0.0, 0.5)
    horizon = S * 0.36
    ImageDraw.Draw(img).rectangle([0, horizon, S, S], fill=GROUND + (255,))
    v = View(S, horizon, bottom=S * 1.0, half_width=S * 0.42, z_thr=1.7, z_end=9)
    _runway(img, v, detailed=False)
    lights = Lights(S)
    _approach_lights(lights, v, r_base=S * 0.02, detailed=False)
    for side in (-1, 1):
        _papi(lights, v, v.z_thr + 1.3, side, r_base=S * 0.05)
    lights.onto(img, blur=S * 0.015)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), _rounded_mask(S))
    return out.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    big = app_icon(512)
    big.save(os.path.join(OUT, "icon_512.png"))
    sizes = {}
    for s in (256, 128, 64, 48):
        sizes[s] = big.resize((s, s), Image.LANCZOS)
        sizes[s].save(os.path.join(OUT, "icon_%d.png" % s))
    for s in (32, 16):
        sizes[s] = small_icon(s)
        sizes[s].save(os.path.join(OUT, "icon_%d.png" % s))
    # .ico: hand Pillow every frame so 16/32 use the simplified art instead of a downscale
    sizes[256].save(os.path.join(OUT, "landingrate.ico"), sizes=[(s, s) for s in (256, 128, 64, 48, 32, 16)],
                    append_images=[sizes[s] for s in (128, 64, 48, 32, 16)])
    print("assets written to", OUT)


if __name__ == "__main__":
    main()
