#!/usr/bin/env python3
"""
"Wild Hours" - a per-pixel procedural watercolour of a city avenue at golden
hour, with wild animals roaming through it.

    python render.py                    # full size 2400x1600 -> wild_hours.png
    python render.py --width 900 --out preview.png

Pipeline (every step is per-pixel numpy maths, no drawing libraries):
  1. paper    - cold-press paper tooth, fibres and cockling
  2. geometry - pinhole camera rays -> G-buffer (object, face, hit point)
  3. light    - sun shadows projected from every sprite onto all surfaces
  4. paint    - each surface is glazed with watercolour washes
  5. finish   - puddle reflections, pencil lines, splatter, paper grain
"""
import argparse
import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wc_core import Painter  # noqa: E402
from wc_city import Scene  # noqa: E402
from wc_flora import populate_flora  # noqa: E402
from wc_fauna import populate_fauna  # noqa: E402


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:7.1f}s] {msg}", flush=True)


def render(width, height, out, debug=False):
    P = Painter(width, height)
    log(f"paper {width}x{height}")
    S = Scene(P)
    S.build_city()
    populate_flora(S)
    populate_fauna(S)
    S.cast()
    log(f"ray cast: {len(S.boxes)} boxes, {len(S.sprites)} sprites")
    if debug:
        dbg = ((S.oid.astype(np.int64) * 2654435761) % 255).astype(np.uint8)
        Image.fromarray(dbg).save(os.path.splitext(out)[0] + '_ids.png')
    S.cast_shadows()
    log("shadows")
    S.paint_underwash()
    S.paint_sky()
    log("sky")
    S.paint_skyline()
    log("skyline")
    S.paint_boxes()
    log("buildings")
    S.paint_ground()
    log("ground")
    for sp in sorted(S.sprites, key=lambda s: -s.Z):
        sp.paint(S)
    for fn in getattr(S, 'post_paint', []):
        fn(S)
    log("sprites")
    S.paint_shadows()
    log("shadows painted")
    S.finish()
    log("reflections, pencil, splatter, vignette, paper grain")
    img = np.clip(P.R, 0, 1)
    Image.fromarray((img * 255 + 0.5).astype(np.uint8)).save(out)
    log(f"saved {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--width', type=int, default=2400)
    ap.add_argument('--out', default='wild_hours.png')
    ap.add_argument('--debug', action='store_true')
    a = ap.parse_args()
    render(a.width, int(round(a.width * 2 / 3)), a.out, a.debug)


if __name__ == '__main__':
    main()
