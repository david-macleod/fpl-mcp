"""
Park trees and street lamps.

Each is a Sprite: a signed distance function in world metres, evaluated per
pixel on the plane z = Z. Tree crowns are smooth unions of blobs roughened by
fBm, with sky holes carved out of the SDF itself so the ray caster really sees
through them. Painting uses layered washes: a light wet-in-wet crown, clumpy
mid-tones on the shadow side, dark accents, bark and a haze glaze with depth.
"""
import math
import random

import numpy as np

from wc_core import (smoothstep, fbm, sd_seg, sd_box, sd_tri, sd_circle, smin, pig,
                     region_sd)
from wc_city import Sprite, SHADOW, GOLD_LIGHT, HAZE

LEAF_LIGHT = pig('sap_green', 0.42, 'quin_gold', 0.42, 'lemon', 0.16)
LEAF_MID = pig('sap_green', 0.6, 'hookers', 0.22, 'burnt_sienna', 0.18)
LEAF_DARK = pig('hookers', 0.45, 'indigo', 0.4, 'burnt_umber', 0.15)
BARK = pig('burnt_umber', 0.45, 'ultramarine', 0.35, 'sepia', 0.2)
IRON = pig('indigo', 0.5, 'sepia', 0.5)


class Tree(Sprite):
    kind = 'tree'
    shadow_planes = (-2.0, 0.0, 2.0)

    def __init__(self, X, Z, height, crown_r, seed, Y0=0.0, lean=0.0, holes=True):
        rng = random.Random(seed)
        self.X, self.Z, self.Y0 = X, Z, Y0
        self.h = height
        self.r = crown_r
        self.seed = seed
        self.holes = holes
        self.cx = X + lean
        self.cy = Y0 + height - crown_r * 0.92
        self.ry = crown_r * rng.uniform(0.78, 0.92)
        self.blobs = []
        for _ in range(rng.randint(10, 15)):
            a = rng.uniform(0, 2 * math.pi)
            rr = math.sqrt(rng.random()) * 0.7
            self.blobs.append((self.cx + math.cos(a) * rr * crown_r,
                               self.cy + math.sin(a) * rr * self.ry,
                               crown_r * rng.uniform(0.3, 0.48)))
        rt = 0.11 + 0.055 * crown_r
        top = self.cy - self.ry * 0.25
        self.segs = [(X, Y0, X + lean * 0.55, top, rt, rt * 0.62)]
        for i in range(3):
            ang = math.radians(rng.uniform(-50, 50) + (i - 1) * 22)
            L = crown_r * rng.uniform(0.35, 0.6)
            x0, y0 = X + lean * 0.55, top
            self.segs.append((x0, y0, x0 + math.sin(ang) * L, y0 + math.cos(ang) * L * 0.8,
                              rt * 0.5, rt * 0.15))

    def bounds(self):
        return (self.cx - 1.7 * self.r, self.cx + 1.7 * self.r,
                self.Y0 - 0.1, self.cy + 1.35 * self.ry)

    def sdf_crown(self, X, Y):
        d = None
        k = 0.3 * self.r
        for (bx, by, br) in self.blobs:
            di = np.hypot(X - bx, Y - by) - br
            d = di if d is None else smin(d, di, k)
        s = self.seed
        n = fbm(X * 0.8, Y * 0.8, 4, s) - 0.5
        n2 = fbm(X * 3.2, Y * 3.2, 2, s + 1) - 0.5
        d = d + n * (self.r * 0.6) + n2 * 0.55
        if self.holes:
            # sky holes: rare in the dense core, commoner towards the edge
            hn = fbm(X * 1.1, Y * 1.1, 3, s + 2)
            inner = smoothstep(-0.2 * self.r, -0.9 * self.r, d)
            d = np.maximum(d, (hn - (0.66 + 0.07 * inner)) * 6.0)
        return d

    def sdf_trunk(self, X, Y):
        d = None
        for (x0, y0, x1, y1, r0, r1) in self.segs:
            di = sd_seg(X, Y, x0, y0, x1, y1, r0, r1)
            d = di if d is None else smin(d, di, 0.15)
        return d

    def sdf(self, X, Y):
        return np.minimum(self.sdf_crown(X, Y), self.sdf_trunk(X, Y))

    def shadow_sdf(self, X, Y):
        """Smoother, hole-free crown for broad, paintable cast shadows."""
        d = None
        for (bx, by, br) in self.blobs:
            di = np.hypot(X - bx, Y - by) - br
            d = di if d is None else smin(d, di, 0.3 * self.r)
        d = d + (fbm(X * 0.5, Y * 0.5, 3, self.seed + 9) - 0.5) * self.r * 0.9
        # a few large gaps let sunlight through in patches
        g = fbm(X * 0.35, Y * 0.35, 2, self.seed + 10)
        d = np.maximum(d, (g - 0.64) * 8.0)
        return np.minimum(d, self.sdf_trunk(X, Y))

    def paint(self, S):
        P, cam = S.P, S.cam
        b, m = S.region(self.id)
        if b is None:
            return
        sx, sy = P.grid(b)
        X, Y = cam.on_plane(sx, sy, self.Z)
        k = cam.f / self.Z
        vis = region_sd(m, P.px)
        dc = np.maximum(self.sdf_crown(X, Y) * k, vis)
        dt = np.maximum(self.sdf_trunk(X, Y) * k, vis)
        rpx = self.r * k * P.H
        fade = math.exp(-self.Z / 1400.0)
        haze = 1.0 - math.exp(-self.Z / 260.0)
        wob = min(0.0028, 0.06 * self.r * k)
        s = self.seed

        # form: a lumpy sphere lit from the upper right, broken into clumps
        Lf = S.form_light
        nx = (X - self.cx) / self.r
        ny = (Y - self.cy) / self.ry
        lam = 0.5 + 0.55 * (nx * Lf[0] + ny * Lf[1])
        cl = fbm(X * 0.55, Y * 0.55, 4, s + 5)
        shade = (1.0 - lam) + 1.3 * (cl - 0.5)

        P.wash(b, dc, LEAF_LIGHT, 0.46 * fade, soft=1.2, wob=wob, wob_f=40, edge=0.35,
               var=0.35, var_f=12, bloom=0.3, bloom_f=14, dry=0.35, seed=s)
        if rpx > 5:
            mid = (shade > 0.52) & (dc < 0)
            if mid.any():
                sdm = np.maximum(region_sd(mid, P.px), dc)
                P.wash(b, sdm, LEAF_MID, 0.52 * fade, soft=1.0, wob=wob * 0.8, wob_f=55,
                       edge=0.4, var=0.35, var_f=16, dry=0.25, seed=s + 1)
            dark = (shade > 0.88) & (dc < 0)
            if dark.any():
                sdd = np.maximum(region_sd(dark, P.px), dc)
                P.wash(b, sdd, LEAF_DARK, 0.62 * fade, soft=0.8, wob=wob * 0.6, wob_f=70,
                       edge=0.45, var=0.3, var_f=20, seed=s + 2)
            # warm rim where the low sun catches the crown
            P.wash(b, dc, GOLD_LIGHT, 0.2 * smoothstep(0.55, 0.95, lam) * fade, soft=2.0,
                   wob=wob, edge=0.05, var=0.4, var_f=12, seed=s + 3)
        else:
            P.wash(b, dc, LEAF_MID, 0.35 * smoothstep(0.3, 0.9, shade) * fade, soft=1.0,
                   edge=0.1, var=0.3, var_f=20, seed=s + 1)
        # trunk and branches, with a lit right-hand side
        P.wash(b, dt, BARK, 0.62 * fade, soft=0.8, wob=wob * 0.3, wob_f=90, edge=0.35,
               var=0.3, var_f=25, seed=s + 4)
        if rpx > 5:
            side = smoothstep(-0.2, 0.25, (X - self.X) / max(0.2, self.segs[0][4]))
            P.wash(b, dt, SHADOW, 0.45 * (1 - side) * fade, soft=0.8, edge=0.1, var=0.3,
                   var_f=25, seed=s + 5)
        # atmospheric depth
        P.wash(b, np.minimum(dc, dt), HAZE, 0.55 * haze, soft=1.5, edge=0.05, var=0.3,
               var_f=8, seed=s + 6)


class Lamp(Sprite):
    kind = 'lamp'
    shadow_planes = (0.0,)

    def __init__(self, X, Z, Y0=0.15, h=4.4):
        self.X, self.Z, self.Y0, self.h = X, Z, Y0, h

    def bounds(self):
        return (self.X - 0.5, self.X + 0.5, self.Y0, self.Y0 + self.h + 1.0)

    def _parts(self, X, Y):
        x = X - self.X
        y = Y - self.Y0
        h = self.h
        iron = sd_seg(x, y, 0, 0, 0, h, 0.075, 0.05)
        iron = np.minimum(iron, sd_seg(x, y, 0, 0, 0, 0.8, 0.14, 0.09))
        iron = np.minimum(iron, sd_tri(x, y, (-0.27, h + 0.55), (0.27, h + 0.55), (0, h + 0.8)))
        iron = np.minimum(iron, sd_circle(x, y, 0, h + 0.85, 0.05))
        iron = np.minimum(iron, sd_box(x, y, 0, h + 0.02, 0.13, 0.04))
        glass = sd_tri(x, y, (-0.2, h + 0.52), (0.2, h + 0.52), (0, h + 0.05))
        glass = np.minimum(glass, sd_box(x, y, 0, h + 0.36, 0.2, 0.17))
        return iron, glass

    def sdf(self, X, Y):
        iron, glass = self._parts(X, Y)
        return np.minimum(iron, glass)

    def paint(self, S):
        P, cam = S.P, S.cam
        b, m = S.region(self.id)
        if b is None:
            return
        sx, sy = P.grid(b)
        X, Y = cam.on_plane(sx, sy, self.Z)
        k = cam.f / self.Z
        vis = region_sd(m, P.px)
        iron, glass = self._parts(X, Y)
        fade = math.exp(-self.Z / 900.0)
        haze = 1.0 - math.exp(-self.Z / 260.0)
        P.wash(b, np.maximum(iron * k, vis), IRON, 0.85 * fade, soft=0.7, wob=0.0002,
               edge=0.3, var=0.25, var_f=40, seed=int(self.Z * 10))
        P.wash(b, np.maximum(glass * k, vis), WINDOW_GLOW, 0.4 * fade, soft=0.8, edge=0.3,
               var=0.3, seed=int(self.Z * 10) + 1)
        P.wash(b, np.maximum(np.minimum(iron, glass) * k, vis), HAZE, 0.5 * haze, soft=1,
               edge=0.0, var=0.2, seed=int(self.Z * 10) + 2)
        # warm halo around the lantern (a glaze over whatever is behind)
        cx, cy = cam.project(self.X, self.Y0 + self.h + 0.3, self.Z)
        rad = 1.6 * k
        hb = P.bbox_from_screen([cx - rad, cx + rad], [cy - rad, cy + rad])
        if hb is not None:
            gx, gy = P.grid(hb)
            dd = np.hypot(gx - cx, gy - cy)
            P.wash(hb, None, WINDOW_GLOW, 0.16 * np.exp(-dd / (0.45 * k)) * fade, var=0.3,
                   var_f=20, gran=0.0, seed=int(self.Z * 10) + 3)


WINDOW_GLOW = pig('quin_gold', 0.6, 'cad_orange', 0.4)


def populate_flora(S):
    rng = random.Random(S.seed * 31 + 7)
    trees = []
    # the giraffes' trees, leaning over the pavement
    trees.append(Tree(15.2, 30.5, 13.0, 5.0, 101, lean=-2.2))
    trees.append(Tree(16.2, 41.0, 15.0, 5.6, 102, lean=-1.0))
    # park-edge row
    z = 52.0
    while z < 700:
        trees.append(Tree(rng.uniform(15.0, 17.5), z, rng.uniform(12.5, 17.0),
                          rng.uniform(4.2, 6.0), rng.randrange(1 << 20),
                          lean=rng.uniform(-1.5, 0.5)))
        z += rng.uniform(9.0, 13.0) * (1.0 + z / 500.0)
    # second row, staggered behind
    z = 30.0
    while z < 500:
        trees.append(Tree(rng.uniform(21.0, 26.0), z, rng.uniform(13.0, 18.0),
                          rng.uniform(4.5, 6.5), rng.randrange(1 << 20),
                          lean=rng.uniform(-1.0, 1.0)))
        z += rng.uniform(11.0, 16.0) * (1.0 + z / 400.0)
    # the park interior, visible above the wall towards the right
    for _ in range(70):
        X = rng.uniform(28.0, 140.0)
        Z = rng.uniform(1.55 * X + 10.0, 1.55 * X + 260.0)
        t = Tree(X, Z, rng.uniform(13.0, 20.0), rng.uniform(5.0, 8.0),
                 rng.randrange(1 << 20), lean=rng.uniform(-1.0, 1.0), holes=Z < 160)
        t.casts = False  # its shadow falls on the park, out of sight
        trees.append(t)
    for t in trees:
        S.add(t)
    # lamps along both pavements
    for z in np.arange(11.0, 420.0, 27.0):
        S.add(Lamp(-10.1, float(z)))
    for z in np.arange(24.0, 420.0, 27.0):
        S.add(Lamp(10.2, float(z)))
