"""
The city: a pinhole camera looking down an avenue, a per-pixel ray caster that
fills a G-buffer (object id, face, world hit position, depth), sun shadows,
and the watercolour painting passes for sky, skyline, buildings and street.

World units are metres: X to the right, Y up, Z into the picture (the avenue
runs along +Z). Screen units are normalised so that the image height is 1.
"""
import math
import random

import numpy as np
from scipy import ndimage

from wc_core import (F32, smoothstep, fbm, hash01, sd_box, sd_tri, pig, region_sd)

NEAR = 0.3

# ---------------------------------------------------------------- palette
SHADOW = pig('ultramarine', 0.55, 'rose', 0.22, 'burnt_sienna', 0.23)
GOLD_LIGHT = pig('quin_gold', 0.55, 'rose', 0.25, 'cad_orange', 0.2)
HAZE = pig('cerulean', 0.45, 'rose', 0.35, 'ultramarine', 0.2)
WINDOW_DARK = pig('ultramarine', 0.45, 'burnt_umber', 0.4, 'rose', 0.15)
WINDOW_SKY = pig('cerulean', 0.55, 'rose', 0.3, 'ultramarine', 0.15)
WINDOW_LIT = pig('quin_gold', 0.65, 'cad_orange', 0.35)
ASPHALT = pig('ultramarine', 0.42, 'burnt_sienna', 0.4, 'rose', 0.18)
PAVING = pig('yellow_ochre', 0.4, 'ultramarine', 0.3, 'burnt_sienna', 0.3)
GRASS = pig('sap_green', 0.55, 'yellow_ochre', 0.3, 'hookers', 0.15)

STYLES = {
    'limestone': dict(pig=pig('yellow_ochre', 0.6, 'rose', 0.15, 'burnt_sienna', 0.25), dens=0.30),
    'pale': dict(pig=pig('yellow_ochre', 0.65, 'rose', 0.2, 'cerulean', 0.15), dens=0.20),
    'brick': dict(pig=pig('burnt_sienna', 0.7, 'rose', 0.2, 'yellow_ochre', 0.1), dens=0.46),
    'brownstone': dict(pig=pig('burnt_umber', 0.45, 'burnt_sienna', 0.45, 'rose', 0.1), dens=0.50),
    'terracotta': dict(pig=pig('cad_orange', 0.3, 'burnt_sienna', 0.5, 'rose', 0.2), dens=0.36),
    'white': dict(pig=pig('yellow_ochre', 0.5, 'ultramarine', 0.2, 'rose', 0.3), dens=0.11),
}


def normalize(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


class Camera:
    def __init__(self, f=1.0, xv=0.07, yh=-0.14, Xc=0.8, e=1.65):
        self.f, self.xv, self.yh, self.Xc, self.e = f, xv, yh, Xc, e

    def project(self, X, Y, Z):
        Z = np.maximum(Z, 1e-4)
        return (self.xv + self.f * (X - self.Xc) / Z,
                self.yh + self.f * (Y - self.e) / Z)

    def ray(self, sx, sy):
        """Direction (dx, dy, 1) of the pixel ray; ray parameter == depth."""
        return (sx - self.xv) / self.f, (sy - self.yh) / self.f

    def on_plane(self, sx, sy, Z):
        dx, dy = self.ray(sx, sy)
        return self.Xc + dx * Z, self.e + dy * Z


class Box:
    """Axis aligned box (building, sidewalk, wall)."""

    def __init__(self, X0, X1, Y0, Y1, Z0, Z1, kind, **style):
        self.X0, self.X1, self.Y0, self.Y1, self.Z0, self.Z1 = X0, X1, Y0, Y1, Z0, Z1
        self.kind = kind
        self.style = style
        self.id = None

    def corners(self):
        for X in (self.X0, self.X1):
            for Y in (self.Y0, self.Y1):
                for Z in (max(self.Z0, NEAR), max(self.Z1, NEAR)):
                    yield X, Y, Z


class Sprite:
    """Something flat standing in the plane z = Z, facing the camera
    (animals, trees, lamps). Subclasses provide bounds/sdf/paint."""
    kind = 'sprite'
    Z = 10.0
    casts = True
    shadow_planes = (0.0,)
    receives = True

    def bounds(self):
        raise NotImplementedError

    def sdf(self, X, Y):
        raise NotImplementedError

    def shadow_sdf(self, X, Y):
        return self.sdf(X, Y)

    def paint(self, scene):
        raise NotImplementedError


class Scene:
    def __init__(self, P, seed=3):
        self.P = P
        self.rng = random.Random(seed)
        self.seed = seed
        self.cam = Camera()
        # low golden-hour sun to the right, slightly ahead of the camera
        self.sun = normalize((0.90, 0.36, 0.20))
        # light used for painted form shading of rounded things
        self.form_light = normalize((0.62, 0.55, -0.55))
        self.boxes = []
        self.sprites = []
        self.objects = {}
        H, W = P.H, P.W
        self.depth = np.full((H, W), np.inf, F32)
        self.oid = np.full((H, W), -1, np.int32)
        self.face = np.zeros((H, W), np.int8)
        self.hX = np.zeros((H, W), F32)
        self.hY = np.zeros((H, W), F32)
        self.hZ = np.zeros((H, W), F32)
        self.shadow = np.zeros((H, W), bool)
        self._next_id = 1

    # ------------------------------------------------------------ objects
    def add(self, obj):
        obj.id = self._next_id
        self._next_id += 1
        self.objects[obj.id] = obj
        if isinstance(obj, Box):
            self.boxes.append(obj)
        else:
            self.sprites.append(obj)
        return obj

    # ------------------------------------------------------------ layout
    def build_city(self):
        rng = self.rng
        big = 4000.0
        self.add(Box(-15.0, -9.0, 0.0, 0.15, -5.0, big, 'sidewalk', side=-1))
        self.add(Box(9.0, 13.0, 0.0, 0.15, -5.0, big, 'sidewalk', side=1))
        self.add(Box(13.0, 13.6, 0.0, 1.0, -5.0, big, 'wall'))

        XF = -15.0
        # hand-placed near lots, then a procedural avenue into the distance
        plan = [
            (26.0, 44.0, 'limestone', dict(rustic=True)),
            (24.0, 58.0, 'pale', dict(rustic=True, awning='green')),
            ('street', 17.0),
            (22.0, 38.0, 'brick', dict(awning='red')),
            (19.0, 70.0, 'limestone', dict(tower=112.0)),
            (16.0, 24.0, 'brownstone', {}),
            (21.0, 47.0, 'terracotta', {}),
            (25.0, 55.0, 'pale', dict(rustic=True)),
            ('street', 17.0),
        ]
        z = -6.0
        k = 0
        while z < 2600.0:
            if k < len(plan):
                item = plan[k]
            else:
                if rng.random() < 0.14:
                    item = ('street', 17.0)
                else:
                    st = rng.choice(['limestone', 'pale', 'brick', 'brownstone', 'terracotta',
                                     'white', 'pale', 'limestone'])
                    h = rng.choice([22, 30, 38, 45, 52, 60, 68]) + rng.uniform(-3, 3)
                    extra = {}
                    if rng.random() < 0.18:
                        extra['tower'] = h + rng.uniform(25, 90)
                    item = (rng.uniform(16, 30), h, st, extra)
            k += 1
            if item[0] == 'street':
                gap = item[1]
                # buildings along the north side of the cross street: their
                # fronts face the camera through the gap
                zf = z + gap
                xs = XF - 22.0
                for _ in range(4):
                    w = rng.uniform(16, 28)
                    self.add(Box(xs - w, xs, 0.0, rng.uniform(22, 55), zf, zf + 25.0, 'building',
                                 **self._style(rng.choice(['brick', 'limestone', 'brownstone', 'pale']))))
                    xs -= w
                z = zf
                continue
            length, h, st, extra = item
            depth = 22.0
            self.add(Box(XF - depth, XF, 0.0, h, z, z + length, 'building',
                         **self._style(st, **extra)))
            if 'tower' in extra:
                self.add(Box(XF - depth + 2.0, XF - 6.0, 0.0, extra['tower'], z + 3.0,
                             z + length - 3.0, 'building', **self._style('pale', setback=True)))
            z += length

    def _style(self, name, **kw):
        rng = self.rng
        s = dict(STYLES[name])
        s['name'] = name
        s['gf'] = rng.uniform(4.4, 5.4)
        s['fh'] = rng.uniform(3.1, 3.6)
        s['bay'] = rng.uniform(2.8, 3.6)
        s['ww'] = s['bay'] * rng.uniform(0.38, 0.5)
        s['wh'] = s['fh'] * rng.uniform(0.52, 0.64)
        s['sill'] = s['fh'] * 0.24
        s['margin'] = rng.uniform(0.6, 1.6)
        s['cornice'] = rng.uniform(1.0, 1.8)
        s['belt_every'] = rng.choice([0, 0, 3, 4, 5])
        s['lit_frac'] = rng.uniform(0.03, 0.09)
        s['seed'] = rng.randrange(1 << 20)
        s.update(kw)
        return s

    # ------------------------------------------------------------ ray casting
    def cast(self):
        self._cast_ground()
        for bx in self.boxes:
            self._cast_box(bx)
        for sp in sorted(self.sprites, key=lambda s: -s.Z):
            self._cast_sprite(sp)
        self.slices = ndimage.find_objects(self.oid + 2)

    def _cast_ground(self):
        P, c = self.P, self.cam
        b = P.full()
        sx, sy = P.grid(b)
        dx, dy = c.ray(sx, sy)
        t = np.where(dy < -1e-7, -c.e / np.minimum(dy, -1e-7), np.inf).astype(F32)
        hit = np.isfinite(t)
        self.depth[hit] = t[hit]
        self.oid[hit] = 0
        self.face[hit] = 1
        self.hX[hit] = (c.Xc + dx * t)[hit]
        self.hY[hit] = 0.0
        self.hZ[hit] = t[hit]

    def _cast_box(self, bx):
        P, c = self.P, self.cam
        pts = np.array(list(bx.corners()))
        sxs, sys_ = c.project(pts[:, 0], pts[:, 1], pts[:, 2])
        b = P.bbox_from_screen(sxs, sys_, pad=2 * P.px)
        if b is None:
            return
        y0, y1, x0, x1 = b
        sx, sy = P.grid(b)
        dx, dy = c.ray(sx, sy)
        dx = np.where(np.abs(dx) < 1e-7, 1e-7, dx)
        dy = np.where(np.abs(dy) < 1e-7, 1e-7, dy)
        ta = (bx.X0 - c.Xc) / dx
        tb = (bx.X1 - c.Xc) / dx
        txn = np.minimum(ta, tb)
        txf = np.maximum(ta, tb)
        ta = (bx.Y0 - c.e) / dy
        tb = (bx.Y1 - c.e) / dy
        tyn = np.minimum(ta, tb)
        tyf = np.maximum(ta, tb)
        tn = np.maximum(np.maximum(txn, tyn), bx.Z0)
        tf = np.minimum(np.minimum(txf, tyf), bx.Z1)
        dc = self.depth[y0:y1, x0:x1]
        hit = (tn <= tf) & (tn > NEAR) & (tn < dc)
        if not hit.any():
            return
        face = np.where(tn == txn, 0, np.where(tn == tyn, 1, 2)).astype(np.int8)
        dc[hit] = tn[hit]
        self.oid[y0:y1, x0:x1][hit] = bx.id
        self.face[y0:y1, x0:x1][hit] = face[hit]
        self.hX[y0:y1, x0:x1][hit] = (c.Xc + dx * tn)[hit]
        self.hY[y0:y1, x0:x1][hit] = (c.e + dy * tn)[hit]
        self.hZ[y0:y1, x0:x1][hit] = tn[hit]

    def sprite_bbox(self, sp, pad=0.004):
        Xa, Xb, Ya, Yb = sp.bounds()
        sxs, sys_ = self.cam.project(np.array([Xa, Xb, Xa, Xb]), np.array([Ya, Ya, Yb, Yb]),
                                     np.full(4, sp.Z))
        return self.P.bbox_from_screen(sxs, sys_, pad=pad)

    def _cast_sprite(self, sp):
        P, c = self.P, self.cam
        b = self.sprite_bbox(sp)
        sp.bbox = b
        if b is None:
            return
        y0, y1, x0, x1 = b
        sx, sy = P.grid(b)
        X, Y = c.on_plane(sx, sy, sp.Z)
        d = sp.sdf(X, Y)
        sp.sd_screen = (d * (c.f / sp.Z)).astype(F32)
        dc = self.depth[y0:y1, x0:x1]
        hit = (d < 0) & (sp.Z < dc)
        dc[hit] = sp.Z
        self.oid[y0:y1, x0:x1][hit] = sp.id
        self.face[y0:y1, x0:x1][hit] = 3
        self.hX[y0:y1, x0:x1][hit] = X[hit]
        self.hY[y0:y1, x0:x1][hit] = Y[hit]
        self.hZ[y0:y1, x0:x1][hit] = sp.Z

    # ------------------------------------------------------------ regions
    def region(self, oid, face=None, pad=16):
        sl = self.slices[oid + 1] if oid + 1 < len(self.slices) else None
        if sl is None:
            return None, None
        P = self.P
        b = (max(0, sl[0].start - pad), min(P.H, sl[0].stop + pad),
             max(0, sl[1].start - pad), min(P.W, sl[1].stop + pad))
        m = self.oid[b[0]:b[1], b[2]:b[3]] == oid
        if face is not None:
            m &= self.face[b[0]:b[1], b[2]:b[3]] == face
        if not m.any():
            return None, None
        return b, m

    def crop(self, arr, b):
        return arr[b[0]:b[1], b[2]:b[3]]

    # ------------------------------------------------------------ shadows
    def lit_receiver(self, b):
        """True where the surface faces the sun (cast shadows only matter there)."""
        oid = self.crop(self.oid, b)
        face = self.crop(self.face, b)
        ok = oid >= 0
        out = ok & ((face == 1) | (face == 3))
        # X faces: lit when their outward normal points to +X
        xf = ok & (face == 0)
        if xf.any():
            X = self.crop(self.hX, b)
            out |= xf & (X < self.cam.Xc)
        return out

    def cast_shadows(self):
        """Project every sprite's silhouette along the sun direction onto all
        receiving surfaces (ground, pavements, facades, other sprites)."""
        L = self.sun
        P, c = self.P, self.cam
        for sp in self.sprites:
            if not sp.casts:
                continue
            Xa, Xb, Ya, Yb = sp.bounds()
            pts = []
            for X in (Xa, Xb):
                for Y in (Ya, Yb):
                    for dz in sp.shadow_planes:
                        Z = sp.Z + dz
                        pts.append((X, Y, Z))
                        tg = Y / L[1]
                        pts.append((X - tg * L[0], 0.0, Z - tg * L[2]))
                        tf = (X + 15.0) / L[0]
                        if tf > 0:
                            pts.append((-15.0, max(Y - tf * L[1], 0.0), Z - tf * L[2]))
            pts = np.array(pts)
            pts[:, 2] = np.maximum(pts[:, 2], NEAR)
            sxs, sys_ = c.project(pts[:, 0], pts[:, 1], pts[:, 2])
            b = P.bbox_from_screen(sxs, sys_, pad=0.01)
            if b is None:
                continue
            recv = self.crop(self.oid, b)
            fc = self.crop(self.face, b)
            ok = (recv >= 0) & (fc != 3)
            if sp.kind == 'tree':
                ok &= fc != 0
            if not ok.any():
                continue
            PX_ = self.crop(self.hX, b)
            PY_ = self.crop(self.hY, b)
            PZ_ = self.crop(self.hZ, b)
            inside = np.zeros(ok.shape, bool)
            for dz in sp.shadow_planes:
                t = (sp.Z + dz - PZ_) / L[2]
                Xs = PX_ + t * L[0]
                Ys = PY_ + t * L[1]
                # only evaluate the SDF where the sun ray actually crosses
                # the sprite's bounding rectangle
                v = ok & (t > 0.05) & (Xs > Xa) & (Xs < Xb) & (Ys > Ya) & (Ys < Yb)
                if not v.any():
                    continue
                d = sp.shadow_sdf(Xs[v], Ys[v])
                inside[v] |= d < 0
            self.shadow[b[0]:b[1], b[2]:b[3]] |= inside

    def paint_shadows(self):
        P = self.P
        b = P.full()
        m = self.shadow & self.lit_receiver(b)
        if not m.any():
            return
        sl = ndimage.find_objects(m.astype(np.int8))[0]
        pad = 12
        b = (max(0, sl[0].start - pad), min(P.H, sl[0].stop + pad),
             max(0, sl[1].start - pad), min(P.W, sl[1].stop + pad))
        mm = m[b[0]:b[1], b[2]:b[3]]
        sd = region_sd(mm, P.px)
        Z = self.crop(self.hZ, b)
        fade = np.exp(-Z / 900.0)
        P.wash(b, sd, SHADOW, 0.42 * fade, soft=1.2, wob=0.0014, wob_f=60, edge=0.35,
               edge_w=0.003, var=0.25, var_f=10, seed=901)

    # ------------------------------------------------------------ painting: sky
    def paint_underwash(self):
        """A pale golden wash over the whole sheet unifies the colours."""
        P = self.P
        b = P.full()
        sx, sy = P.grid(b)
        glow = np.exp(-((sx - 0.9) ** 2) / 0.45 - ((sy - 0.0) ** 2) / 0.35)
        P.wash(b, None, pig('quin_gold', 0.6, 'rose', 0.4), 0.05 + 0.08 * glow,
               var=0.5, var_f=2.5, gran=0.2, seed=11)

    def paint_sky(self):
        P, c = self.P, self.cam
        b = P.full()
        sky = self.oid == -1
        sd = region_sd(sky, P.px)
        sx, sy = P.grid(b)
        t = np.clip((sy - c.yh) / (0.5 - c.yh), 0, 1)  # 0 at horizon, 1 at top
        r = np.clip((sx + 0.75) / 1.5, 0, 1)             # 0 left, 1 right
        glow = np.exp(-((sx - 0.95) ** 2) / 0.30 - ((sy - 0.02) ** 2) / 0.10)

        # clouds: a few long banks, horizontally stretched, lit from below
        band = smoothstep(0.28, 0.5, t) * smoothstep(1.0, 0.72, t)
        cf = P.snoise(b, 1.4, 5, seed=21, ax=0.5, ay=2.8)
        cloud = smoothstep(0.55, 0.68, cf * (0.72 + 0.4 * band))
        sh = int(0.016 * P.H)
        cf_up = P.snoise((b[0] - sh, b[1] - sh, b[2], b[3]), 1.4, 5, seed=21, ax=0.5, ay=2.8)
        band_up = np.concatenate([band[:1].repeat(sh, 0), band[:-sh]], 0) if sh else band
        cloud_up = smoothstep(0.55, 0.68, cf_up * (0.72 + 0.4 * band_up))

        # the sky wash is cut in loosely around the buildings
        sdw = sd - 0.0015
        gold = 0.30 * smoothstep(0.8, 0.0, t) * (0.45 + 0.55 * r) + 0.08 * glow
        P.wash(b, sdw, pig('quin_gold', 0.55, 'cad_yellow', 0.3, 'cad_orange', 0.15), gold,
               soft=2.0, wob=0.003, wob_f=25, edge=0.12, var=0.35, var_f=3.0, gran=0.3,
               bloom=0.2, bloom_f=5, seed=22)
        rose = 0.17 * np.exp(-((t - 0.36) / 0.22) ** 2) * (0.5 + 0.5 * r) * (1 - 0.6 * glow)
        P.wash(b, sdw, pig('rose', 0.85, 'cad_orange', 0.15), rose, soft=3.0, wob=0.003,
               edge=0.1, var=0.4, var_f=3.5, gran=0.2, seed=23)
        blue = 0.80 * smoothstep(0.26, 1.0, t) ** 1.3 * (1.0 - 0.45 * r) * (1 - 0.8 * glow)
        blue = blue * (1.0 - 0.9 * cloud)
        P.wash(b, sdw, pig('cerulean', 0.55, 'ultramarine', 0.37, 'rose', 0.08), blue,
               soft=2.0, wob=0.003, edge=0.15, var=0.3, var_f=3.0, var_ax=0.5, var_ay=2.0,
               gran=0.45, bloom=0.3, bloom_f=4.0, seed=24)
        # cloud shading: soft violet tops, rose-gold glowing undersides
        top = cloud * cloud_up
        P.wash(b, sdw, pig('ultramarine', 0.5, 'rose', 0.5), 0.17 * top * (0.4 + 0.6 * t),
               soft=5.0, edge=0.05, var=0.5, var_f=5, gran=0.6, seed=25)
        under = cloud * (1 - cloud_up)
        P.wash(b, sdw, pig('rose', 0.65, 'quin_gold', 0.35), 0.24 * under, soft=2.5,
               edge=0.15, var=0.4, var_f=6, seed=26)

    def paint_skyline(self):
        """Hazy towers far beyond the park and at the end of the avenue
        (screen-space silhouettes standing on the horizon)."""
        P, c = self.P, self.cam
        rng = random.Random(self.seed * 7 + 1)
        towers = []
        # a distant downtown rising behind the park, tallest right of the
        # vanishing point, plus low roofs along the horizon further right
        x = c.xv - 0.05
        peak = c.xv + 0.13
        while x < 0.80:
            w = rng.uniform(0.011, 0.036)
            env = math.exp(-((x - peak) / 0.1) ** 2)
            h = (0.03 + 0.15 * env) * rng.uniform(0.55, 1.15)
            if env > 0.3 and rng.random() < 0.1:
                w *= 0.5
                h = rng.uniform(0.2, 0.28) * env ** 0.3  # slender super-tall
            kind = rng.choices(['flat', 'step', 'spire', 'crown'], [6, 3, 1, 1])[0]
            towers.append((x + w / 2, w / 2, h, kind))
            x += w * rng.uniform(0.5, 1.0)
        ymax = c.yh + 0.36
        b = P.bbox_from_screen([c.xv - 0.05, 0.76], [c.yh - 0.01, ymax], pad=0.005)
        sx, sy = P.grid(b)
        d = np.full(sx.shape, 1.0, F32)
        dfar = np.full(sx.shape, 1.0, F32)
        for (cx, hw, h, kind) in towers:
            base = c.yh - 0.01
            top = c.yh + h
            dt = sd_box(sx, sy, cx, (base + top) / 2, hw, (top - base) / 2)
            if kind == 'step':
                dt = np.minimum(dt, sd_box(sx, sy, cx, top + h * 0.08, hw * 0.62, h * 0.08))
                dt = np.minimum(dt, sd_box(sx, sy, cx, top + h * 0.2, hw * 0.3, h * 0.05))
            elif kind == 'spire':
                dt = np.minimum(dt, sd_box(sx, sy, cx, top + h * 0.06, hw * 0.5, h * 0.06))
                dt = np.minimum(dt, sd_tri(sx, sy, (cx - hw * 0.25, top + h * 0.1),
                                           (cx + hw * 0.25, top + h * 0.1), (cx, top + h * 0.32)))
            elif kind == 'crown':
                dt = np.minimum(dt, sd_tri(sx, sy, (cx - hw, top - 0.0005), (cx + hw, top - 0.0005),
                                           (cx, top + hw * 1.1)))
            if h < 0.075:
                dfar = np.minimum(dfar, dt)
            else:
                d = np.minimum(d, dt)
        skym = self.crop(self.oid, b) == -1
        sdsky = region_sd(skym, P.px)
        # far layer at the end of the avenue: palest
        for dd, dens, sd_seed in ((dfar, 0.22, 31), (d, 0.34, 32)):
            sdr = np.maximum(dd, sdsky)
            haze = smoothstep(c.yh - 0.005, c.yh + 0.06, sy)
            P.wash(b, sdr, pig('ultramarine', 0.4, 'rose', 0.35, 'cerulean', 0.25),
                   dens * (0.45 + 0.55 * haze), soft=0.9, wob=0.0005, wob_f=90, edge=0.25,
                   var=0.3, var_f=12, gran=0.9, seed=sd_seed)
            # glazed shadow side on the left of each tower
            P.wash(b, sdr + 0.0004, pig('ultramarine', 0.6, 'rose', 0.4), dens * 0.35 * haze,
                   soft=0.9, wob=0.0004, edge=0.2, var=0.4, var_f=20, seed=sd_seed + 5,
                   mask=smoothstep(0.0, 1.0, self._left_half(sx, towers)))

    @staticmethod
    def _left_half(sx, towers):
        """1 on the left (shadow) half of each tower, 0 on the lit right."""
        out = np.zeros(sx.shape, F32)
        for (cx, hw, h, kind) in towers:
            inside = np.abs(sx - cx) < hw
            out = np.where(inside, np.clip((cx + hw * 0.2 - sx) / (hw * 0.4), 0, 1), out)
        return out

    # ------------------------------------------------------------ painting: buildings
    def paint_boxes(self):
        for bx in self.boxes:
            if bx.kind == 'building':
                self._paint_building(bx)
            elif bx.kind == 'sidewalk':
                self._paint_sidewalk(bx)
            elif bx.kind == 'wall':
                self._paint_wall(bx)

    def _paint_building(self, bx):
        for face in (0, 2):
            b, m = self.region(bx.id, face)
            if b is None:
                continue
            sd = region_sd(m, self.P.px)
            if face == 0:
                self._paint_facade(bx, b, m, sd, along='z')
            else:
                self._paint_facade(bx, b, m, sd, along='x')

    def _paint_facade(self, bx, b, m, sd, along):
        P, c = self.P, self.cam
        st = bx.style
        seed = st['seed']
        Z = self.crop(self.hZ, b)
        Z = np.where(m, Z, np.median(Z[m]))
        v = self.crop(self.hY, b)
        Htop = bx.Y1
        lit = along == 'z'
        if lit:
            u = Z - bx.Z0
            su = c.f * abs(bx.X1 - c.Xc) / (Z * Z)
            nb_len = bx.Z1 - bx.Z0
        else:
            u = bx.X1 - self.crop(self.hX, b)
            su = c.f / Z
            nb_len = bx.X1 - bx.X0
        sv = c.f / Z
        fade = np.exp(-Z / 1700.0)
        haze = 1.0 - np.exp(-Z / 380.0)
        vn = np.clip(v / Htop, 0, 1)

        # 1. local colour, darker towards the street (less sky light)
        P.wash(b, sd, st['pig'], st['dens'] * (1.0 + 0.35 * (1 - vn)) * fade, soft=0.8,
               wob=0.0018, wob_f=35, edge=0.35, var=0.28, var_f=7, seed=seed)
        if lit:
            # 2. golden light raking along the facade, strongest up high
            P.wash(b, sd + 0.0012, GOLD_LIGHT, (0.06 + 0.12 * vn) * fade, soft=2.5,
                   wob=0.003, edge=0.08, var=0.45, var_f=4, seed=seed + 1)
        else:
            # faces turned away from the sun: cool transparent shadow
            P.wash(b, sd - 0.0008, SHADOW, (0.30 + 0.16 * vn) * fade, soft=1.0, wob=0.0015,
                   edge=0.3, var=0.3, var_f=8, seed=seed + 1)

        # 3. cornice and belt-course shadows
        cor = st['cornice']
        sd_c = (np.abs(v - (Htop - cor - 0.45)) - 0.45) * sv
        sd_c = np.minimum(sd_c, (np.abs(v - (Htop - 0.25)) - 0.08) * sv)
        if st['belt_every']:
            period = st['fh'] * st['belt_every']
            vb = np.mod(v - st['gf'], period)
            sd_c = np.minimum(sd_c, (np.abs(vb - period + 0.2) - 0.14) * sv)
        sd_c = np.minimum(sd_c, (np.abs(v - st['gf'] + 0.25) - 0.22) * sv)
        sd_c = np.maximum(sd_c, sd)
        P.wash(b, sd_c, SHADOW, 0.38 * fade, soft=0.8, wob=0.0006, wob_f=80, edge=0.3,
               var=0.3, var_f=15, seed=seed + 2)

        # 4. windows
        gf, fh, bay, ww, wh, sill = st['gf'], st['fh'], st['bay'], st['ww'], st['wh'], st['sill']
        top_margin = cor + 1.2
        nfl = max(0, int((Htop - gf - top_margin) // fh))
        nbays = max(1, int((nb_len - 2 * st['margin']) // bay))
        uu = u - st['margin']
        j = np.floor(uu / bay)
        fu = uu - (j + 0.5) * bay
        vv = v - gf
        k = np.floor(vv / fh)
        fv = vv - k * fh - (sill + wh / 2)
        valid = (k >= 0) & (k < nfl) & (j >= 0) & (j < nbays) & m
        wpx = ww * su * P.H
        lod = smoothstep(1.0, 3.5, wpx)
        if valid.any() and float(np.max(np.where(valid, wpx, 0))) > 0.8:
            ji = j.astype(np.int64)
            ki = k.astype(np.int64) + 1000 * bx.id
            r1 = hash01(ji, ki, seed)
            r2 = hash01(ji, ki, seed + 1)
            r3 = hash01(ji, ki, seed + 2)
            sdw = np.maximum((np.abs(fu) - ww / 2) * su, (np.abs(fv) - wh / 2) * sv)
            sdw = np.where(valid & (r3 > 0.06), sdw, 1.0)
            sdw = np.maximum(sdw, sd)
            # glazing bars reserved as paper-light lines
            bar = np.minimum(np.abs(fu) * su, np.abs(fv - wh * 0.12) * sv)
            bars = smoothstep(0.9 * P.px, 0.3 * P.px, bar) * smoothstep(6, 12, wpx)
            recess = np.maximum(smoothstep(wh / 2 - 0.35, wh / 2 - 0.1, fv),
                                smoothstep(-ww / 2 + 0.22, -ww / 2 + 0.05, fu))
            kind_lit = r2 < st['lit_frac']
            kind_sky = (r2 >= st['lit_frac']) & (r2 < st['lit_frac'] + (0.25 if lit else 0.1))
            dark = ~(kind_lit | kind_sky)
            base_d = (0.42 + 0.4 * r1) * (1.0 if lit else 1.15)
            dens = np.where(dark, base_d, 0.0) * (1 - 0.8 * bars) * (1 + 0.6 * recess)
            P.wash(b, sdw, WINDOW_DARK, dens * lod * fade, soft=0.7, wob=0.0004, wob_f=90,
                   edge=0.3, edge_w=0.0015, var=0.3, var_f=30, seed=seed + 3, fine=0.12)
            P.wash(b, sdw, WINDOW_SKY, np.where(kind_sky, 0.35 + 0.2 * r1, 0.0) * lod * fade
                   * (1 - 0.8 * bars) * (1 + 0.8 * recess), soft=0.7, wob=0.0004, wob_f=90,
                   edge=0.25, edge_w=0.0015, var=0.35, var_f=30, seed=seed + 4, fine=0.12)
            P.wash(b, sdw, WINDOW_LIT, np.where(kind_lit, 0.55, 0.0) * lod * fade,
                   soft=0.7, wob=0.0004, wob_f=90, edge=0.2, var=0.3, var_f=30, seed=seed + 5,
                   fine=0.12)
        # far away the windows melt into faint floor bands
        band = smoothstep(2.0, 5.0, fh * sv * P.H) * (1 - lod)
        if float(band.max()) > 0.02:
            fvb = np.mod(v - gf, fh) - (sill + wh / 2)
            sdb = np.maximum((np.abs(fvb) - wh * 0.35) * sv, sd)
            sdb = np.where((v > gf) & (v < Htop - top_margin), sdb, 1.0)
            P.wash(b, sdb, WINDOW_DARK, 0.28 * band * fade, soft=0.7, wob=0.0005, edge=0.1,
                   var=0.5, var_f=25, seed=seed + 6)

        # 5. street level: shop windows, doors, rustication, awnings
        spx = 0.6 * su * P.H
        if float(np.max(np.where(m, spx, 0))) > 1.0:
            sb = bay * 2.0
            js = np.floor(uu / sb)
            fus = uu - (js + 0.5) * sb
            sd_shop = np.maximum((np.abs(fus) - sb * 0.36) * su, (np.abs(v - (gf * 0.45)) - gf * 0.33) * sv)
            sd_shop = np.where((js >= 0) & (js < max(1, int((nb_len - 2 * st['margin']) // sb))),
                               sd_shop, 1.0)
            sd_shop = np.maximum(sd_shop, sd)
            rs = hash01(js.astype(np.int64), np.full(js.shape, bx.id, np.int64), seed + 7)
            P.wash(b, sd_shop, WINDOW_DARK, (0.55 + 0.35 * rs) * fade, soft=0.7, wob=0.0007,
                   edge=0.35, var=0.35, var_f=20, seed=seed + 8)
            if st.get('rustic'):
                gr = np.abs(np.mod(v, 0.62) - 0.31)
                sd_r = np.maximum((0.31 - gr - 0.035) * sv, sd)
                sd_r = np.where(v < gf - 0.3, sd_r, 1.0)
                P.wash(b, sd_r, SHADOW, 0.16 * fade * smoothstep(1.0, 3.0, 0.62 * sv * P.H),
                       soft=0.6, wob=0.0004, edge=0.1, var=0.4, var_f=30, dry=0.5, seed=seed + 9)
            aw = st.get('awning')
            if aw:
                apig = (pig('hookers', 0.7, 'indigo', 0.3) if aw == 'green'
                        else pig('alizarin', 0.75, 'burnt_umber', 0.25))
                sd_a = np.maximum((np.abs(fus) - sb * 0.42) * su,
                                  (np.abs(v - (gf - 0.75)) - 0.42) * sv)
                sd_a = np.where((js >= 0) & (js < 3), sd_a, 1.0)
                sd_a = np.maximum(sd_a, sd)
                P.wash(b, sd_a, apig, 0.75 * fade, soft=0.7, wob=0.0008, edge=0.35, var=0.3,
                       var_f=20, seed=seed + 10)
                sd_as = np.maximum((np.abs(fus) - sb * 0.42) * su,
                                   (np.abs(v - (gf - 1.45)) - 0.28) * sv)
                sd_as = np.maximum(np.where((js >= 0) & (js < 3), sd_as, 1.0), sd)
                P.wash(b, sd_as, SHADOW, 0.45 * fade, soft=1.5, wob=0.0008, edge=0.2,
                       var=0.3, var_f=20, seed=seed + 11)

        # 6. atmospheric perspective
        P.wash(b, sd - 0.0005, HAZE, 0.42 * haze, soft=1.5, wob=0.001, edge=0.05, var=0.25,
               var_f=5, gran=0.5, seed=seed + 12)

    def _paint_sidewalk(self, bx):
        P, c = self.P, self.cam
        side = bx.style['side']
        # top surface
        b, m = self.region(bx.id, 1)
        if b is not None:
            sd = region_sd(m, P.px)
            X = self.crop(self.hX, b)
            Z = np.where(m, self.crop(self.hZ, b), 50.0)
            sxs = c.f / Z
            szs = c.f * (c.e - 0.15) / (Z * Z)
            fade = np.exp(-Z / 1500.0)
            P.wash(b, sd, PAVING, 0.22 * fade, soft=0.8, wob=0.0015, edge=0.25, var=0.3,
                   var_f=6, var_ax=2.0, var_ay=0.6, seed=41 + bx.id)
            # paving joints
            gx = np.abs(np.mod(X, 1.6) - 0.8)
            gz = np.abs(np.mod(Z, 1.6) - 0.8)
            dj = np.minimum((0.8 - gx) * sxs, (0.8 - gz) * szs) - 0.35 * P.px
            lod = smoothstep(2.0, 6.0, 1.6 * szs * P.H)
            P.wash(b, np.maximum(dj, sd), PAVING, 0.22 * lod * fade, soft=0.6, wob=0.0004,
                   edge=0.1, var=0.4, var_f=30, dry=0.6, seed=42 + bx.id)
            # kerb stones: a light band with a crisp inner joint
            xk = -9.0 if side < 0 else 9.0
            dk = (np.abs(X - (xk - 0.3 * side)) - 0.01) * sxs - 0.3 * P.px
            P.wash(b, np.maximum(dk, sd), SHADOW, 0.25 * fade, soft=0.6, wob=0.0004,
                   edge=0.1, var=0.3, var_f=30, seed=43 + bx.id)
            P.wash(b, sd, GOLD_LIGHT, 0.08 * fade, soft=2, wob=0.002, edge=0.05, var=0.4,
                   var_f=5, seed=44 + bx.id)
        # kerb face
        b, m = self.region(bx.id, 0)
        if b is not None:
            sd = region_sd(m, P.px)
            if side < 0:
                P.wash(b, sd, PAVING, 0.12, soft=0.6, wob=0.0005, edge=0.2, var=0.3, seed=45)
            else:
                P.wash(b, sd, SHADOW, 0.55, soft=0.6, wob=0.0005, edge=0.3, var=0.3, seed=46)

    def _paint_wall(self, bx):
        P, c = self.P, self.cam
        b, m = self.region(bx.id, 0)
        if b is not None:
            sd = region_sd(m, P.px)
            v = self.crop(self.hY, b)
            Z = np.where(m, self.crop(self.hZ, b), 50.0)
            su = c.f * abs(bx.X0 - c.Xc) / (Z * Z)
            sv = c.f / Z
            fade = np.exp(-Z / 1200.0)
            P.wash(b, sd, pig('yellow_ochre', 0.5, 'burnt_sienna', 0.2, 'ultramarine', 0.3),
                   0.3 * fade, soft=0.7, wob=0.0008, edge=0.3, var=0.3, var_f=10, seed=51)
            P.wash(b, sd, SHADOW, 0.42 * fade, soft=0.7, wob=0.0008, edge=0.3, var=0.3,
                   var_f=10, seed=52)
            # stone courses
            gz = np.abs(np.mod(self.crop(self.hZ, b), 2.2) - 1.1)
            gv = np.abs(v - 0.5)
            dj = np.minimum((1.1 - gz) * su, gv * sv) - 0.35 * P.px
            lod = smoothstep(2.0, 5.0, 0.5 * sv * P.H)
            P.wash(b, np.maximum(dj, sd), SHADOW, 0.3 * lod * fade, soft=0.6, edge=0.1,
                   var=0.4, var_f=25, dry=0.4, seed=53)
        b, m = self.region(bx.id, 1)
        if b is not None:
            sd = region_sd(m, P.px)
            P.wash(b, sd, pig('yellow_ochre', 0.6, 'rose', 0.4), 0.12, soft=0.6, wob=0.0005,
                   edge=0.3, var=0.3, seed=54)

    # ------------------------------------------------------------ painting: ground
    def road_markings_sd(self, X, Z):
        """Signed distance (screen units) to painted road markings."""
        c = self.cam
        sxs = c.f / Z
        szs = c.f * c.e / (Z * Z)
        d = np.full(X.shape, 1.0, F32)
        for Xl in (-3.0, 3.0):
            dz = np.mod(Z + 2.0, 9.0) - 1.6
            dd = np.maximum((np.abs(X - Xl) - 0.08) * sxs, (np.abs(dz) - 1.5) * szs)
            d = np.minimum(d, np.where(Z > 19.0, dd, 1.0))
        for Xl in (-7.3, 7.3):
            d = np.minimum(d, (np.abs(X - Xl) - 0.06) * sxs)
        # zebra crossing
        fx = np.mod(X + 0.3, 1.2) - 0.6
        dcw = np.maximum((np.abs(fx) - 0.29) * sxs, (np.abs(Z - 16.0) - 2.0) * szs)
        dcw = np.maximum(dcw, (np.abs(X) - 8.5) * sxs)
        d = np.minimum(d, dcw)
        # stop line
        d = np.minimum(d, np.maximum((np.abs(Z - 12.9) - 0.22) * szs, (np.abs(X) - 8.9) * sxs))
        return d

    def puddles(self, X, Z):
        """0..1 puddle coverage on the wet road (world space)."""
        n = fbm(X * 0.3 + 3.0, Z * 0.3, 4, seed=77)
        edge = fbm(X * 1.3, Z * 1.3, 3, seed=78) - 0.5
        near = smoothstep(60.0, 20.0, Z) * smoothstep(8.8, 7.5, np.abs(X))
        n = n * (0.8 + 0.3 * near)
        # the flamingos' pool by the right-hand kerb, and a puddle the fox is
        # splashing through in the foreground
        for (px, pz, rx, rz, lvl) in ((6.6, 12.5, 2.9, 2.3, 0.62), (-1.6, 6.8, 3.4, 1.6, 0.64)):
            pool = lvl - 0.12 * np.hypot((X - px) / rx, (Z - pz) / rz)
            n = np.maximum(n, pool + 0.25 * (n - 0.5) + 0.12 * edge)
        keep = np.maximum(smoothstep(7.5, 11.0, Z), smoothstep(6.0, 3.5, np.abs(X + 1.6)))
        n = n * (0.6 + 0.4 * keep)
        return smoothstep(0.55, 0.58, n + 0.03 * edge) * (Z < 60)

    def paint_ground(self):
        P, c = self.P, self.cam
        b, m = self.region(0)
        if b is None:
            return
        X = self.crop(self.hX, b)
        Z = np.where(m, self.crop(self.hZ, b), 1e4)
        Zs = np.minimum(Z, 5000.0)
        road = m & (X < 13.3)
        grass = m & (X >= 13.3)
        fade = np.exp(-Zs / 2000.0)
        sy = P.grid(b)[1]
        near_h = smoothstep(c.yh - 0.004, c.yh - 0.2, sy)

        if road.any():
            sd = region_sd(road, P.px)
            mk = self.road_markings_sd(X, Zs)
            mcov = smoothstep(0.8 * P.px, -0.8 * P.px, mk)
            pud = self.puddles(X, Zs)
            self.puddle = (b, pud * road)
            reserve = 1.0 - 0.9 * mcov
            dens = (0.20 + 0.14 * near_h) * fade * (1 - 0.75 * pud)
            P.wash(b, sd, ASPHALT, dens * reserve, soft=0.8, wob=0.0012, edge=0.2, var=0.45,
                   var_f=5, var_ax=0.3, var_ay=3.5, gran=0.7, seed=61)
            # tyre-polished lanes: darker streaks along the traffic lanes
            lane = np.cos((X / 3.0) * math.pi * 2.0) * 0.5 + 0.5
            P.wash(b, sd, ASPHALT, 0.10 * lane * near_h * reserve * (1 - pud), soft=2,
                   edge=0.0, var=0.6, var_f=8, var_ax=0.3, var_ay=4.0, dry=0.4, seed=62)
            # golden sheen of the low sun on the wet road towards the horizon
            P.wash(b, sd, GOLD_LIGHT, 0.10 + 0.10 * (1 - near_h), soft=2, edge=0.05, var=0.4,
                   var_f=4, seed=63)
            # the markings themselves: worn, slightly warm paint
            P.wash(b, np.maximum(mk, sd), pig('yellow_ochre', 1.0), 0.05 * fade, soft=0.8,
                   wob=0.0006, edge=0.3, var=0.4, var_f=20, dry=0.5, seed=64)
            # manhole covers
            dm = np.full(X.shape, 1.0, F32)
            for (mx, mz) in ((-1.2, 8.5), (4.8, 24.0), (-5.2, 38.0)):
                dm = np.minimum(dm, (np.hypot((X - mx) / 0.35, (Z - mz) / 0.35) - 1.0)
                                * 0.35 * np.sqrt((c.f / Z) * (c.f * c.e / (Z * Z))))
            P.wash(b, np.maximum(dm, sd), pig('indigo', 0.5, 'burnt_umber', 0.5), 0.55,
                   soft=0.7, wob=0.0005, edge=0.4, var=0.3, seed=65)
        if grass.any():
            sd = region_sd(grass, P.px)
            P.wash(b, sd, GRASS, 0.5 * fade, soft=1.0, wob=0.0015, edge=0.25, var=0.35, var_f=8,
                   seed=66)
            P.wash(b, sd, GOLD_LIGHT, 0.12, soft=2, edge=0.05, var=0.4, var_f=6, seed=67)

    # ------------------------------------------------------------ reflections
    def trace_mirror(self, sx, sy):
        """Depth of the first hit of the ray reflected in the (flat, wet)
        ground, for 1-D arrays of pixel coordinates. Uses the mirrored camera
        at height -e; inf where the reflection sees sky."""
        c = self.cam
        dx, dy = c.ray(sx, sy)
        dym = -dy
        oy = -c.e
        best = np.full(sx.shape, np.inf, F32)
        dxs = np.where(np.abs(dx) < 1e-7, 1e-7, dx)
        dys = np.where(np.abs(dym) < 1e-7, 1e-7, dym)
        for bx in self.boxes:
            pts = np.array(list(bx.corners()))
            msx, msy = c.project(pts[:, 0], -pts[:, 1], pts[:, 2])
            cand = ((sx >= msx.min() - 0.002) & (sx <= msx.max() + 0.002)
                    & (sy >= msy.min() - 0.002) & (sy <= msy.max() + 0.002))
            if not cand.any():
                continue
            idx = np.nonzero(cand)[0]
            ddx, ddy = dxs[idx], dys[idx]
            ta = (bx.X0 - c.Xc) / ddx
            tb = (bx.X1 - c.Xc) / ddx
            txn, txf = np.minimum(ta, tb), np.maximum(ta, tb)
            ta = (bx.Y0 - oy) / ddy
            tb = (bx.Y1 - oy) / ddy
            tyn, tyf = np.minimum(ta, tb), np.maximum(ta, tb)
            tn = np.maximum(np.maximum(txn, tyn), bx.Z0)
            tf = np.minimum(np.minimum(txf, tyf), bx.Z1)
            hit = (tn <= tf) & (tn > NEAR) & (tn < best[idx])
            best[idx[hit]] = tn[hit]
        for sp in self.sprites:
            Xa, Xb, Ya, Yb = sp.bounds()
            X = c.Xc + dx * sp.Z
            Y = oy + dym * sp.Z
            inb = (X > Xa) & (X < Xb) & (Y > Ya) & (Y < Yb) & (sp.Z < best)
            if not inb.any():
                continue
            idx = np.nonzero(inb)[0]
            d = sp.sdf(X[idx], Y[idx])
            best[idx[d < 0]] = sp.Z
        return best

    def paint_reflections(self):
        """Puddles mirror the painted scene; the whole wet road carries a
        faint streaky sheen. The reflected colour is fetched from the canvas
        where the mirrored point is seen directly (same column, shifted row),
        smeared vertically like wet-into-wet strokes."""
        if not hasattr(self, 'puddle'):
            return
        P, c = self.P, self.cam
        b, pud = self.puddle
        y0, y1, x0, x1 = b
        sx, sy = P.grid(b)
        Z = self.crop(self.hZ, b)
        road = (self.crop(self.oid, b) == 0) & (self.crop(self.hX, b) < 13.3)
        sheen = road * (0.05 + 0.16 * smoothstep(12.0, 120.0, Z))
        # wobbly puddle outline painted wet: soft, slightly irregular
        wob = P.snoise(b, 70.0, 3, seed=701, signed=True)
        pcov = smoothstep(0.35, 0.65, pud + 0.06 * wob) * road
        alpha = np.maximum(pcov * 0.74, sheen)
        sel = alpha > 0.01
        if not sel.any():
            return
        ii, jj = np.nonzero(sel)
        psx, psy = sx[ii, jj], sy[ii, jj]
        t = self.trace_mirror(psx, psy)
        ydir = 2 * c.yh - psy - np.where(np.isfinite(t), 2 * c.e * c.f / np.where(np.isfinite(t), t, 1.0), 0.0)
        rows = P.H * 0.5 - 0.5 - ydir * P.H
        # ripples: small horizontal shifts that vary quickly with height
        rip = P.snoise(b, 5.0, 3, seed=702, ax=0.8, ay=7.0, signed=True)[ii, jj]
        cols = jj + x0 + rip * (0.6 * P.H / 800.0)
        # vertical smear, longer for things further away, like a loaded brush
        # dragged down through wet paint
        streak = (0.010 + 0.022 * smoothstep(0.0, 1.0, (np.minimum(t, 400.0) / 400.0))) * P.H
        acc = np.zeros((len(ii), 3), F32)
        R0 = P.R.copy()
        n = 11
        wsum = 0.0
        for k in range(n):
            f = k / (n - 1)
            w = 1.0 - 0.6 * f
            rr = np.clip(np.round(rows - streak * (f - 0.25)), 0, P.H - 1).astype(np.int64)
            for dc in (-1.5, 1.5):
                cc = np.clip(np.round(cols + dc * P.H / 800.0), 0, P.W - 1).astype(np.int64)
                acc += w * R0[rr, cc]
                wsum += w
        refl = acc / wsum
        # water darkens and cools what it mirrors
        refl = refl * np.array([0.83, 0.85, 0.91], F32)
        a = alpha[ii, jj][:, None]
        cur = P.R[ii + y0, jj + x0]
        P.R[ii + y0, jj + x0] = cur * (1 - a) + refl * a
        # a darker rim where the puddle meets the road
        rim = np.exp(-np.abs(pud + 0.06 * wob - 0.5) / 0.05) * road * (pud > 0.2)
        P.wash(b, None, pig('ultramarine', 0.5, 'burnt_umber', 0.5), 0.18 * rim, var=0.4,
               var_f=30, gran=0.6, seed=703)

    # ------------------------------------------------------------ finishing
    def vignette(self):
        """The painting stops short of the sheet edges with a loose, irregular
        border; the foreground is left loosely brushed and fades to paper."""
        P = self.P
        b = P.full()
        sx, sy = P.grid(b)
        half_w = 0.5 * P.W / P.H
        n = P.snoise(b, 5.0, 4, seed=1201, signed=True)
        n2 = P.snoise(b, 22.0, 2, seed=1202, signed=True)
        dl = sx + half_w
        dr = half_w - sx
        dt = 0.5 - sy
        db = sy + 0.5
        # horizontal brush strokes decide where the bottom edge ends
        nb = P.snoise(b, 3.0, 4, seed=1203, ax=0.25, ay=6.0, signed=True)
        m = np.minimum.reduce([
            smoothstep(0.004, 0.035, dl + 0.010 * n + 0.003 * n2),
            smoothstep(0.004, 0.035, dr + 0.010 * n + 0.003 * n2),
            smoothstep(0.004, 0.030, dt + 0.008 * n + 0.003 * n2),
            smoothstep(0.010, 0.10, db + 0.018 * nb + 0.004 * n2),
        ])
        paper = np.array(P.PAPER_RGB, F32)
        ratio = np.clip(P.R / paper, 1e-4, 1.0)
        # a touch of edge darkening where the wash ends
        rim = 1.0 + 0.35 * np.exp(-np.abs(m - 0.5) / 0.12) * (m > 0.02)
        P.R = paper * ratio ** (m * rim)[..., None]

    def paper_grain(self):
        """Raking light over the paper tooth and the cockled sheet."""
        P = self.P
        h = P.paper + 3.0 * (P.cockle - 0.5)
        gy, gx = np.gradient(h)
        shade = -(gx * 0.7 + gy * 0.7)
        P.R *= (1.0 + 0.10 * np.clip(shade, -0.5, 0.5))[..., None]
        P.R *= (0.985 + 0.03 * P.paper)[..., None]

    def pencil(self):
        """Faint graphite under-drawing in the urban-sketch manner: the
        architecture's outlines (found per pixel where the G-buffer changes
        object or face) plus construction lines that overshoot the corners."""
        P, c = self.P, self.cam
        key = self.oid.astype(np.int64) * 4 + self.face
        box_ids = np.array([bx.id for bx in self.boxes if bx.kind == 'building'])
        isbox = np.isin(self.oid, box_ids)
        e = np.zeros(key.shape, bool)
        dh = key[:, 1:] != key[:, :-1]
        dv = key[1:, :] != key[:-1, :]
        bh = isbox[:, 1:] | isbox[:, :-1]
        bv = isbox[1:, :] | isbox[:-1, :]
        e[:, 1:] |= dh & bh
        e[1:, :] |= dv & bv
        # construction lines: verticals overshooting the rooflines and
        # rooflines running on towards the vanishing point
        rng = random.Random(77)
        segs = []
        for bx in self.boxes:
            if bx.kind != 'building' or bx.X1 != -15.0 or bx.Z0 > 260 or bx.Z1 < 5:
                continue
            z0 = max(bx.Z0, 3.0)
            ax, ay = c.project(bx.X1, bx.Y1, z0)
            fx, fy = c.project(bx.X1, bx.Y1, bx.Z1)
            gx, gy = c.project(bx.X1, 0.0, z0)
            over = rng.uniform(0.008, 0.03)
            segs.append((gx, gy, ax, ay + over))
            t = rng.uniform(0.05, 0.3)
            segs.append((ax, ay, fx + (c.xv - fx) * t, fy + (c.yh - fy) * t))
        segs.append((c.xv - 0.25, c.yh, c.xv + 0.35, c.yh))
        b = P.full()
        sx, sy = P.grid(b)
        dist = ndimage.distance_transform_edt(~e) * P.px
        for (x0, y0, x1, y1) in segs:
            bb = P.bbox_from_screen([x0, x1], [y0, y1], pad=0.003)
            if bb is None:
                continue
            gx_, gy_ = P.grid(bb)
            vx, vy = x1 - x0, y1 - y0
            L2 = vx * vx + vy * vy + 1e-12
            h = np.clip(((gx_ - x0) * vx + (gy_ - y0) * vy) / L2, 0, 1)
            d = np.hypot(gx_ - x0 - vx * h, gy_ - y0 - vy * h)
            sub = dist[bb[0]:bb[1], bb[2]:bb[3]]
            np.minimum(sub, d, out=sub)
        wob = 0.0006 * P.snoise(b, 60.0, 2, seed=801, signed=True)
        line = smoothstep(0.9 * P.px, 0.25 * P.px, dist + wob)
        broken = smoothstep(0.35, 0.6, P.snoise(b, 40.0, 3, seed=802))
        graphite = pig('lamp_black', 0.6, 'paynes', 0.4)
        P.wash(b, None, graphite, 0.22 * line * broken, var=0.4, var_f=30, gran=0.0,
               seed=803)

    def splatter(self):
        """Flicked drops of pigment and a couple of water blooms."""
        P = self.P
        rng = random.Random(1234)
        pigs = [pig('sepia', 1.0), pig('burnt_sienna', 1.0), pig('ultramarine', 1.0),
                pig('quin_gold', 1.0), pig('rose', 0.6, 'ultramarine', 0.4)]
        for i in range(70):
            cx = rng.uniform(-0.72, 0.72)
            cy = rng.uniform(-0.48, -0.15) if rng.random() < 0.7 else rng.uniform(-0.15, 0.45)
            big = rng.random() < 0.12
            r = rng.uniform(0.002, 0.005) if big else rng.uniform(0.0004, 0.0016)
            b = P.bbox_from_screen([cx - 3 * r, cx + 3 * r], [cy - 3 * r, cy + 3 * r])
            if b is None:
                continue
            gx, gy = P.grid(b)
            d = np.hypot(gx - cx, (gy - cy) * rng.uniform(0.8, 1.2)) - r
            P.wash(b, d, rng.choice(pigs), rng.uniform(0.15, 0.5), soft=0.7,
                   wob=r * 0.25, wob_f=1.0 / (r * 1.5), edge=0.8, edge_w=r * 0.4, var=0.2,
                   seed=900 + i)

    def finish(self):
        self.paint_reflections()
        self.pencil()
        self.splatter()
        self.vignette()
        self.paper_grain()
