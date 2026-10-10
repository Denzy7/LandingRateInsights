"""Synthetic LandingRate.lua logs, for the self-test and the unit tests (no real flight data needed)."""

import random
from datetime import datetime, timedelta
from pathlib import Path

AIRCRAFT = ["B738", "BE58", "C172", "BE9L", "A320"]
FLARES = ["Very good flare", "Poor and late flare", "Poor and early flare", "Aggressive flare"]


def lines(n=200, seed=7, start=datetime(2025, 1, 4, 9, 0)):
    """n log lines in the non-VR format: time,ICAO,VS,G,nose,float,"flare",q,|Qrad|"""
    rnd = random.Random(seed)
    t = start
    out = []
    for i in range(n):
        # sessions of a few landings, a day or so apart, with the odd quick re-touch and crash
        t += timedelta(minutes=rnd.randint(6, 25)) if i % 5 else timedelta(hours=rnd.randint(20, 60))
        ac = rnd.choice(AIRCRAFT)
        vs = -abs(rnd.gauss(190, 90))
        if i % 97 == 50:
            vs = -1500.0
        out.append(f'{t:%Y-%m-%d %H:%M:%S},{ac},{vs:.2f},{rnd.gauss(1.05, 0.12):.2f},{rnd.uniform(-4, 2):.2f},'
                   f'{rnd.uniform(3, 14):.2f},"{rnd.choice(FLARES)}",{rnd.gauss(0, 1.5):.2f},{rnd.uniform(0, 0.2):.2f}')
        if i % 41 == 20:
            t += timedelta(seconds=20)
            out.append(f'{t:%Y-%m-%d %H:%M:%S},{ac},-80.00,1.01,0.00,0.50,"Very good flare",0.10,0.01')
    return out


def write_log(path, n=200, seed=7):
    Path(path).write_text("\n".join(lines(n, seed)) + "\n", encoding="utf-8")
    return Path(path)
