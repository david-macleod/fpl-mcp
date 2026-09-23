"""
Core per-pixel maths for the watercolour renderer.

Everything here works on numpy arrays of pixel coordinates, the same way a
fragment shader works on one pixel at a time:

  * hashing / value noise / fBm / Voronoi (for paper, washes and patterns)
  * 2-D signed distance functions (SDFs) used to build every shape
  * a pigment model: Beer-Lambert glazing with per-pigment absorption and
    granulation, mixed subtractively
  * ``Painter.wash`` - one watercolour wash: wobbly edges, edge darkening
    ("coffee ring"), uneven pigment flow, granulation into the paper tooth,
    blooms/back-runs and dry-brush break-up.
"""
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import ndimage

F32 = np.float32
_POOL = ThreadPoolExecutor(4)


# --------------------------------------------------------------------------
# scalar helpers
# --------------------------------------------------------------------------
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------
# hashing and noise
# --------------------------------------------------------------------------
_M1 = np.uint32(0x8DA6B343)
_M2 = np.uint32(0xD8163841)
_M3 = np.uint32(0x5BD1E995)
_S8, _S13, _S15, _S16 = (np.uint32(v) for v in (8, 13, 15, 16))


def hash2(ix, iy, seed):
    """32-bit integer hash of integer lattice coordinates (vectorised)."""
    h = (np.asarray(ix).astype(np.uint32) * _M1
         + np.asarray(iy).astype(np.uint32) * _M2
         + np.uint32((seed * 0x9E3779B9 + 0x632BE5AB) & 0xFFFFFFFF))
    h ^= h >> _S13
    h *= _M3
    h ^= h >> _S15
    return h


def hash01(ix, iy, seed):
    return (hash2(ix, iy, seed) >> _S8).astype(F32) * F32(1.0 / 16777216.0)


def vnoise(x, y, seed=0):
    """Quintic value noise in [0, 1)."""
    x = np.asarray(x, F32)
    y = np.asarray(y, F32)
    xf = np.floor(x)
    yf = np.floor(y)
    tx = x - xf
    ty = y - yf
    ix = xf.astype(np.int64)
    iy = yf.astype(np.int64)
    ix0, iy0 = int(ix.min()), int(iy.min())
    nx = int(ix.max()) - ix0 + 2
    ny = int(iy.max()) - iy0 + 2
    if nx * ny <= 4 * ix.size + 4096:
        # hash the (small) lattice once, then gather - much faster than
        # hashing 4 corners per pixel
        gy, gx = np.mgrid[iy0:iy0 + ny, ix0:ix0 + nx]
        T = hash01(gx, gy, seed).ravel()
        idx = (iy - iy0) * nx + (ix - ix0)
        a = T[idx]
        b = T[idx + 1]
        c = T[idx + nx]
        d = T[idx + nx + 1]
    else:
        a = hash01(ix, iy, seed)
        b = hash01(ix + 1, iy, seed)
        c = hash01(ix, iy + 1, seed)
        d = hash01(ix + 1, iy + 1, seed)
    tx = tx * tx * tx * (tx * (tx * 6 - 15) + 10)
    ty = ty * ty * ty * (ty * (ty * 6 - 15) + 10)
    ab = a + (b - a) * tx
    cd = c + (d - c) * tx
    return ab + (cd - ab) * ty


_RC, _RS = math.cos(0.83), math.sin(0.83)


def fbm(x, y, octaves=4, seed=0, lac=2.02, gain=0.5):
    """Fractal value noise, mean ~0.5, std ~0.12 for 4 octaves."""
    x = np.asarray(x, F32)
    y = np.asarray(y, F32)
    acc = None
    amp, norm = 1.0, 0.0
    for i in range(octaves):
        n = vnoise(x, y, seed * 131 + i * 17 + 1)
        acc = n * amp if acc is None else acc + n * amp
        norm += amp
        amp *= gain
        if i + 1 < octaves:
            x, y = (_RC * x - _RS * y) * lac + 5.2, (_RS * x + _RC * y) * lac + 1.7
    return acc / F32(norm)


def voronoi_f1(x, y, seed=0, jitter=0.9):
    """Distance to the nearest jittered feature point."""
    xf = np.floor(x)
    yf = np.floor(y)
    fx = (x - xf).astype(F32)
    fy = (y - yf).astype(F32)
    ix = xf.astype(np.int64)
    iy = yf.astype(np.int64)
    md = np.full(np.shape(x), 8.0, F32)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            h = hash2(ix + ox, iy + oy, seed)
            px = ox + 0.5 + jitter * ((h & np.uint32(0xFFFF)).astype(F32) / 65536.0 - 0.5) - fx
            py = oy + 0.5 + jitter * ((h >> _S16).astype(F32) / 65536.0 - 0.5) - fy
            np.minimum(md, px * px + py * py, out=md)
    return np.sqrt(md)


def voronoi_border(x, y, seed=0, jitter=0.85):
    """Exact distance to Voronoi cell borders (two-pass) and a per-cell hash.

    Used for reticulated giraffe patches.
    """
    xf = np.floor(x)
    yf = np.floor(y)
    fx = (x - xf).astype(F32)
    fy = (y - yf).astype(F32)
    ix = xf.astype(np.int64)
    iy = yf.astype(np.int64)
    shp = np.shape(x)
    md = np.full(shp, 8.0, F32)
    mrx = np.zeros(shp, F32)
    mry = np.zeros(shp, F32)
    mbx = np.zeros(shp, np.int64)
    mby = np.zeros(shp, np.int64)
    mh = np.zeros(shp, np.uint32)

    def feat(cx, cy):
        h = hash2(cx, cy, seed)
        ox = 0.5 + jitter * ((h & np.uint32(0xFFFF)).astype(F32) / 65536.0 - 0.5)
        oy = 0.5 + jitter * ((h >> _S16).astype(F32) / 65536.0 - 0.5)
        return ox, oy, h

    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            px, py, h = feat(ix + ox, iy + oy)
            rx = ox + px - fx
            ry = oy + py - fy
            d = rx * rx + ry * ry
            m = d < md
            md = np.where(m, d, md)
            mrx = np.where(m, rx, mrx)
            mry = np.where(m, ry, mry)
            mbx = np.where(m, ox, mbx)
            mby = np.where(m, oy, mby)
            mh = np.where(m, h, mh)
    bd = np.full(shp, 8.0, F32)
    for oy in (-2, -1, 0, 1, 2):
        for ox in (-2, -1, 0, 1, 2):
            gx = mbx + ox
            gy = mby + oy
            px, py, _ = feat(ix + gx, iy + gy)
            rx = gx + px - fx
            ry = gy + py - fy
            dx = rx - mrx
            dy = ry - mry
            dd = dx * dx + dy * dy
            ok = dd > 1e-6
            inv = 1.0 / np.sqrt(np.maximum(dd, 1e-6))
            dist = (0.5 * (mrx + rx) * dx + 0.5 * (mry + ry) * dy) * inv
            bd = np.where(ok, np.minimum(bd, dist), bd)
    return bd, mh


# --------------------------------------------------------------------------
# 2-D signed distance functions (negative inside)
# --------------------------------------------------------------------------
def sd_circle(x, y, cx, cy, r):
    return np.hypot(x - cx, y - cy) - r


def sd_ellipse(x, y, cx, cy, rx, ry, ang=0.0):
    """Good approximation of the ellipse SDF (iq)."""
    px = x - cx
    py = y - cy
    if ang:
        c, s = math.cos(ang), math.sin(ang)
        px, py = c * px + s * py, -s * px + c * py
    k0 = np.hypot(px / rx, py / ry)
    k1 = np.hypot(px / (rx * rx), py / (ry * ry))
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)


def sd_seg(x, y, ax, ay, bx, by, ra, rb=None):
    """Tapered capsule between (ax, ay) radius ra and (bx, by) radius rb."""
    if rb is None:
        rb = ra
    pax = x - ax
    pay = y - ay
    bax = bx - ax
    bay = by - ay
    L2 = bax * bax + bay * bay + 1e-12
    h = np.clip((pax * bax + pay * bay) / L2, 0.0, 1.0)
    return np.hypot(pax - bax * h, pay - bay * h) - (ra + (rb - ra) * h)


def sd_box(x, y, cx, cy, hx, hy, r=0.0, ang=0.0):
    px = x - cx
    py = y - cy
    if ang:
        c, s = math.cos(ang), math.sin(ang)
        px, py = c * px + s * py, -s * px + c * py
    qx = np.abs(px) - hx + r
    qy = np.abs(py) - hy + r
    return (np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
            + np.minimum(np.maximum(qx, qy), 0) - r)


def sd_tri(x, y, p0, p1, p2):
    """Exact triangle SDF (iq)."""
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    e0x, e0y = x1 - x0, y1 - y0
    e1x, e1y = x2 - x1, y2 - y1
    e2x, e2y = x0 - x2, y0 - y2
    v0x, v0y = x - x0, y - y0
    v1x, v1y = x - x1, y - y1
    v2x, v2y = x - x2, y - y2

    def seg(vx, vy, ex, ey):
        h = np.clip((vx * ex + vy * ey) / (ex * ex + ey * ey), 0, 1)
        qx, qy = vx - ex * h, vy - ey * h
        return qx * qx + qy * qy

    s = np.sign(e0x * e2y - e0y * e2x)
    d = np.minimum(np.minimum(seg(v0x, v0y, e0x, e0y), seg(v1x, v1y, e1x, e1y)),
                   seg(v2x, v2y, e2x, e2y))
    c0 = s * (v0x * e0y - v0y * e0x)
    c1 = s * (v1x * e1y - v1y * e1x)
    c2 = s * (v2x * e2y - v2y * e2x)
    inside = (c0 > 0) & (c1 > 0) & (c2 > 0)
    d = np.sqrt(d)
    return np.where(inside, -d, d)


def smin(a, b, k):
    """Polynomial smooth minimum (smooth union)."""
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


def bezier_pts(pts, n):
    """Sample a Bezier curve (any degree) given control points."""
    pts = np.asarray(pts, float)
    out = []
    for t in np.linspace(0.0, 1.0, n):
        p = pts.copy()
        while len(p) > 1:
            p = p[:-1] * (1 - t) + p[1:] * t
        out.append(p[0])
    return np.array(out)


def sd_curve(x, y, ctrl, r0, r1, n=10, k=0.0, profile=None):
    """Tapered tube along a Bezier curve (union of tapered capsules)."""
    pts = bezier_pts(ctrl, n)
    ts = np.linspace(0, 1, n)
    if profile is None:
        rad = [r0 + (r1 - r0) * t for t in ts]
    else:
        rad = [profile(t) for t in ts]
    d = None
    for i in range(n - 1):
        di = sd_seg(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], rad[i], rad[i + 1])
        d = di if d is None else smin(d, di, k)
    return d


# --------------------------------------------------------------------------
# pigments (appearance of a density-1 layer over white paper, granulation)
# --------------------------------------------------------------------------
PIGMENTS = {
    'ultramarine': ((0.26, 0.31, 0.70), 1.00),
    'cobalt': ((0.33, 0.46, 0.78), 0.55),
    'cerulean': ((0.46, 0.66, 0.82), 0.70),
    'burnt_sienna': ((0.68, 0.37, 0.22), 0.55),
    'raw_sienna': ((0.85, 0.60, 0.30), 0.35),
    'yellow_ochre': ((0.89, 0.73, 0.43), 0.40),
    'quin_gold': ((0.94, 0.69, 0.22), 0.08),
    'cad_orange': ((0.97, 0.52, 0.18), 0.12),
    'cad_yellow': ((0.99, 0.85, 0.26), 0.08),
    'lemon': ((0.97, 0.93, 0.40), 0.05),
    'rose': ((0.91, 0.43, 0.56), 0.05),
    'alizarin': ((0.70, 0.21, 0.31), 0.08),
    'opera': ((0.97, 0.37, 0.66), 0.00),
    'sap_green': ((0.48, 0.61, 0.24), 0.18),
    'hookers': ((0.23, 0.42, 0.30), 0.20),
    'indigo': ((0.21, 0.24, 0.35), 0.25),
    'paynes': ((0.29, 0.32, 0.40), 0.30),
    'burnt_umber': ((0.44, 0.31, 0.22), 0.50),
    'sepia': ((0.35, 0.27, 0.21), 0.45),
    'lamp_black': ((0.17, 0.17, 0.18), 0.15),
    'violet': ((0.53, 0.40, 0.70), 0.60),
}


class Pigment:
    __slots__ = ('absorb', 'gran')

    def __init__(self, absorb, gran):
        self.absorb = np.asarray(absorb, F32)
        self.gran = float(gran)


def pig(*spec):
    """pig('ultramarine', 0.6, 'burnt_sienna', 0.4) -> subtractive mixture."""
    names = spec[0::2]
    ws = np.array(spec[1::2], float)
    ws = ws / ws.sum()
    absorb = np.zeros(3)
    gran = 0.0
    for n, w in zip(names, ws):
        rgb, g = PIGMENTS[n]
        absorb += w * -np.log(np.array(rgb))
        gran += w * g
    return Pigment(absorb, gran)


# --------------------------------------------------------------------------
# utilities on pixel crops
# --------------------------------------------------------------------------
def region_sd(mask, px):
    """Signed distance (normalised screen units, negative inside) of a boolean
    pixel mask, via exact Euclidean distance transforms."""
    if not mask.any():
        return np.full(mask.shape, 1.0, F32)
    if mask.all():
        return np.full(mask.shape, -1.0, F32)
    inside = ndimage.distance_transform_edt(mask)
    outside = ndimage.distance_transform_edt(~mask)
    return ((outside - inside + np.where(mask, 0.5, -0.5)) * px).astype(F32)


def upsample_to(a, shape, step):
    """Bilinear upsampling of a grid sampled every ``step`` pixels
    (coarse sample k sits exactly on pixel k * step)."""
    if step == 1:
        return a[:shape[0], :shape[1]]
    ny, nx = a.shape
    ys = np.arange(shape[0], dtype=F32) / step
    xs = np.arange(shape[1], dtype=F32) / step
    y0 = np.minimum(ys.astype(np.int64), ny - 1)
    x0 = np.minimum(xs.astype(np.int64), nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    x1 = np.minimum(x0 + 1, nx - 1)
    wy = (ys - y0)[:, None]
    wx = (xs - x0)[None, :]
    r0 = a[y0]
    r1 = a[y1]
    top = r0[:, x0] * (1 - wx) + r0[:, x1] * wx
    bot = r1[:, x0] * (1 - wx) + r1[:, x1] * wx
    return (top * (1 - wy) + bot * wy).astype(F32)


# --------------------------------------------------------------------------
# the painter: canvas, paper and washes
# --------------------------------------------------------------------------
class Painter:
    PAPER_RGB = (0.963, 0.947, 0.908)

    def __init__(self, W, H, seed=11):
        self.W, self.H = W, H
        self.px = 1.0 / H
        self.seed = seed
        self.R = np.empty((H, W, 3), F32)
        self.R[:] = np.array(self.PAPER_RGB, F32)
        self._make_paper()

    # ---- coordinates ----------------------------------------------------
    def grid(self, b, step=1):
        """Normalised screen coords (x right, y up, height = 1) of a crop."""
        y0, y1, x0, x1 = b
        ys = np.arange(y0, y1, step, dtype=F32) + F32(0.5)
        xs = np.arange(x0, x1, step, dtype=F32) + F32(0.5)
        sx = (xs - F32(self.W * 0.5)) / F32(self.H)
        sy = (F32(self.H * 0.5) - ys) / F32(self.H)
        SX, SY = np.meshgrid(sx, sy)
        return SX, SY

    def full(self):
        return (0, self.H, 0, self.W)

    def to_pix(self, sx, sy):
        return sx * self.H + self.W * 0.5 - 0.5, self.H * 0.5 - 0.5 - sy * self.H

    def bbox_from_screen(self, xs, ys, pad=0.0):
        """Pixel bbox (y0, y1, x0, x1) covering screen points, clipped."""
        cols, rows = self.to_pix(np.asarray(xs, float), np.asarray(ys, float))
        p = pad * self.H
        x0 = int(math.floor(np.min(cols) - p))
        x1 = int(math.ceil(np.max(cols) + p)) + 1
        y0 = int(math.floor(np.min(rows) - p))
        y1 = int(math.ceil(np.max(rows) + p)) + 1
        x0, x1 = max(0, x0), min(self.W, x1)
        y0, y1 = max(0, y0), min(self.H, y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return (y0, y1, x0, x1)

    # ---- screen-space noise ---------------------------------------------
    def snoise(self, b, freq, octaves=4, seed=0, ax=1.0, ay=1.0, signed=False):
        """fBm over a crop in screen space. ``freq`` is in cycles per image
        height; low frequencies are evaluated on a coarse grid and
        bilinearly upsampled; big crops are split across threads."""
        y0, y1, x0, x1 = b
        h, w = y1 - y0, x1 - x0
        hi = freq * max(ax, ay) * (2.02 ** (octaves - 1))
        step = int(max(1, min(16, self.H / hi / 3.0)))
        # coarse samples sit on pixels y0 + k*step (k = 0..ny-1) and must
        # reach past the last pixel so interpolation never extrapolates
        ny = (h - 1) // step + 2 if step > 1 else h
        nx = (w - 1) // step + 2 if step > 1 else w

        def work(rows):
            r0, r1 = rows
            sx, sy = self.grid((y0 + r0 * step, y0 + r1 * step, x0, x0 + nx * step), step)
            return fbm(sx * (freq * ax), sy * (freq * ay), octaves, seed)

        if ny * nx > 250_000 and ny >= 8:
            cuts = np.linspace(0, ny, 5).astype(int)
            parts = [(cuts[i], cuts[i + 1]) for i in range(4) if cuts[i + 1] > cuts[i]]
            coarse = np.concatenate(list(_POOL.map(work, parts)), axis=0)
        else:
            coarse = work((0, ny))
        out = upsample_to(coarse, (h, w), step)
        if signed:
            return (out - 0.5) * F32(8.0)
        return out

    # ---- paper ------------------------------------------------------------
    def _make_paper(self):
        b = self.full()
        g = 0.0052  # size of the cold-press "tooth" relative to image height
        n1 = self.snoise(b, 1.0 / g, 3, seed=self.seed + 1)

        def bumps(bb):
            sx, sy = self.grid(bb)
            f1 = voronoi_f1(sx / (g * 1.3), sy / (g * 1.3), seed=self.seed + 2)
            return 1.0 - np.clip(f1 / 0.8, 0, 1)

        rows = np.linspace(0, self.H, 5).astype(int)
        parts = [(rows[i], rows[i + 1], 0, self.W) for i in range(4)]
        bm = np.concatenate(list(_POOL.map(bumps, parts)), axis=0)
        fib = self.snoise(b, 1.0 / (g * 2.5), 3, seed=self.seed + 3, ax=0.35, ay=2.2)
        h = 0.5 * (n1 - 0.5) / 0.12 + 0.5 * (bm - bm.mean()) / (bm.std() + 1e-6) \
            + 0.25 * (fib - 0.5) / 0.12
        lo, hi = np.percentile(h[::7, ::7], [1, 99])
        self.paper = np.clip((h - lo) / (hi - lo), 0, 1).astype(F32)
        # pigment clumping at the pixel scale
        sx, sy = self.grid(b)
        self.speck = vnoise(sx * (self.H / 1.6), sy * (self.H / 1.6), self.seed + 4).astype(F32)
        # large-scale cockling of the wet sheet
        self.cockle = self.snoise(b, 2.5, 3, seed=self.seed + 5)
        # where granulation clumps strongly vs. where the wash stayed smooth
        cl = self.snoise(b, 14.0, 3, seed=self.seed + 6)
        self.clump = np.clip((cl - 0.5) * 5.0 + 0.75, 0.15, 1.6).astype(F32)

    # ---- the watercolour wash -------------------------------------------
    def wash(self, b, sd, pg, dens, soft=0.8, wob=0.0, wob_f=40.0, edge=0.4,
             edge_w=0.0035, var=0.22, var_f=9.0, var_ax=1.0, var_ay=1.0,
             gran=1.0, bloom=0.0, bloom_f=7.0, dry=0.0, dry_w=0.004, seed=0,
             mask=None, fine=0.25):
        """Glaze one wash of pigment ``pg`` over crop ``b``.

        sd    signed distance of the wash shape (normalised units, <0 inside)
              or None for an edgeless wash (use ``mask``/``dens`` arrays)
        dens  scalar or array: base pigment density
        soft  edge softness in pixels (large = wet-in-wet)
        wob   edge wobble amplitude (normalised units), wob_f its frequency
        edge  strength of pigment migration to the wash boundary
        var   uneven flow of pigment inside the wash
        gran  granulation multiplier (pigment settling in the paper tooth)
        bloom cauliflower/back-run strength
        dry   dry-brush break-up near the edges
        """
        if b is None:
            return
        y0, y1, x0, x1 = b
        if y1 <= y0 or x1 <= x0:
            return
        px = self.px
        D = None
        if sd is not None:
            d = sd
            if wob > 0:
                d = d + wob * self.snoise(b, wob_f, 3, seed=seed + 101, signed=True)
                if fine > 0:
                    d = d + (wob * fine) * self.snoise(b, wob_f * 6.0, 2, seed=seed + 102,
                                                       signed=True)
            cov = smoothstep(soft * px, -soft * px, d)
            inside = np.maximum(-d, 0)
            body = cov
            if edge > 0:
                body = cov * (1.0 + edge * np.exp(-inside / edge_w))
            D = body
        else:
            D = np.ones((y1 - y0, x1 - x0), F32)
        if var > 0:
            n = self.snoise(b, var_f, 4, seed=seed + 103, ax=var_ax, ay=var_ay, signed=True)
            D = D * np.maximum(1.0 + var * n, 0.05)
        D = D * dens
        P = self.paper[y0:y1, x0:x1]
        g = pg.gran * gran
        if g > 0:
            # pigment settles in the valleys of the tooth, more so where the
            # wash puddled (clumpy, not uniform)
            Sp = self.speck[y0:y1, x0:x1]
            cl = self.clump[y0:y1, x0:x1]
            D = D * np.maximum(1.0 + g * cl * (0.95 * (0.5 - P) + 0.10 * (Sp - 0.5)), 0.0)
        # slight pooling of water in the cockles of the sheet
        D = D * (1.0 + 0.16 * (0.5 - self.cockle[y0:y1, x0:x1]))
        if bloom > 0:
            bn = self.snoise(b, bloom_f, 5, seed=seed + 104)
            t = 0.63
            inner = smoothstep(t - 0.003, t + 0.003, bn)
            fringe = np.exp(-np.abs(bn - t) / 0.006) * smoothstep(t - 0.04, t, bn)
            D = D * (1.0 - bloom * 0.6 * inner + bloom * 0.9 * fringe)
        if dry > 0:
            zone = 1.0
            if sd is not None:
                zone = np.exp(-np.maximum(-d, 0) / dry_w)
            Sp = self.speck[y0:y1, x0:x1]
            D = D * (1.0 - dry * zone * smoothstep(0.50, 0.72, P + 0.25 * (Sp - 0.5)))
        if mask is not None:
            D = D * mask
        D = np.maximum(D, 0).astype(F32)
        R = self.R[y0:y1, x0:x1]
        R *= np.exp(-D[..., None] * pg.absorb[None, None, :])
