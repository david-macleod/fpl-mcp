"""
The wild animals.

Every animal is a Sprite built from 2-D signed distance parts in local metres
(side view, facing +x, feet on y = 0). Per pixel we evaluate the parts on the
animal's plane, then paint:

  * a local-colour wash over the visible silhouette,
  * species patterns computed per pixel (Voronoi giraffe patches, zebra
    stripes from a blended, noise-warped cosine phase field, ...),
  * form shading from a pseudo-3-D normal obtained by "inflating" the SDF
    (sphere profile) and lighting it from the low sun on the right,
  * contour strokes where limbs overlap the body, eyes, hooves and a contact
    shadow where the feet meet the ground.
"""
import math
import random
from types import SimpleNamespace

import numpy as np

from wc_core import (F32, smoothstep, fbm, voronoi_border, sd_circle, sd_ellipse, sd_seg,
                     sd_tri, sd_curve, smin, pig, region_sd)
from wc_city import Sprite, SHADOW, GOLD_LIGHT, HAZE

DARK = pig('sepia', 0.45, 'indigo', 0.35, 'lamp_black', 0.2)
BLACK = pig('lamp_black', 0.5, 'indigo', 0.3, 'sepia', 0.2)
COOL = pig('ultramarine', 0.5, 'rose', 0.3, 'cerulean', 0.2)
WARM_BOUNCE = pig('raw_sienna', 0.6, 'rose', 0.4)


def legs(x, y, joints, radii, k=0.04):
    """A limb as a chain of tapered capsules through ``joints``."""
    d = None
    for i in range(len(joints) - 1):
        (ax, ay), (bx, by) = joints[i], joints[i + 1]
        di = sd_seg(x, y, ax, ay, bx, by, radii[i], radii[i + 1])
        d = di if d is None else smin(d, di, k)
    return d


def union(*ds):
    out = ds[0]
    for d in ds[1:]:
        out = np.minimum(out, d)
    return out


class Animal(Sprite):
    kind = 'animal'
    R = 0.8                 # roundness used to inflate the silhouette (metres)
    wf = 0.3                # edge-wobble feature size (metres)
    lb = (-1.0, 1.0, 0.0, 2.0)
    planes = (0.0,)
    near_parts = ()
    far_parts = ()

    def __init__(self, X, Z, facing=1, s=1.0, Y0=0.0, seed=0, **pose):
        self.X, self.Z, self.facing, self.s, self.Y0 = X, Z, facing, s, Y0
        self.seed = seed
        self.pose = pose
        self.shadow_planes = tuple(p * s for p in self.planes)

    # geometry -------------------------------------------------------------
    def local(self, X, Y):
        return (X - self.X) * self.facing / self.s, (Y - self.Y0) / self.s

    def to_world(self, x, y):
        return self.X + self.facing * x * self.s, self.Y0 + y * self.s

    def bounds(self):
        x0, x1, y0, y1 = self.lb
        xs = (self.X + self.facing * x0 * self.s, self.X + self.facing * x1 * self.s)
        return (min(xs), max(xs), self.Y0 + y0 * self.s, self.Y0 + y1 * self.s)

    def parts(self, x, y):
        raise NotImplementedError

    def combine(self, p):
        return union(*p.values())

    def sdf(self, X, Y):
        x, y = self.local(X, Y)
        return self.combine(self.parts(x, y)) * self.s

    # painting helpers ----------------------------------------------------
    def begin(self, S):
        P, cam = S.P, S.cam
        b, m = S.region(self.id)
        if b is None:
            return None
        sx, sy = P.grid(b)
        X, Y = cam.on_plane(sx, sy, self.Z)
        x, y = self.local(X, Y)
        k = cam.f * self.s / self.Z
        vis = region_sd(m, P.px)
        parts = self.parts(x, y)
        body = self.combine(parts)
        sil = np.maximum(body * k, vis)
        # pseudo normal: SDF gradient (screen orientation) + sphere profile
        gy, gx = np.gradient(body)
        gX, gY = gx, -gy
        g = np.hypot(gX, gY) + 1e-9
        delta = np.clip(-body / self.R, 0.0, 1.0)
        rim = 1.0 - delta
        nz = -np.sqrt(np.clip(1.0 - rim * rim, 0.0, 1.0))
        L = S.form_light
        lam = np.clip(gX / g * rim * L[0] + gY / g * rim * L[1] + nz * L[2], 0.0, 1.0)
        ny = gY / g * rim
        fade = math.exp(-self.Z / 1200.0)
        haze = 1.0 - math.exp(-self.Z / 300.0)
        return SimpleNamespace(P=P, S=S, b=b, m=m, x=x, y=y, k=k, vis=vis, parts=parts,
                               body=body, sil=sil, lam=lam, ny=ny, fade=fade, haze=haze,
                               seed=self.seed * 97 + self.id * 13)

    def clip(self, c, d_local):
        return np.maximum(d_local * c.k, c.sil)

    def wash(self, c, sd, pg, dens, seed_off, wob=1.0, **kw):
        """Animal wash: edge wobble scales with the animal (``self.wf`` is the
        wobble feature size in local metres; ``wob`` is a relative amount)."""
        kw.setdefault('soft', 0.9)
        kw.setdefault('edge', 0.35)
        kw.setdefault('var', 0.28)
        kw.setdefault('var_f', 30.0)
        kw.setdefault('wob_f', 1.0 / (self.wf * c.k))
        amp = min(0.12 * self.wf * c.k, 0.0022) * wob
        c.P.wash(c.b, sd, pg, dens * c.fade, wob=amp, seed=c.seed + seed_off, **kw)

    def mask_sd(self, c, mask):
        """Screen SDF of a boolean mask, clipped to the silhouette."""
        if not mask.any():
            return None
        return np.maximum(region_sd(mask, c.P.px), c.sil)

    def far_mask(self, c):
        if not self.far_parts:
            return np.zeros(c.sil.shape, bool)
        far = union(*[c.parts[n] for n in self.far_parts])
        near = union(*[v for n, v in c.parts.items() if n not in self.far_parts])
        return (far < 0) & (near > 0.012) & (c.sil < 0)

    def form(self, c, dens_mid, dens_deep, pg=SHADOW, t_mid=0.40, t_deep=0.14, soft=1.6):
        far = self.far_mask(c)
        mid = ((c.lam < t_mid) | far) & (c.sil < 0)
        sd = self.mask_sd(c, mid)
        if sd is not None:
            self.wash(c, sd, pg, dens_mid, 21, soft=soft, edge=0.3, var=0.3, var_f=18)
        deep = ((c.lam < t_deep) | far) & (c.sil < 0)
        sd = self.mask_sd(c, deep)
        if sd is not None:
            self.wash(c, sd, pg, dens_deep, 22, soft=soft * 0.8, edge=0.4, var=0.3,
                      var_f=22)
        # warm light bounced up from the sunny street into the undersides
        bounce = smoothstep(-0.2, -0.8, c.ny)
        self.wash(c, c.sil, WARM_BOUNCE, 0.12 * bounce, 23, soft=2.0, edge=0.0, var=0.3)

    def contour(self, c, d_local, width=0.018, dens=0.35, pg=DARK, inside=None, off=31,
                ymax=None):
        """Thin line along a part's outline where it overlaps the body (only
        below ``ymax``: the crease where a limb leaves the belly)."""
        d = np.abs(d_local) - width
        if ymax is not None:
            d = np.maximum(d, c.y - ymax)
        sd = self.clip(c, d)
        if inside is not None:
            sd = np.maximum(sd, inside * c.k)
        self.wash(c, sd, pg, dens, off, wob=0.27, soft=0.7, edge=0.2, var=0.4,
                  var_f=40, dry=0.45)

    def glint(self, c, dens, mask=1.0):
        """Warm rim light where the low sun grazes the silhouette."""
        rim = smoothstep(-0.16 * self.R, -0.01, c.body)
        top = smoothstep(0.62, 0.92, c.lam) * rim * mask
        self.wash(c, c.sil, GOLD_LIGHT, dens * top, 14, soft=1.5, edge=0.0)

    def finish_haze(self, c):
        if c.haze > 0.02:
            self.wash(c, c.sil, HAZE, 0.5 * c.haze / max(c.fade, 1e-3), 41, soft=1.2, edge=0.0,
                      var=0.2)

    def feet(self):
        """World (X, Y) of the feet, used for contact shadows."""
        return []

    def contact_shadows(self, S):
        P, cam = S.P, S.cam
        for (fx, fy) in self.feet():
            X, Y = self.to_world(fx, fy)
            rx = 0.35 * self.s
            cx, cy = cam.project(X, Y, self.Z)
            k = cam.f / self.Z
            hb = P.bbox_from_screen([cx - 2.5 * rx * k, cx + 2.5 * rx * k],
                                    [cy - rx * k, cy + rx * k], pad=0.002)
            if hb is None:
                continue
            gx, gy = P.grid(hb)
            oid = S.crop(S.oid, hb)
            recv = (oid >= 0) & (oid != self.id)
            dd = np.hypot((gx - cx) / (2.2 * rx * k), (gy - cy) / (0.55 * rx * k))
            P.wash(hb, None, SHADOW, 0.55 * np.exp(-dd * dd * 2.0) * recv, var=0.3,
                   var_f=40, gran=0.5, seed=self.id * 7 + 1)


# ==========================================================================
class Elephant(Animal):
    R = 1.0
    wf = 0.35
    lb = (-2.7, 3.4, -0.05, 3.55)
    planes = (-0.7, 0.0, 0.7)
    far_parts = ('leg_ff', 'leg_hf')

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, -0.15, 2.28, 1.72, 0.9, -0.05)
        body = smin(body, sd_circle(x, y, 0.95, 2.42, 0.9), 0.4)
        body = smin(body, sd_circle(x, y, -1.3, 2.25, 0.84), 0.4)
        body = smin(body, sd_ellipse(x, y, 0.0, 1.76, 1.2, 0.45), 0.3)
        p['body'] = body
        head = sd_circle(x, y, 1.98, 2.62, 0.74)
        head = smin(head, sd_circle(x, y, 2.2, 2.98, 0.42), 0.2)
        head = smin(head, sd_ellipse(x, y, 2.1, 2.12, 0.46, 0.42), 0.2)
        p['head'] = head
        p['trunk'] = sd_curve(x, y, [(2.45, 2.5), (3.02, 1.9), (2.8, 0.95), (3.02, 0.36),
                                     (3.22, 0.44)], 0.31, 0.1, n=14, k=0.05)
        p['tusk'] = sd_curve(x, y, [(2.38, 2.0), (2.8, 1.66), (3.14, 1.6), (3.28, 1.78)],
                             0.09, 0.03, n=8)
        ear = sd_ellipse(x, y, 1.42, 2.52, 0.72, 0.96, 0.12)
        ear = smin(ear, sd_circle(x, y, 1.55, 1.7, 0.38), 0.3)
        p['ear'] = ear
        ph = self.pose.get('phase', 0.0)
        sw = 0.18 * math.sin(ph)
        p['leg_fn'] = legs(x, y, [(1.15, 2.1), (1.22 + sw, 1.0), (1.15 + 2 * sw, 0.12)],
                           [0.42, 0.33, 0.3], 0.1)
        p['leg_ff'] = legs(x, y, [(0.72, 2.1), (0.55 - sw, 1.0), (0.42 - 2 * sw, 0.14)],
                           [0.4, 0.32, 0.29], 0.1)
        p['leg_hn'] = legs(x, y, [(-1.35, 2.1), (-1.2 - sw, 1.0), (-1.32 - 2 * sw, 0.12)],
                           [0.44, 0.33, 0.3], 0.1)
        p['leg_hf'] = legs(x, y, [(-0.98, 2.1), (-0.75 + sw, 1.0), (-0.62 + 2 * sw, 0.14)],
                           [0.42, 0.32, 0.29], 0.1)
        for n, fx in (('leg_fn', 1.15 + 2 * sw), ('leg_ff', 0.42 - 2 * sw),
                      ('leg_hn', -1.32 - 2 * sw), ('leg_hf', -0.62 + 2 * sw)):
            fy = 0.14 if n.endswith('f') else 0.12
            foot = np.maximum(sd_ellipse(x, y, fx, fy + 0.02, 0.37, 0.16), fy - 0.13 - y)
            p[n] = smin(p[n], foot, 0.06)
        tail = sd_curve(x, y, [(-2.15, 2.72), (-2.42, 2.15), (-2.36, 1.45)], 0.07, 0.035, n=6)
        p['tail'] = smin(tail, sd_ellipse(x, y, -2.36, 1.37, 0.06, 0.13), 0.03)
        return p

    def combine(self, p):
        d = smin(p['body'], p['head'], 0.35)
        d = smin(d, p['trunk'], 0.12)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.12)
        d = smin(d, p['tail'], 0.05)
        return union(d, p['ear'], p['tusk'])

    def feet(self):
        ph = self.pose.get('phase', 0.0)
        sw = 0.18 * math.sin(ph)
        return [(1.15 + 2 * sw, 0.0), (0.42 - 2 * sw, 0.02), (-1.32 - 2 * sw, 0.0),
                (-0.62 + 2 * sw, 0.02)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p = c.parts
        tusk = p['tusk']
        ear = p['ear']
        away = -0.09 * self.facing   # local x offset pointing away from the sun
        # the ear flap faces us: treat it as flat for the form shading
        inear = smoothstep(0.0, -0.08, ear)
        c.lam = c.lam * (1 - inear) + (0.5 + 0.12 * (c.y - 2.5)) * inear
        grey = pig('ultramarine', 0.42, 'burnt_sienna', 0.43, 'rose', 0.15)
        base = np.maximum(c.sil, -tusk * c.k)
        self.wash(c, base, grey, 0.36, 1, edge=0.45, var=0.15, var_f=10, gran=0.95)
        # dust on the legs and belly
        dust = smoothstep(1.4, 0.3, c.y)
        self.wash(c, base, pig('raw_sienna', 0.5, 'burnt_sienna', 0.5), 0.22 * dust, 2,
                  soft=2.5, edge=0.05, var=0.3, var_f=12)
        shadow_pg = pig('ultramarine', 0.5, 'burnt_sienna', 0.3, 'alizarin', 0.2)
        far = self.far_mask(c)
        mid = ((c.lam < 0.4) | far) & (c.sil < 0)
        sd = self.mask_sd(c, mid)
        if sd is not None:
            self.wash(c, sd, shadow_pg, 0.3, 21, soft=2.5, edge=0.3, var=0.25, var_f=14)
        sd = self.mask_sd(c, far & (c.sil < 0))
        if sd is not None:
            self.wash(c, sd, shadow_pg, 0.25, 22, soft=1.2, edge=0.35, var=0.25)
        bounce = smoothstep(-0.2, -0.8, c.ny)
        self.wash(c, c.sil, WARM_BOUNCE, 0.12 * bounce, 23, soft=2.0, edge=0.0, var=0.3)
        # the great ear: a shadow cast onto neck and cheek, then the flap itself
        # with a strongly darkened rim and a few folds
        ear_sh = sd_ellipse(c.x, c.y, 1.42 + away, 2.4, 0.74, 0.98, 0.12)
        ear_sh = smin(ear_sh, sd_circle(c.x, c.y, 1.55 + away, 1.58, 0.38), 0.3)
        self.wash(c, self.clip(c, np.maximum(ear_sh, -ear)), shadow_pg, 0.55, 3, soft=1.0,
                  edge=0.35)
        self.wash(c, self.clip(c, ear), grey, 0.12, 4, soft=0.8, edge=1.6, edge_w=0.0016,
                  var=0.3)
        folds = union(np.abs(sd_ellipse(c.x, c.y, 1.5, 2.45, 0.46, 0.66, 0.12)) - 0.018,
                      np.abs(sd_ellipse(c.x, c.y, 1.62, 2.2, 0.3, 0.42, 0.3)) - 0.015)
        folds = np.maximum(folds, np.abs(c.x - 1.6) - 0.5)
        self.wash(c, self.clip(c, np.maximum(folds, ear + 0.05)), shadow_pg, 0.3, 5,
                  soft=0.8, edge=0.2, dry=0.5, var=0.5)
        # skin: wrinkle rings on the trunk and knees
        tr = p['trunk']
        wr = np.abs(np.mod(c.y * 9.0 + 0.6 * fbm(c.x * 4, c.y * 4, 2, 7), 1.0) - 0.5) - 0.38
        self.wash(c, self.clip(c, np.maximum(-wr * 0.05, tr)), DARK, 0.2, 6, soft=0.8,
                  edge=0.1, dry=0.4, var=0.5)
        knees = np.abs(np.mod(c.y * 7.0 + 0.4 * fbm(c.x * 5, c.y * 5, 2, 8), 1.0) - 0.5) - 0.4
        legs_all = union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf'])
        self.wash(c, self.clip(c, np.maximum(-knees * 0.05, np.maximum(legs_all, c.y - 1.2))),
                  DARK, 0.16, 8, soft=0.8, edge=0.1, dry=0.5, var=0.5)
        # where the trunk leaves the face
        crease = np.maximum(np.abs(p['trunk'] + 0.02) - 0.02, np.abs(c.y - 2.1) - 0.35)
        crease = np.maximum(crease, -p['head'] - 0.02)
        self.wash(c, self.clip(c, crease), DARK, 0.25, 15, soft=0.8, edge=0.2, dry=0.4)
        # tusk: ivory, left mostly as paper
        self.wash(c, self.clip(c, tusk), pig('yellow_ochre', 0.6, 'burnt_umber', 0.4), 0.14, 9,
                  wob=0.22, soft=0.7, edge=0.6)
        # eye, mouth, tail tuft
        eye = sd_ellipse(c.x, c.y, 2.26, 2.7, 0.075, 0.045, -0.2)
        self.wash(c, self.clip(c, eye), BLACK, 0.95, 10, wob=0.11, soft=0.6, edge=0.2)
        brow = np.abs(sd_ellipse(c.x, c.y, 2.23, 2.67, 0.16, 0.1, -0.2)) - 0.012
        self.wash(c, self.clip(c, np.maximum(brow, 2.67 - c.y)), DARK, 0.4, 11, wob=0.11,
                  soft=0.6, edge=0.1)
        mouth = sd_ellipse(c.x, c.y, 2.32, 1.92, 0.14, 0.05, -0.3)
        self.wash(c, self.clip(c, mouth), DARK, 0.5, 12, wob=0.22, soft=0.7)
        tuft = sd_ellipse(c.x, c.y, -2.36, 1.37, 0.07, 0.14)
        self.wash(c, self.clip(c, tuft), BLACK, 0.8, 13, wob=0.22, soft=0.6)
        # a warm glint of low sun along the back and the top of the head
        self.glint(c, 0.35, 1 - inear)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Giraffe(Animal):
    R = 0.45
    wf = 0.22
    lb = (-1.55, 2.7, -0.05, 5.6)
    planes = (-0.35, 0.0, 0.35)
    far_parts = ('leg_ff', 'leg_hf')

    def neck_ctrl(self):
        reach = self.pose.get('reach', 0.0)   # 0 upright, 1 browsing forward
        tip = (1.72 + 0.55 * reach, 4.85 + 0.25 * reach)
        return [(0.72, 2.75), (1.1 + 0.2 * reach, 3.75), (1.45 + 0.45 * reach, 4.45 + 0.1 * reach),
                tip]

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, 0.0, 2.55, 1.12, 0.5, 0.16)
        body = smin(body, sd_circle(x, y, 0.78, 2.62, 0.47), 0.25)
        body = smin(body, sd_circle(x, y, -0.86, 2.42, 0.43), 0.25)
        body = smin(body, sd_circle(x, y, 0.52, 2.95, 0.33), 0.2)
        p['body'] = body
        nc = self.neck_ctrl()
        p['neck'] = sd_curve(x, y, nc, 0.36, 0.15, n=10, k=0.05)
        hx, hy = nc[-1]
        reach = self.pose.get('reach', 0.0)
        ang = -0.55 + 0.35 * reach
        ex, ey = hx + 0.56 * math.cos(ang), hy + 0.08 + 0.56 * math.sin(ang)
        head = sd_seg(x, y, hx - 0.02, hy + 0.1, ex, ey, 0.165, 0.1)
        head = smin(head, sd_ellipse(x, y, hx + 0.2, hy - 0.02, 0.2, 0.13, ang), 0.08)
        p['head'] = head
        self._head = (hx, hy, ex, ey)
        ox = hx - 0.07
        oy = hy + 0.2
        p['ossicone'] = union(sd_seg(x, y, ox, oy, ox - 0.06, oy + 0.24, 0.036, 0.03),
                              sd_circle(x, y, ox - 0.06, oy + 0.25, 0.05),
                              sd_seg(x, y, ox + 0.09, oy, ox + 0.05, oy + 0.22, 0.034, 0.03),
                              sd_circle(x, y, ox + 0.05, oy + 0.23, 0.048))
        p['ear'] = sd_ellipse(x, y, hx - 0.2, hy + 0.12, 0.16, 0.05, 0.45)
        ph = self.pose.get('phase', 0.0)
        sw = 0.12 * math.sin(ph)
        p['leg_fn'] = legs(x, y, [(0.85, 2.35), (0.9 + sw, 1.15), (0.86 + 2 * sw, 0.1)],
                           [0.15, 0.1, 0.08], 0.05)
        p['leg_ff'] = legs(x, y, [(0.62, 2.35), (0.66 - sw, 1.15), (0.6 - 2.5 * sw, 0.12)],
                           [0.14, 0.095, 0.075], 0.05)
        p['leg_hn'] = legs(x, y, [(-0.95, 2.4), (-0.7, 1.72), (-1.08 - sw, 1.05),
                                  (-0.98 - 2 * sw, 0.1)], [0.22, 0.14, 0.09, 0.075], 0.05)
        p['leg_hf'] = legs(x, y, [(-0.75, 2.4), (-0.5, 1.72), (-0.84 + sw, 1.05),
                                  (-0.7 + 2 * sw, 0.12)], [0.2, 0.13, 0.085, 0.07], 0.05)
        tail = sd_curve(x, y, [(-1.18, 2.72), (-1.32, 2.2), (-1.3, 1.6)], 0.035, 0.022, n=6)
        p['tail'] = smin(tail, sd_ellipse(x, y, -1.3, 1.5, 0.06, 0.14), 0.03)
        return p

    def combine(self, p):
        d = smin(p['body'], p['neck'], 0.18)
        d = smin(d, p['head'], 0.06)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.08)
        return union(d, p['tail'], p['ossicone'], p['ear'])

    def feet(self):
        ph = self.pose.get('phase', 0.0)
        sw = 0.12 * math.sin(ph)
        return [(0.86 + 2 * sw, 0.0), (0.6 - 2.5 * sw, 0.02), (-0.98 - 2 * sw, 0.0),
                (-0.7 + 2 * sw, 0.02)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        self.wash(c, c.sil, pig('yellow_ochre', 0.75, 'raw_sienna', 0.25), 0.36, 1, edge=0.4,
                  var=0.3, var_f=16)
        # reticulated patches: Voronoi cells, smaller on the neck, fading
        # out down the legs; the pale network is left as the first wash
        neckish = smoothstep(-0.05, -0.25, p['neck']) * smoothstep(2.9, 3.3, y)
        bd1, h1 = voronoi_border(x * 3.3 + 11.0, y * 3.3, seed=self.seed + 5)
        bd2, h2 = voronoi_border(x * 5.0 + 3.0, y * 5.0, seed=self.seed + 6)
        d1 = (0.075 - bd1) / 3.3
        d2 = (0.08 - bd2) / 5.0
        dpat = d1 * (1 - neckish) + d2 * neckish
        legfade = smoothstep(0.9, 1.9, y)
        dpat = dpat + (1 - legfade) * 0.2
        dpat = np.maximum(dpat, -p['head'] - 0.03)
        patch_d = 0.72 * (0.85 + 0.3 * ((h1 & 255).astype(F32) / 255.0))
        self.wash(c, self.clip(c, dpat), pig('burnt_sienna', 0.72, 'alizarin', 0.1,
                                             'burnt_umber', 0.18),
                  patch_d, 2, wob=0.27, soft=0.8, edge=0.45, edge_w=0.0012, var=0.3,
                  var_f=40, fine=0.5)
        self.form(c, 0.38, 0.36, t_mid=0.42, t_deep=0.15)
        # mane, face, ossicones, hooves, tail tuft, eye
        nc = self.neck_ctrl()
        mane_pts = [(px - 0.12, py + 0.1) for (px, py) in nc[:-1]] + [(nc[-1][0] - 0.12,
                                                                       nc[-1][1] + 0.05)]
        mane = np.maximum(sd_curve(x, y, mane_pts, 0.07, 0.05, n=8), -p['neck'] - 0.3)
        self.wash(c, self.clip(c, mane), pig('burnt_umber', 0.7, 'sepia', 0.3), 0.55, 3,
                  wob=0.22, soft=0.8, edge=0.3, dry=0.5)
        hx, hy, ex, ey = self._head
        eye = sd_circle(x, y, hx + 0.06, hy + 0.1, 0.045)
        self.wash(c, self.clip(c, eye), BLACK, 0.9, 4, wob=0.09, soft=0.6)
        muzzle = sd_circle(x, y, ex, ey, 0.1)
        self.wash(c, self.clip(c, np.maximum(muzzle, p['head'])), DARK, 0.3, 5, wob=0.13,
                  soft=1.0, edge=0.2)
        tips = union(sd_circle(x, y, hx - 0.13, hy + 0.45, 0.055),
                     sd_circle(x, y, hx - 0.02, hy + 0.43, 0.052))
        self.wash(c, self.clip(c, tips), BLACK, 0.85, 6, wob=0.09, soft=0.6)
        hooves = np.maximum(union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf']),
                            y - 0.2)
        self.wash(c, self.clip(c, hooves), DARK, 0.7, 7, wob=0.13, soft=0.7)
        tuft = sd_ellipse(x, y, -1.3, 1.5, 0.06, 0.14)
        self.wash(c, self.clip(c, tuft), BLACK, 0.85, 8, wob=0.18, soft=0.6)
        for n in ('leg_fn', 'leg_hn'):
            self.contour(c, p[n], dens=0.28, inside=p['body'] + 0.03, off=9, ymax=2.25)
        self.glint(c, 0.3)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Zebra(Animal):
    R = 0.34
    wf = 0.14
    lb = (-1.15, 1.5, -0.05, 2.1)
    planes = (-0.28, 0.0, 0.28)
    far_parts = ('leg_ff', 'leg_hf')

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, 0.0, 1.08, 0.86, 0.35)
        body = smin(body, sd_circle(x, y, 0.6, 1.07, 0.33), 0.15)
        body = smin(body, sd_circle(x, y, -0.62, 1.12, 0.35), 0.15)
        p['body'] = body
        drop = self.pose.get('drop', 0.0)     # head lowered
        p['neck'] = sd_curve(x, y, [(0.58, 1.2), (0.82, 1.48 - 0.2 * drop),
                                    (1.0, 1.62 - 0.45 * drop)], 0.3, 0.17, n=6)
        hx, hy = 1.02, 1.7 - 0.45 * drop
        mx, my = 1.34 + 0.05 * drop, 1.22 - 0.4 * drop
        self._head = (hx, hy, mx, my)
        head = sd_seg(x, y, hx, hy, mx, my, 0.15, 0.1)
        p['head'] = smin(head, sd_circle(x, y, hx + 0.08, hy - 0.18, 0.13), 0.06)
        p['ear'] = union(sd_seg(x, y, hx - 0.05, hy + 0.1, hx - 0.13, hy + 0.3, 0.05, 0.012),
                         sd_seg(x, y, hx + 0.01, hy + 0.1, hx - 0.02, hy + 0.3, 0.045, 0.012))
        p['mane'] = sd_curve(x, y, [(0.6, 1.45), (0.84, 1.72 - 0.2 * drop),
                                    (hx - 0.04, hy + 0.14)], 0.07, 0.05, n=6)
        ph = self.pose.get('phase', 0.0)
        a = 0.14 * math.sin(ph)
        p['leg_fn'] = legs(x, y, [(0.6, 0.85), (0.66 + a, 0.46), (0.62 + 2 * a, 0.06)],
                           [0.1, 0.065, 0.055])
        p['leg_ff'] = legs(x, y, [(0.48, 0.85), (0.4 - a, 0.47), (0.32 - 2.2 * a, 0.1)],
                           [0.095, 0.062, 0.052])
        p['leg_hn'] = legs(x, y, [(-0.62, 0.95), (-0.5, 0.62), (-0.72 - a, 0.4),
                                  (-0.66 - 2 * a, 0.05)], [0.15, 0.09, 0.065, 0.055])
        p['leg_hf'] = legs(x, y, [(-0.5, 0.95), (-0.34, 0.62), (-0.5 + a, 0.4),
                                  (-0.4 + 2.2 * a, 0.08)], [0.14, 0.085, 0.062, 0.052])
        tail = sd_curve(x, y, [(-0.95, 1.25), (-1.06, 0.95), (-1.05, 0.62)], 0.035, 0.025, n=5)
        p['tail'] = smin(tail, sd_ellipse(x, y, -1.05, 0.58, 0.045, 0.1), 0.02)
        return p

    def combine(self, p):
        d = smin(p['body'], p['neck'], 0.1)
        d = smin(d, p['head'], 0.05)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.05)
        return union(d, p['tail'], p['ear'], p['mane'])

    def feet(self):
        a = 0.14 * math.sin(self.pose.get('phase', 0.0))
        return [(0.62 + 2 * a, 0.0), (0.32 - 2.2 * a, 0.03), (-0.66 - 2 * a, 0.0),
                (-0.4 + 2.2 * a, 0.02)]

    def stripe_field(self, c):
        """Blend of cosine phase fields: vertical on the barrel, arcs over the
        rump, rings round the legs, bands across the neck and face."""
        x, y, p = c.x, c.y, c.parts
        warp = 0.22 * (fbm(x * 3.0, y * 3.0, 3, self.seed + 9) - 0.5)
        hx, hy, mx, my = self._head
        ux, uy = mx - hx, my - hy
        ul = math.hypot(ux, uy)
        ux, uy = ux / ul, uy / ul
        ph_torso = (x + 0.22 * (y - 1.25) ** 2 * 3.0) * 6.2 + warp * 3
        ph_rump = np.hypot(x + 0.15, y - 0.35) * 7.0 + warp * 3
        ph_leg = y * 13.0 + warp * 2
        nx, ny = 0.72, 0.69
        ph_neck = ((x - 0.58) * nx + (y - 1.2) * ny) * 8.5 + warp * 2
        ph_head = ((x - hx) * ux + (y - hy) * uy) * 15.0 + warp
        w_leg = smoothstep(0.8, 0.62, y)
        w_rump = smoothstep(-0.22, -0.55, x) * (1 - w_leg)
        w_neck = smoothstep(0.52, 0.72, x) * smoothstep(1.05, 1.3, y) * (1 - w_leg)
        w_head = smoothstep(0.0, -0.06, p['head'])
        w_torso = np.clip(1.0 - w_leg - w_rump - w_neck, 0.0, 1.0)
        w_neck = w_neck * (1 - w_head)
        w_torso = w_torso * (1 - w_head)
        w_leg = w_leg * (1 - w_head)
        w_rump = w_rump * (1 - w_head)
        tau = 2.0 * math.pi
        s = (w_torso * np.cos(tau * ph_torso) + w_rump * np.cos(tau * ph_rump)
             + w_leg * np.cos(tau * ph_leg) + w_neck * np.cos(tau * ph_neck)
             + w_head * np.cos(tau * ph_head))
        wsum = w_torso + w_rump + w_leg + w_neck + w_head + 1e-6
        s = s / wsum
        # stripes thin out under the belly and towards the hooves
        thin = 0.25 * smoothstep(0.95, 0.72, y) * (1 - w_leg) + 0.35 * smoothstep(0.3, 0.05, y)
        return s - thin

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        # white coat: cool shadows only, the paper does the rest
        self.wash(c, c.sil, pig('yellow_ochre', 0.7, 'rose', 0.3), 0.07, 1, edge=0.6,
                  var=0.3)
        self.form(c, 0.3, 0.26, pg=COOL, t_mid=0.45, t_deep=0.18)
        s = self.stripe_field(c)
        gy, gx = np.gradient(s)
        g = np.hypot(gx, gy) * c.P.H + 1e-6
        sd = (0.05 - s) / g
        sd = np.maximum(sd, c.sil)
        muzzle = sd_circle(x, y, self._head[2], self._head[3], 0.12)
        sd = np.minimum(sd, self.clip(c, np.maximum(muzzle, p['head'])))
        self.wash(c, sd, pig('paynes', 0.45, 'indigo', 0.35, 'burnt_umber', 0.2), 1.05, 2,
                  wob=0.18, soft=0.7, edge=0.35, edge_w=0.0012, var=0.25, var_f=40,
                  dry=0.25, fine=0.5)
        hx, hy, mx, my = self._head
        eye = sd_circle(x, y, hx + 0.07, hy - 0.02, 0.035)
        self.wash(c, self.clip(c, eye), BLACK, 0.9, 3, wob=0.07, soft=0.6)
        hooves = np.maximum(union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf']),
                            y - 0.1)
        self.wash(c, self.clip(c, hooves), BLACK, 0.85, 4, wob=0.09, soft=0.6)
        tuft = sd_ellipse(x, y, -1.05, 0.58, 0.05, 0.11)
        self.wash(c, self.clip(c, tuft), BLACK, 0.8, 5, wob=0.09, soft=0.6)
        for n in ('leg_fn', 'leg_hn'):
            self.contour(c, p[n], width=0.01, dens=0.3, inside=p['body'] + 0.03, off=6,
                         ymax=0.88)
        self.glint(c, 0.22)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Stag(Animal):
    R = 0.3
    wf = 0.12
    lb = (-1.0, 1.35, -0.05, 2.95)
    planes = (-0.22, 0.0, 0.22)
    far_parts = ('leg_ff', 'leg_hf')

    def antler(self, x, y, dx, dy, sc=1.0):
        def P_(px, py):
            return (0.8 + dx + (px - 0.8) * sc, 1.75 + dy + (py - 1.75) * sc)
        beam = sd_curve(x, y, [P_(0.8, 1.75), P_(0.64, 2.06), P_(0.5, 2.42), P_(0.63, 2.74)],
                        0.042, 0.02, n=8)
        tines = [((0.77, 1.86), (0.97, 1.98)), ((0.72, 1.99), (0.9, 2.12)),
                 ((0.57, 2.27), (0.8, 2.38)), ((0.53, 2.52), (0.42, 2.76)),
                 ((0.58, 2.6), (0.74, 2.82))]
        d = beam
        for (a, b) in tines:
            A, B = P_(*a), P_(*b)
            d = smin(d, sd_seg(x, y, A[0], A[1], B[0], B[1], 0.026, 0.01), 0.02)
        return d

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, 0.0, 1.0, 0.72, 0.29, 0.03)
        body = smin(body, sd_circle(x, y, 0.48, 0.99, 0.3), 0.12)
        body = smin(body, sd_circle(x, y, -0.55, 1.03, 0.28), 0.12)
        p['body'] = body
        neck = sd_curve(x, y, [(0.42, 1.06), (0.68, 1.34), (0.8, 1.62)], 0.25, 0.12, n=6)
        shag = 0.03 * (fbm(x * 12, y * 12, 2, self.seed + 3) - 0.5) * 6
        p['neck'] = neck + np.where(y < 1.45, shag, 0)
        p['head'] = smin(sd_seg(x, y, 0.79, 1.67, 1.12, 1.5, 0.11, 0.055),
                         sd_circle(x, y, 0.84, 1.62, 0.1), 0.05)
        p['ear'] = sd_ellipse(x, y, 0.7, 1.77, 0.12, 0.045, 0.55)
        p['antler'] = union(self.antler(x, y, 0.0, 0.0),
                            self.antler(x, y, 0.1, -0.03, 0.93))
        a = 0.1 * math.sin(self.pose.get('phase', 0.0))
        p['leg_fn'] = legs(x, y, [(0.5, 0.82), (0.54 + a, 0.45), (0.5 + 2 * a, 0.04)],
                           [0.08, 0.045, 0.035])
        p['leg_ff'] = legs(x, y, [(0.4, 0.82), (0.34 - a, 0.46), (0.3 - 2 * a, 0.06)],
                           [0.075, 0.043, 0.033])
        p['leg_hn'] = legs(x, y, [(-0.55, 0.9), (-0.42, 0.6), (-0.62 - a, 0.38),
                                  (-0.57 - 2 * a, 0.04)], [0.12, 0.07, 0.045, 0.035])
        p['leg_hf'] = legs(x, y, [(-0.45, 0.9), (-0.3, 0.6), (-0.46 + a, 0.38),
                                  (-0.36 + 2 * a, 0.05)], [0.11, 0.065, 0.043, 0.033])
        p['tail'] = sd_seg(x, y, -0.8, 1.12, -0.86, 0.96, 0.05, 0.03)
        return p

    def combine(self, p):
        d = smin(p['body'], p['neck'], 0.08)
        d = smin(d, p['head'], 0.04)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.04)
        return union(d, p['tail'], p['ear'], p['antler'])

    def feet(self):
        a = 0.1 * math.sin(self.pose.get('phase', 0.0))
        return [(0.5 + 2 * a, 0.0), (0.3 - 2 * a, 0.02), (-0.57 - 2 * a, 0.0),
                (-0.36 + 2 * a, 0.01)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        antler = p['antler']
        coat = np.maximum(c.sil, -antler * c.k)
        rump = sd_ellipse(x, y, -0.7, 1.0, 0.16, 0.2)
        self.wash(c, np.maximum(coat, -rump * c.k), pig('burnt_sienna', 0.5, 'raw_sienna', 0.4,
                                                        'burnt_umber', 0.1),
                  0.5, 1, edge=0.4, var=0.3, var_f=16)
        self.wash(c, self.clip(c, rump), pig('yellow_ochre', 1.0), 0.18, 2, soft=1.5, edge=0.1)
        self.wash(c, self.clip(c, np.maximum(p['neck'], -p['body'] - 0.02)),
                  pig('burnt_umber', 0.7, 'sepia', 0.3), 0.35, 3, soft=2.0, edge=0.1, dry=0.3)
        self.form(c, 0.4, 0.4, t_mid=0.42, t_deep=0.15)
        self.wash(c, self.clip(c, antler), pig('sepia', 0.6, 'burnt_umber', 0.4), 0.6, 4,
                  wob=0.13, soft=0.7, edge=0.3)
        eye = sd_circle(x, y, 0.88, 1.66, 0.028)
        nose = sd_circle(x, y, 1.13, 1.5, 0.04)
        self.wash(c, self.clip(c, union(eye, nose)), BLACK, 0.9, 6, wob=0.07, soft=0.6)
        hooves = np.maximum(union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf']),
                            y - 0.07)
        self.wash(c, self.clip(c, hooves), BLACK, 0.8, 7, wob=0.07, soft=0.6)
        for n in ('leg_fn', 'leg_hn'):
            self.contour(c, p[n], width=0.01, dens=0.3, inside=p['body'] + 0.02, off=8,
                         ymax=0.85)
        self.glint(c, 0.3)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Fox(Animal):
    R = 0.1
    wf = 0.06
    lb = (-0.85, 0.62, -0.03, 0.66)

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, 0.0, 0.33, 0.3, 0.105, 0.04)
        p['body'] = smin(body, sd_circle(x, y, 0.2, 0.33, 0.105), 0.05)
        head = sd_circle(x, y, 0.36, 0.43, 0.082)
        head = smin(head, sd_seg(x, y, 0.4, 0.425, 0.55, 0.392, 0.05, 0.015), 0.03)
        p['head'] = smin(head, sd_seg(x, y, 0.25, 0.36, 0.34, 0.42, 0.07, 0.06), 0.04)
        p['ears'] = union(sd_tri(x, y, (0.3, 0.47), (0.37, 0.495), (0.315, 0.605)),
                          sd_tri(x, y, (0.35, 0.49), (0.415, 0.505), (0.395, 0.605)))
        ph = self.pose.get('phase', 0.0)
        a = 0.06 * math.sin(ph)
        p['leg_fn'] = legs(x, y, [(0.21, 0.29), (0.25 + a, 0.15), (0.28 + 2 * a, 0.02)],
                           [0.032, 0.02, 0.018], 0.01)
        p['leg_ff'] = legs(x, y, [(0.15, 0.29), (0.1 - a, 0.15), (0.03 - 2 * a, 0.05)],
                           [0.03, 0.019, 0.017], 0.01)
        p['leg_hn'] = legs(x, y, [(-0.2, 0.31), (-0.13, 0.19), (-0.24 - a, 0.1),
                                  (-0.3 - 2 * a, 0.02)], [0.05, 0.03, 0.02, 0.018], 0.01)
        p['leg_hf'] = legs(x, y, [(-0.16, 0.31), (-0.27, 0.19), (-0.3 + a, 0.1),
                                  (-0.2 + 2 * a, 0.04)], [0.048, 0.029, 0.019, 0.017], 0.01)

        def bushy(t):
            return 0.045 + 0.055 * math.sin(math.pi * min(1.0, t * 1.1)) + 0.01
        p['tail'] = sd_curve(x, y, [(-0.26, 0.37), (-0.46, 0.38), (-0.64, 0.28), (-0.78, 0.22)],
                             0, 0, n=10, k=0.03, profile=bushy)
        return p

    far_parts = ('leg_ff', 'leg_hf')

    def combine(self, p):
        d = smin(p['body'], p['head'], 0.04)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.015)
        d = smin(d, p['tail'], 0.03)
        return union(d, p['ears'])

    def feet(self):
        a = 0.06 * math.sin(self.pose.get('phase', 0.0))
        return [(0.28 + 2 * a, 0.0), (-0.3 - 2 * a, 0.0)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        white = union(sd_ellipse(x, y, 0.32, 0.36, 0.1, 0.05, -0.5),
                      sd_ellipse(x, y, 0.45, 0.39, 0.08, 0.025, -0.2),
                      sd_circle(x, y, -0.79, 0.22, 0.06))
        coat = np.maximum(c.sil, -white * c.k)
        self.wash(c, coat, pig('cad_orange', 0.55, 'burnt_sienna', 0.45), 0.62, 1, edge=0.45,
                  var=0.3, var_f=20, bloom=0.2, bloom_f=60)
        self.form(c, 0.35, 0.35, t_mid=0.42, t_deep=0.15)
        socks = np.maximum(union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf']), y - 0.15)
        backs = np.maximum(p['ears'], 0.5 - y)
        nose = sd_circle(x, y, 0.55, 0.393, 0.017)
        eye = sd_ellipse(x, y, 0.405, 0.445, 0.017, 0.01, -0.3)
        self.wash(c, self.clip(c, union(socks, backs, nose, eye)), BLACK, 0.85, 2, wob=0.09,
                  soft=0.6, edge=0.3, dry=0.2)
        self.contour(c, p['tail'], width=0.006, dens=0.35, inside=p['body'] + 0.01, off=3)
        self.glint(c, 0.3)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Flamingo(Animal):
    R = 0.12
    wf = 0.035
    lb = (-0.5, 0.45, -0.02, 1.72)

    def parts(self, x, y):
        p = {}
        p['body'] = smin(sd_ellipse(x, y, 0.0, 0.98, 0.3, 0.14, -0.16),
                         sd_ellipse(x, y, -0.28, 0.99, 0.13, 0.06, -0.1), 0.06)
        p['neck'] = sd_curve(x, y, [(0.22, 1.03), (0.5, 1.22), (0.0, 1.42), (0.2, 1.63)],
                             0.04, 0.028, n=16)
        p['head'] = smin(sd_circle(x, y, 0.22, 1.645, 0.056),
                         sd_curve(x, y, [(0.25, 1.655), (0.34, 1.64), (0.37, 1.54)],
                                  0.036, 0.013, n=6), 0.02)
        if self.pose.get('one_leg', True):
            p['leg'] = union(sd_seg(x, y, 0.0, 0.9, 0.02, 0.48, 0.015, 0.012),
                             sd_seg(x, y, 0.02, 0.48, 0.0, 0.02, 0.012),
                             sd_seg(x, y, -0.02, 0.9, 0.1, 0.62, 0.014, 0.012),
                             sd_seg(x, y, 0.1, 0.62, -0.1, 0.56, 0.012))
        else:
            p['leg'] = union(sd_seg(x, y, 0.02, 0.9, 0.03, 0.48, 0.015, 0.012),
                             sd_seg(x, y, 0.03, 0.48, 0.0, 0.02, 0.012),
                             sd_seg(x, y, -0.03, 0.9, -0.08, 0.48, 0.015, 0.012),
                             sd_seg(x, y, -0.08, 0.48, -0.14, 0.03, 0.012))
        return p

    def combine(self, p):
        d = smin(p['body'], p['neck'], 0.03)
        d = smin(d, p['head'], 0.01)
        return union(d, p['leg'])

    def feet(self):
        return [(0.0, 0.0)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        pink = pig('opera', 0.55, 'rose', 0.45)
        self.wash(c, c.sil, pink, 0.5, 1, edge=0.5, var=0.35, var_f=25, bloom=0.3, bloom_f=80)
        wing = sd_ellipse(x, y, -0.05, 1.0, 0.2, 0.08, -0.2)
        self.wash(c, self.clip(c, np.maximum(wing, p['body'])), pig('alizarin', 0.4, 'opera', 0.6),
                  0.35, 2, soft=1.2, edge=0.4)
        self.form(c, 0.3, 0.3, pg=pig('alizarin', 0.5, 'ultramarine', 0.5), t_mid=0.42,
                  t_deep=0.15)
        # black flight feathers: a dark streak along the lower rear of the wing
        prim = np.maximum(sd_ellipse(x, y, -0.2, 0.93, 0.2, 0.05, -0.1), p['body'])
        beak = np.maximum(p['head'], np.maximum(0.33 - x, c.y - 1.62))
        eye = sd_circle(x, y, 0.235, 1.66, 0.013)
        self.wash(c, self.clip(c, union(prim, beak, eye)), BLACK, 0.85, 3, wob=0.09, soft=0.6)
        self.glint(c, 0.25)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
class Lion(Animal):
    R = 0.3
    wf = 0.12
    lb = (-1.6, 1.45, -0.05, 1.35)
    planes = (-0.25, 0.0, 0.25)
    far_parts = ('leg_ff', 'leg_hf')

    def parts(self, x, y):
        p = {}
        body = sd_ellipse(x, y, -0.02, 0.74, 0.84, 0.27, 0.02)
        body = smin(body, sd_circle(x, y, 0.58, 0.74, 0.3), 0.15)
        body = smin(body, sd_circle(x, y, -0.64, 0.76, 0.27), 0.15)
        p['body'] = body
        shag = (fbm(x * 9.0, y * 9.0, 3, self.seed + 1) - 0.5) * 0.16
        mane = sd_ellipse(x, y, 0.8, 0.93, 0.34, 0.39, -0.25) + shag
        p['mane'] = smin(mane, sd_ellipse(x, y, 0.62, 0.72, 0.22, 0.3, 0.3) + shag, 0.08)
        head = sd_circle(x, y, 1.0, 0.95, 0.17)
        head = smin(head, sd_ellipse(x, y, 1.15, 0.87, 0.13, 0.095, -0.2), 0.05)
        p['head'] = head
        p['ear'] = sd_circle(x, y, 0.9, 1.1, 0.05)
        ph = self.pose.get('phase', 0.0)
        a = 0.12 * math.sin(ph)
        p['leg_fn'] = legs(x, y, [(0.62, 0.58), (0.67 + a, 0.3), (0.66 + 2 * a, 0.05)],
                           [0.13, 0.09, 0.08], 0.03)
        p['leg_ff'] = legs(x, y, [(0.48, 0.58), (0.4 - a, 0.3), (0.3 - 2 * a, 0.08)],
                           [0.12, 0.085, 0.075], 0.03)
        p['leg_hn'] = legs(x, y, [(-0.62, 0.66), (-0.48, 0.38), (-0.7 - a, 0.2),
                                  (-0.64 - 2 * a, 0.04)], [0.17, 0.11, 0.085, 0.075], 0.03)
        p['leg_hf'] = legs(x, y, [(-0.5, 0.66), (-0.34, 0.38), (-0.5 + a, 0.2),
                                  (-0.38 + 2 * a, 0.06)], [0.16, 0.1, 0.08, 0.07], 0.03)
        for n, fx, fy in (('leg_fn', 0.66 + 2 * a, 0.05), ('leg_ff', 0.3 - 2 * a, 0.08),
                          ('leg_hn', -0.64 - 2 * a, 0.04), ('leg_hf', -0.38 + 2 * a, 0.06)):
            paw = np.maximum(sd_ellipse(x, y, fx + 0.04, fy, 0.11, 0.06), fy - 0.05 - y)
            p[n] = smin(p[n], paw, 0.03)
        tail = sd_curve(x, y, [(-0.84, 0.86), (-1.18, 0.7), (-1.36, 0.46), (-1.5, 0.62)],
                        0.04, 0.028, n=8)
        p['tail'] = smin(tail, sd_ellipse(x, y, -1.52, 0.66, 0.06, 0.09, 0.6), 0.02)
        return p

    def combine(self, p):
        d = smin(p['body'], p['mane'], 0.06)
        d = smin(d, p['head'], 0.04)
        for n in ('leg_fn', 'leg_ff', 'leg_hn', 'leg_hf'):
            d = smin(d, p[n], 0.05)
        return union(d, p['tail'], p['ear'])

    def feet(self):
        a = 0.12 * math.sin(self.pose.get('phase', 0.0))
        return [(0.7 + 2 * a, 0.0), (0.34 - 2 * a, 0.03), (-0.6 - 2 * a, 0.0),
                (-0.34 + 2 * a, 0.02)]

    def paint(self, S):
        c = self.begin(S)
        if c is None:
            return
        p, x, y = c.parts, c.x, c.y
        coat = pig('yellow_ochre', 0.5, 'raw_sienna', 0.4, 'burnt_sienna', 0.1)
        # paler underside: simply less pigment there
        belly = smoothstep(0.64, 0.5, y) * smoothstep(0.0, -0.1, p['body'])
        self.wash(c, c.sil, coat, 0.46 * (1 - 0.45 * belly), 1, edge=0.4, var=0.25, var_f=20,
                  bloom=0.2, bloom_f=40)
        # the mane: darker, streaked along strands radiating from the face
        mane = np.maximum(p['mane'], -p['head'] - 0.01)
        ang = np.arctan2(y - 0.92, x - 1.0)
        # hair: high frequency around the face, low along each lock
        strands = fbm(ang * 16.0, np.hypot(x - 1.0, y - 0.92) * 1.5, 3, self.seed + 2)
        md = 0.62 + 0.38 * smoothstep(0.38, 0.62, strands)
        self.wash(c, self.clip(c, mane), pig('burnt_sienna', 0.5, 'raw_sienna', 0.3,
                                             'burnt_umber', 0.2),
                  0.62 * md, 2, soft=1.0, edge=0.35, var=0.3, var_f=40, dry=0.35)
        self.form(c, 0.36, 0.34, t_mid=0.42, t_deep=0.15)
        # face: pale muzzle, dark nose, eye and chin
        nose = sd_ellipse(x, y, 1.27, 0.9, 0.035, 0.028)
        eye = sd_ellipse(x, y, 1.08, 0.99, 0.022, 0.012, -0.2)
        mouth = np.maximum(np.abs(y - 0.82) - 0.008, np.abs(x - 1.18) - 0.07)
        self.wash(c, self.clip(c, union(nose, eye, mouth)), BLACK, 0.85, 4, wob=0.08,
                  soft=0.6)
        tuft = sd_ellipse(x, y, -1.52, 0.66, 0.06, 0.09, 0.6)
        paws = np.maximum(union(p['leg_fn'], p['leg_ff'], p['leg_hn'], p['leg_hf']), y - 0.05)
        self.wash(c, self.clip(c, tuft), BLACK, 0.8, 5, wob=0.1, soft=0.6)
        self.wash(c, self.clip(c, paws), DARK, 0.25, 6, wob=0.1, soft=0.8)
        for n in ('leg_fn', 'leg_hn'):
            self.contour(c, p[n], width=0.01, dens=0.25, inside=p['body'] + 0.02, off=7,
                         ymax=0.6)
        self.glint(c, 0.35)
        self.finish_haze(c)
        self.contact_shadows(S)


# ==========================================================================
def paint_birds(S):
    """A loose flock of swifts/starlings in the evening sky (screen space)."""
    P = S.P
    rng = random.Random(4242)
    birds = []
    for _ in range(16):
        birds.append((rng.gauss(0.36, 0.1), rng.gauss(0.3, 0.05), rng.uniform(0.004, 0.009),
                      rng.uniform(-0.4, 0.4), rng.uniform(0.2, 1.0)))
    for (bx, by, sz, tilt, flap) in [(-0.05, 0.4, 0.016, 0.1, 0.8), (0.08, 0.36, 0.012, -0.2, 0.3),
                                     (0.62, 0.42, 0.011, 0.15, 0.6)]:
        birds.append((bx, by, sz, tilt, flap))
    for i, (bx, by, sz, tilt, flap) in enumerate(birds):
        b = P.bbox_from_screen([bx - 1.3 * sz, bx + 1.3 * sz], [by - 0.8 * sz, by + 0.8 * sz],
                               pad=0.002)
        if b is None:
            continue
        sx, sy = P.grid(b)
        u = (sx - bx) / sz
        v = (sy - by) / sz
        cs, sn = math.cos(tilt), math.sin(tilt)
        u, v = cs * u + sn * v, -sn * u + cs * v
        lift = 0.35 * flap
        wl = sd_curve(u, v, [(0.0, 0.0), (-0.45, lift + 0.12), (-1.05, lift * 0.4)], 0.09, 0.02,
                      n=6)
        wr = sd_curve(u, v, [(0.0, 0.0), (0.45, lift + 0.12), (1.05, lift * 0.4)], 0.09, 0.02,
                      n=6)
        bd = sd_ellipse(u, v, 0.0, -0.02, 0.16, 0.08)
        d = union(wl, wr, bd) * sz
        P.wash(b, d, DARK, 0.75, soft=0.7, wob=0.0004, wob_f=200, edge=0.3, var=0.3, var_f=60,
               seed=5000 + i)


def populate_fauna(S):
    S.add(Elephant(-3.2, 27.0, facing=-1, s=1.0, seed=1, phase=0.6))
    S.add(Elephant(-8.4, 25.2, facing=-1, s=0.46, seed=2, phase=-0.9))
    S.add(Giraffe(10.9, 28.5, facing=1, s=1.0, seed=3, reach=1.0, phase=0.3))
    S.add(Giraffe(10.0, 38.5, facing=-1, s=0.93, seed=4, reach=0.0, phase=-0.5))
    S.add(Zebra(6.0, 16.0, facing=-1, s=1.0, seed=5, phase=0.9))
    S.add(Zebra(3.35, 16.6, facing=-1, s=0.97, seed=6, phase=-0.7, drop=0.3))
    S.add(Zebra(0.7, 15.6, facing=-1, s=1.03, seed=7, phase=0.2))
    S.add(Stag(-12.0, 19.0, facing=1, s=1.0, Y0=0.15, seed=8, phase=0.0))
    S.add(Lion(-2.5, 6.7, facing=1, s=1.0, seed=13, phase=0.7))
    S.add(Fox(3.7, 6.1, facing=-1, s=1.0, seed=9, phase=1.1))
    S.add(Flamingo(5.6, 11.8, facing=1, s=1.0, seed=10))
    S.add(Flamingo(6.6, 12.6, facing=-1, s=0.95, seed=11, one_leg=False))
    S.add(Flamingo(7.5, 13.3, facing=1, s=1.05, seed=12))
    S.post_paint = getattr(S, 'post_paint', []) + [paint_birds]
