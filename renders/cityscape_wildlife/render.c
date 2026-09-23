/*
 * Rewilded Avenue
 * ===============
 * A per-pixel Monte-Carlo path tracer that renders a city avenue at golden hour with
 * elephants, giraffes and zebras roaming through it. There is no 3D engine, no mesh and
 * no image texture anywhere: every pixel's colour is computed from maths.
 *
 *   camera     thin-lens camera, tent-filtered jittered sub-pixel samples, depth of field
 *   geometry   ground plane + ~2k axis-aligned boxes (buildings, pavements) in a BVH, and
 *              signed-distance-field objects (animals, trees, street lights) sphere-traced
 *              inside their bounding boxes
 *   sky        Rayleigh + Mie single-scattering atmosphere (Nishita) baked to a lat-long map;
 *              sun colour from the transmittance along the sun path
 *   haze       exponential height fog; single-scattered sunlight is estimated with a shadow
 *              ray at a transmittance-distributed point on camera segments (light shafts)
 *   materials  GGX microfacet specular + Lambert diffuse, Fresnel glass with interior mapping
 *              (virtual rooms behind every window), parallax window recesses with analytic
 *              self-shadowing, procedural brick / stone / asphalt / paint / skin / fur
 *   transport  path tracing with next-event estimation of the sun, Russian roulette
 *   film       veiling-glare bloom, lateral chromatic aberration, cos^4 vignette,
 *              ACES filmic curve, sRGB encoding, grain; written as PNG through zlib
 *
 * Build:  make           (gcc -O3 -march=native -ffast-math -fopenmp render.c -lm -lz)
 * Run:    ./render -w 1920 -h 1080 -spp 256 -o cityscape_wildlife.png
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <zlib.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#define PI 3.14159265358979323846
#define INV_PI 0.31830988618379067154
#define BIG 1e30

/* =========================================================================
 * Vector maths
 * ========================================================================= */
typedef struct { double x, y, z; } V;

static inline V vv(double x, double y, double z) { V r = {x, y, z}; return r; }
static inline V vs(double s) { return vv(s, s, s); }
static inline V add(V a, V b) { return vv(a.x + b.x, a.y + b.y, a.z + b.z); }
static inline V sub(V a, V b) { return vv(a.x - b.x, a.y - b.y, a.z - b.z); }
static inline V mul(V a, double s) { return vv(a.x * s, a.y * s, a.z * s); }
static inline V mulv(V a, V b) { return vv(a.x * b.x, a.y * b.y, a.z * b.z); }
static inline V madd(V a, V b, double s) { return vv(a.x + b.x * s, a.y + b.y * s, a.z + b.z * s); }
static inline double dot(V a, V b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static inline V cross(V a, V b) { return vv(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x); }
static inline double len(V a) { return sqrt(dot(a, a)); }
static inline V norm(V a) { double l = len(a); return l > 1e-30 ? mul(a, 1.0 / l) : vv(0, 1, 0); }
static inline V mixv(V a, V b, double t) { return vv(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t); }
static inline V vminv(V a, V b) { return vv(fmin(a.x, b.x), fmin(a.y, b.y), fmin(a.z, b.z)); }
static inline V vmaxv(V a, V b) { return vv(fmax(a.x, b.x), fmax(a.y, b.y), fmax(a.z, b.z)); }
static inline V vexp(V a) { return vv(exp(a.x), exp(a.y), exp(a.z)); }
static inline V reflect(V d, V n) { return sub(d, mul(n, 2.0 * dot(d, n))); }
static inline double clampd(double x, double a, double b) { return x < a ? a : (x > b ? b : x); }
static inline double mixd(double a, double b, double t) { return a + (b - a) * t; }
static inline double sstep(double a, double b, double x) { double t = clampd((x - a) / (b - a), 0.0, 1.0); return t * t * (3.0 - 2.0 * t); }
static inline double fractd(double x) { return x - floor(x); }
static inline double sq(double x) { return x * x; }
static inline double lum(V c) { return 0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z; }
static inline double maxc(V c) { return fmax(c.x, fmax(c.y, c.z)); }

/* orthonormal basis around a unit vector (Duff et al. 2017) */
static inline void onb(V n, V *t, V *b) {
    double s = copysign(1.0, n.z);
    double a = -1.0 / (s + n.z);
    double c = n.x * n.y * a;
    *t = vv(1.0 + s * n.x * n.x * a, s * c, -s * n.x);
    *b = vv(c, s + n.y * n.y * a, -n.y);
}

/* =========================================================================
 * Random numbers, hashing, noise
 * ========================================================================= */
typedef struct { uint64_t s; } Rng;

static inline uint64_t rng_next(Rng *r) {
    uint64_t z = (r->s += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}
static inline double rnd(Rng *r) { return (double)(rng_next(r) >> 11) * (1.0 / 9007199254740992.0); }

static inline uint32_t hash_u32(uint32_t x) {
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16;
    return x;
}
static inline uint32_t hash2i(int x, int y) { return hash_u32((uint32_t)x * 73856093u ^ hash_u32((uint32_t)y * 19349663u + 0x68bc21ebu)); }
static inline uint32_t hash3i(int x, int y, int z) {
    return hash_u32((uint32_t)x * 73856093u ^ hash_u32((uint32_t)y * 19349663u ^ hash_u32((uint32_t)z * 83492791u + 0x2545f491u)));
}
static inline double u01(uint32_t h) { return h * (1.0 / 4294967296.0); }

static inline double gdot3(uint32_t h, double x, double y, double z) {
    switch (h & 15) {
    case 0: return x + y;   case 1: return -x + y;  case 2: return x - y;   case 3: return -x - y;
    case 4: return x + z;   case 5: return -x + z;  case 6: return x - z;   case 7: return -x - z;
    case 8: return y + z;   case 9: return -y + z;  case 10: return y - z;  case 11: return -y - z;
    case 12: return x + y;  case 13: return -y + z; case 14: return -x + y; default: return -y - z;
    }
}
/* Perlin gradient noise, roughly in [-1, 1] */
static double noise3(double px, double py, double pz) {
    double fx = floor(px), fy = floor(py), fz = floor(pz);
    int ix = (int)fx, iy = (int)fy, iz = (int)fz;
    double x = px - fx, y = py - fy, z = pz - fz;
    double u = x * x * x * (x * (x * 6 - 15) + 10);
    double v = y * y * y * (y * (y * 6 - 15) + 10);
    double w = z * z * z * (z * (z * 6 - 15) + 10);
    double n000 = gdot3(hash3i(ix, iy, iz), x, y, z);
    double n100 = gdot3(hash3i(ix + 1, iy, iz), x - 1, y, z);
    double n010 = gdot3(hash3i(ix, iy + 1, iz), x, y - 1, z);
    double n110 = gdot3(hash3i(ix + 1, iy + 1, iz), x - 1, y - 1, z);
    double n001 = gdot3(hash3i(ix, iy, iz + 1), x, y, z - 1);
    double n101 = gdot3(hash3i(ix + 1, iy, iz + 1), x - 1, y, z - 1);
    double n011 = gdot3(hash3i(ix, iy + 1, iz + 1), x, y - 1, z - 1);
    double n111 = gdot3(hash3i(ix + 1, iy + 1, iz + 1), x - 1, y - 1, z - 1);
    return mixd(mixd(mixd(n000, n100, u), mixd(n010, n110, u), v),
                mixd(mixd(n001, n101, u), mixd(n011, n111, u), v), w);
}
static inline double gdot2(uint32_t h, double x, double y) {
    switch (h & 7) {
    case 0: return x + y; case 1: return -x + y; case 2: return x - y; case 3: return -x - y;
    case 4: return x;     case 5: return -x;     case 6: return y;     default: return -y;
    }
}
static double noise2(double px, double py) {
    double fx = floor(px), fy = floor(py);
    int ix = (int)fx, iy = (int)fy;
    double x = px - fx, y = py - fy;
    double u = x * x * x * (x * (x * 6 - 15) + 10), v = y * y * y * (y * (y * 6 - 15) + 10);
    double a = gdot2(hash2i(ix, iy), x, y), b = gdot2(hash2i(ix + 1, iy), x - 1, y);
    double c = gdot2(hash2i(ix, iy + 1), x, y - 1), d = gdot2(hash2i(ix + 1, iy + 1), x - 1, y - 1);
    return mixd(mixd(a, b, u), mixd(c, d, u), v);
}
static double fbm2(double x, double y, int oct) {
    double s = 0, a = 0.5;
    for (int i = 0; i < oct; i++) { s += a * noise2(x + i * 17.13, y - i * 9.71); x *= 2.03; y *= 2.03; a *= 0.5; }
    return s;
}
static double fbm3(V p, int oct) {
    double s = 0, a = 0.5;
    for (int i = 0; i < oct; i++) { s += a * noise3(p.x + i * 5.3, p.y - i * 3.1, p.z + i * 7.7); p = mul(p, 2.02); a *= 0.5; }
    return s;
}
/* cellular noise: distance to nearest / second nearest feature point */
static double voronoi2(double x, double y, double *f2out) {
    int ix = (int)floor(x), iy = (int)floor(y);
    double f1 = 1e9, f2 = 1e9;
    for (int j = -1; j <= 1; j++)
        for (int i = -1; i <= 1; i++) {
            uint32_t h = hash2i(ix + i, iy + j);
            double cx = ix + i + u01(h), cy = iy + j + u01(hash_u32(h ^ 0x5bd1e995u));
            double d = sq(x - cx) + sq(y - cy);
            if (d < f1) { f2 = f1; f1 = d; } else if (d < f2) f2 = d;
        }
    if (f2out) *f2out = sqrt(f2);
    return sqrt(f1);
}
static double voronoi3(V p, double *f2out, uint32_t *idout) {
    int ix = (int)floor(p.x), iy = (int)floor(p.y), iz = (int)floor(p.z);
    double f1 = 1e9, f2 = 1e9;
    uint32_t id = 0;
    for (int k = -1; k <= 1; k++)
        for (int j = -1; j <= 1; j++)
            for (int i = -1; i <= 1; i++) {
                uint32_t h = hash3i(ix + i, iy + j, iz + k);
                double cx = ix + i + u01(h), cy = iy + j + u01(hash_u32(h ^ 0x68e31da4u)), cz = iz + k + u01(hash_u32(h ^ 0xb5297a4du));
                double d = sq(p.x - cx) + sq(p.y - cy) + sq(p.z - cz);
                if (d < f1) { f2 = f1; f1 = d; id = h; } else if (d < f2) f2 = d;
            }
    if (f2out) *f2out = sqrt(f2);
    if (idout) *idout = id;
    return sqrt(f1);
}

/* =========================================================================
 * Settings
 * ========================================================================= */
static int W = 960, H = 540, SPP = 16, MAXDEPTH = 4, SCENE = 0;
static double EXPOSURE = 0.9, SUN_AZ = 17.0, SUN_EL = 11.0, SKY_GAIN = 1.5;
static double CAM[7] = {0.6, 1.55, 0.0, -0.8, 4.4, 60.0, 38.0};
static int CAM_SET = 0;
static double LENS_R = 0.006, FOCUS = 14.0;
static int SEED = 0;

/* =========================================================================
 * Sky: Rayleigh + Mie single scattering (Nishita), baked to a lat-long map
 * ========================================================================= */
#define R_PLANET 6371e3
#define R_ATMOS 6471e3
#define SH_RLH 8e3
#define SH_MIE 1.2e3
#define K_MIE 21e-6
#define G_MIE 0.758
#define I_SUN 22.0
static const V K_RLH = {5.5e-6, 13.0e-6, 22.4e-6};

#define SKY_W 1024
#define SKY_H 384
static float g_sky[SKY_H][SKY_W][3];
static V g_sun_dir, g_sun_E, g_sun_L, g_skyavg, g_amb, g_int_day;
static double g_sun_cos, g_int_lamp = 0.55;

/* exponential height fog (urban haze) */
static double g_fog_s0 = 3.2e-4, g_fog_H = 260.0, g_fog_alb = 0.9, g_fog_g = 0.55;

static int rsi(V r0, V rd, double sr, double *t0, double *t1) {
    double b = 2.0 * dot(rd, r0), c = dot(r0, r0) - sr * sr;
    double d = b * b - 4.0 * c;
    if (d < 0) return 0;
    double s = sqrt(d);
    *t0 = (-b - s) * 0.5; *t1 = (-b + s) * 0.5;
    return 1;
}

static V atmosphere(V r, V r0, V ps) {
    double t0, t1, g0, g1;
    if (!rsi(r0, r, R_ATMOS, &t0, &t1)) return vs(0);
    double pend = t1;
    if (rsi(r0, r, R_PLANET, &g0, &g1) && g0 > 0) pend = fmin(pend, g0);
    double pstart = fmax(t0, 0.0);
    const int IS = 48, JS = 12;
    double istep = (pend - pstart) / IS, it = pstart;
    V totR = vs(0), totM = vs(0);
    double odR = 0, odM = 0;
    double mu = dot(r, ps), mumu = mu * mu, gg = G_MIE * G_MIE;
    double pR = 3.0 / (16.0 * PI) * (1.0 + mumu);
    double pM = 3.0 / (8.0 * PI) * ((1.0 - gg) * (mumu + 1.0)) / (pow(1.0 + gg - 2.0 * mu * G_MIE, 1.5) * (2.0 + gg));
    for (int i = 0; i < IS; i++) {
        V ip = madd(r0, r, it + istep * 0.5);
        double ih = len(ip) - R_PLANET;
        double dR = exp(-ih / SH_RLH) * istep, dM = exp(-ih / SH_MIE) * istep;
        odR += dR; odM += dM;
        double a0 = 0, a1 = 0, b0, b1;
        rsi(ip, ps, R_ATMOS, &a0, &a1);
        int shadowed = rsi(ip, ps, R_PLANET, &b0, &b1) && b0 > 0;
        if (!shadowed) {
            double js = a1 / JS, jt = 0, jR = 0, jM = 0;
            for (int j = 0; j < JS; j++) {
                V jp = madd(ip, ps, jt + js * 0.5);
                double jh = len(jp) - R_PLANET;
                jR += exp(-jh / SH_RLH) * js; jM += exp(-jh / SH_MIE) * js;
                jt += js;
            }
            V tau = add(mul(K_RLH, odR + jR), vs(K_MIE * 1.1 * (odM + jM)));
            V att = vexp(mul(tau, -1.0));
            totR = add(totR, mul(att, dR));
            totM = add(totM, mul(att, dM));
        }
        it += istep;
    }
    return mul(add(mul(mulv(K_RLH, totR), pR), mul(totM, pM * K_MIE)), I_SUN);
}

static V sun_transmittance(V r0, V ps) {
    double a0 = 0, a1 = 0;
    rsi(r0, ps, R_ATMOS, &a0, &a1);
    const int N = 128;
    double step = a1 / N, R = 0, M = 0;
    for (int i = 0; i < N; i++) {
        V p = madd(r0, ps, (i + 0.5) * step);
        double h = len(p) - R_PLANET;
        R += exp(-h / SH_RLH) * step; M += exp(-h / SH_MIE) * step;
    }
    return vexp(mul(add(mul(K_RLH, R), vs(K_MIE * 1.1 * M)), -1.0));
}

#define SKY_EMIN (-0.12)
static V sky_lookup(V d) {
    double e = asin(clampd(d.y, -1.0, 1.0));
    double v = sqrt(clampd((e - SKY_EMIN) / (PI / 2 - SKY_EMIN), 0.0, 1.0));
    double u = (atan2(d.z, d.x) + PI) / (2 * PI);
    double fx = u * SKY_W - 0.5, fy = v * SKY_H - 0.5;
    int x0 = (int)floor(fx), y0 = (int)floor(fy);
    double tx = fx - x0, ty = fy - y0;
    int x1 = x0 + 1, y1 = y0 + 1;
    x0 = (x0 % SKY_W + SKY_W) % SKY_W; x1 = (x1 % SKY_W + SKY_W) % SKY_W;
    if (y0 < 0) { y0 = 0; ty = 0; }
    if (y1 >= SKY_H) y1 = SKY_H - 1;
    if (y0 >= SKY_H) y0 = SKY_H - 1;
    V c = vs(0);
    for (int k = 0; k < 3; k++) {
        double a = mixd(g_sky[y0][x0][k], g_sky[y0][x1][k], tx);
        double b = mixd(g_sky[y1][x0][k], g_sky[y1][x1][k], tx);
        double val = mixd(a, b, ty);
        if (k == 0) c.x = val; else if (k == 1) c.y = val; else c.z = val;
    }
    return mul(c, SKY_GAIN);
}

/* golden-hour altocumulus: a thin, wind-streaked cloud deck at 2.8 km lit by the low sun */
static V sky_radiance(V d) {
    V sky = sky_lookup(d);
    if (d.y <= 0.004 || SCENE == 1) return sky;
    double h = 2800.0, R = R_PLANET;
    double b = R * d.y, t = -b + sqrt(b * b + 2.0 * R * h + h * h); /* distance to the spherical deck */
    double x = d.x * t, z = d.z * t;
    double wx = x + 900.0 * fbm2(x / 5200.0, z / 5200.0, 3), wz = z + 900.0 * fbm2(z / 5200.0 + 7.3, x / 5200.0, 3);
    double n = fbm2(wx / 4200.0 + 3.7, wz / 1700.0 - 1.3, 6);
    double dens = sstep(0.02, 0.32, n + 0.1 * noise2(wx / 380.0, wz / 380.0));
    if (dens <= 0) return sky;
    double fade = sstep(0.004, 0.06, d.y);
    double lit = 0.75 + 0.25 * sstep(0.0, 0.3, n - 0.05);
    V E = mul(g_sun_E, 1.0 / exp(-g_fog_s0 * g_fog_H / fmax(g_sun_dir.y, 0.02))); /* above the haze */
    V cloud = add(mul(E, 0.085 * lit), mul(g_skyavg, 0.55));
    return mixv(sky, cloud, dens * 0.85 * fade);
}

static double hg_phase(double mu, double g) {
    return (1.0 - g * g) / (4.0 * PI * pow(1.0 + g * g - 2.0 * g * mu, 1.5));
}

static void setup_sky(void) {
    double a = SUN_AZ * PI / 180.0, e = SUN_EL * PI / 180.0;
    g_sun_dir = norm(vv(sin(a) * cos(e), sin(e), -cos(a) * cos(e)));
    V r0 = vv(0, R_PLANET + 30.0, 0);
#pragma omp parallel for schedule(dynamic, 4)
    for (int j = 0; j < SKY_H; j++) {
        double v = (j + 0.5) / SKY_H;
        double el = SKY_EMIN + (PI / 2 - SKY_EMIN) * v * v;
        for (int i = 0; i < SKY_W; i++) {
            double az = (i + 0.5) / SKY_W * 2 * PI - PI;
            V d = vv(cos(el) * cos(az), sin(el), cos(el) * sin(az));
            V c = atmosphere(d, r0, g_sun_dir);
            g_sky[j][i][0] = (float)c.x; g_sky[j][i][1] = (float)c.y; g_sky[j][i][2] = (float)c.z;
        }
    }
    /* sun: atmospheric transmittance times ground-level haze transmittance */
    V T = sun_transmittance(r0, g_sun_dir);
    double haze = exp(-g_fog_s0 * g_fog_H / fmax(g_sun_dir.y, 0.02));
    g_sun_E = mul(T, I_SUN * haze);
    double th = 0.35 * PI / 180.0;
    g_sun_cos = cos(th);
    g_sun_L = mul(g_sun_E, 1.0 / (2 * PI * (1 - g_sun_cos)));
    /* cosine-weighted average sky radiance (= sky irradiance / pi on a horizontal plane) */
    V acc = vs(0);
    double wsum = 0;
    for (int j = 0; j < 64; j++)
        for (int i = 0; i < 128; i++) {
            double el = (j + 0.5) / 64 * PI / 2, az = (i + 0.5) / 128 * 2 * PI;
            double w = cos(el) * sin(el);
            acc = add(acc, mul(sky_lookup(vv(cos(el) * cos(az), sin(el), cos(el) * sin(az))), w));
            wsum += w;
        }
    g_skyavg = mul(acc, 1.0 / wsum);
    /* in-scattering source for haze inside the street canyons: sky + light bounced off the ground */
    V ground = mul(add(mul(g_sun_E, g_sun_dir.y * INV_PI), g_skyavg), 0.14);
    g_amb = add(mul(g_skyavg, 0.62), mul(ground, 0.38));
    g_int_day = mul(g_skyavg, 0.30);
    fprintf(stderr, "sun dir (%.3f %.3f %.3f)  E_sun (%.2f %.2f %.2f)  sky avg (%.3f %.3f %.3f)\n",
            g_sun_dir.x, g_sun_dir.y, g_sun_dir.z, g_sun_E.x, g_sun_E.y, g_sun_E.z, g_skyavg.x, g_skyavg.y, g_skyavg.z);
}

/* haze optical depth along o + t d, t in [0, tmax] */
static double fog_tau(V o, V d, double tmax) {
    double a = g_fog_s0 * exp(-o.y / g_fog_H);
    double k = d.y / g_fog_H;
    if (tmax >= BIG * 0.5) return k > 1e-7 ? a / k : 1e3;
    if (fabs(k * tmax) < 1e-6) return a * tmax;
    return a * (1.0 - exp(-k * tmax)) / k;
}
/* distance at which the optical depth reaches taus */
static double fog_invert(V o, V d, double taus) {
    double a = g_fog_s0 * exp(-o.y / g_fog_H);
    double k = d.y / g_fog_H;
    if (fabs(k) < 1e-9) return taus / a;
    double x = 1.0 - taus * k / a;
    if (x <= 1e-12) return BIG;
    return -log(x) / k;
}
/* sunlight above the street is less attenuated by the haze layer */
static inline double sun_gain(double y) {
    if (y <= 0) return 1.0;
    return exp(g_fog_s0 * g_fog_H * (1.0 - exp(-y / g_fog_H)) / fmax(g_sun_dir.y, 0.02));
}

/* =========================================================================
 * Scene description
 * ========================================================================= */
enum { ST_GLASS, ST_BRICK, ST_STONE, ST_CONCRETE, ST_DARK };

typedef struct {
    int style;
    uint32_t seed;
    double height;
    V wall, trim, frame, glass, f0c;
    double f0, trans;
    double floor_h, ground_h, bay_w, win_w, win_h, win_sill, recess, parapet, spandrel;
    double lit, shop_lit;
    int muntins;
} Bld;

enum { PK_BOX, PK_SDF };
enum { BX_BUILDING, BX_SIDEWALK, BX_CORNICE };
typedef struct { V mn, mx; int kind, sub, obj; } Prim;
typedef struct { V mn, mx; int left, right, start, count; } Node;

enum { SK_ELEPHANT, SK_GIRAFFE, SK_ZEBRA, SK_TREE, SK_LAMP, SK_SIGNAL, SK_BIRD };
enum {
    M_NONE, M_ELE_SKIN, M_IVORY, M_EYE, M_HAIR, M_HOOF,
    M_GIR_BODY, M_GIR_LEG, M_GIR_NECK, M_GIR_HEAD, M_GIR_MANE,
    M_ZEB_BODY, M_ZEB_LEG, M_ZEB_NECK, M_ZEB_HEAD, M_ZEB_MANE,
    M_BARK, M_LEAF, M_METAL, M_LAMPGLASS, M_SIG_HOUSING, M_SIG_RED, M_SIG_OFF, M_BIRD
};

typedef struct {
    int kind;
    V pos, F, Z;                 /* world position, local +x (heading) and local +z axes */
    double scale, inv_scale, step;
    V lmn, lmx;                  /* local bounding box */
    uint32_t seed;
    double phase, stride, tusk, ear_flare;
    double hy_c, hy_s, hp_c, hp_s; /* head yaw / pitch */
    V hip[4], knee[4], ankle[4];
    V ch[12]; double cr[12]; int nch;
    V cc[8]; double ccr[8]; int ncc;
} Sobj;

#define MAX_PRIMS 40000
#define MAX_SOBJ 1024
#define MAX_BLD 16000
static Prim g_prims[MAX_PRIMS];
static int g_np;
static Sobj g_sobj[MAX_SOBJ];
static int g_ns;
static Bld g_bld[MAX_BLD];
static int g_nb;
static Node *g_nodes;
static int g_nn;
static int *g_pi;
static double g_tree_xz[128][2];
static int g_ntree;

static inline V to_local(const Sobj *o, V p) {
    V q = sub(p, o->pos);
    return mul(vv(dot(q, o->F), q.y, dot(q, o->Z)), o->inv_scale);
}
static inline V dir_to_world(const Sobj *o, V l) {
    return vv(o->F.x * l.x + o->Z.x * l.z, l.y, o->F.z * l.x + o->Z.z * l.z);
}

/* =========================================================================
 * Signed distance primitives
 * ========================================================================= */
static inline double sd_sphere(V p, double r) { return len(p) - r; }
static inline double sd_ellipsoid(V p, V r) {
    double k0 = len(vv(p.x / r.x, p.y / r.y, p.z / r.z));
    double k1 = len(vv(p.x / (r.x * r.x), p.y / (r.y * r.y), p.z / (r.z * r.z)));
    return k0 * (k0 - 1.0) / (k1 + 1e-12);
}
static inline double sd_capsule(V p, V a, V b, double r) {
    V pa = sub(p, a), ba = sub(b, a);
    double h = clampd(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return len(sub(pa, mul(ba, h))) - r;
}
/* cone with rounded caps of radius r1 at a and r2 at b (Quilez) */
static double sd_rcone(V p, V a, V b, double r1, double r2) {
    V ba = sub(b, a);
    double l2 = dot(ba, ba), rr = r1 - r2, a2 = l2 - rr * rr, il2 = 1.0 / l2;
    V pa = sub(p, a);
    double y = dot(pa, ba), z = y - l2;
    V xv = sub(mul(pa, l2), mul(ba, y));
    double x2 = dot(xv, xv), y2 = y * y * l2, z2 = z * z * l2;
    double k = copysign(1.0, rr) * rr * rr * x2;
    if (copysign(1.0, z) * a2 * z2 > k) return sqrt(x2 + z2) * il2 - r2;
    if (copysign(1.0, y) * a2 * y2 < k) return sqrt(x2 + y2) * il2 - r1;
    return (sqrt(x2 * a2 * il2) + y * rr) * il2 - r1;
}
static inline double smin(double a, double b, double k) {
    double h = fmax(k - fabs(a - b), 0.0) / k;
    return fmin(a, b) - h * h * k * 0.25;
}

/* rotate a point into an animal's head/neck frame about a pivot */
static inline V head_space(const Sobj *o, V p, V pv) {
    V q = sub(p, pv);
    q = vv(o->hy_c * q.x - o->hy_s * q.z, q.y, o->hy_s * q.x + o->hy_c * q.z);
    q = vv(o->hp_c * q.x - o->hp_s * q.y, o->hp_s * q.x + o->hp_c * q.y, q.z);
    return add(q, pv);
}

/* ---- walking gait: foot trajectory + two-bone inverse kinematics ---- */
static V foot_target(V hip, double ph, double stride, double lift, double duty, double ah) {
    double f = fractd(ph), fx, fy = 0;
    if (f < duty) {
        fx = stride * (0.5 - f / duty);
    } else {
        double s = (f - duty) / (1.0 - duty), e = s * s * (3 - 2 * s);
        fx = stride * (-0.5 + e);
        fy = lift * sin(PI * s);
    }
    return vv(hip.x + fx, ah + fy, hip.z);
}
static void leg_ik(V hip, V foot, double l1, double l2, double bend, V *knee, V *ankle) {
    V dv = sub(foot, hip);
    double dist = len(dv);
    V u = mul(dv, 1.0 / dist);
    *ankle = foot;
    if (dist >= l1 + l2 - 1e-4) { *knee = madd(hip, u, dist * l1 / (l1 + l2)); return; }
    double ca = clampd((l1 * l1 + dist * dist - l2 * l2) / (2 * l1 * dist), -1, 1);
    double sa = sqrt(1 - ca * ca);
    V pn = norm(vv(-u.y, u.x, 0));
    *knee = add(hip, add(mul(u, l1 * ca), mul(pn, l1 * sa * bend)));
}
static void pose_legs(Sobj *o, const V hips[4], const double offs[4], const double bend[4],
                      double lift, double duty, double ah, double l1, double l2) {
    for (int i = 0; i < 4; i++) {
        V foot = foot_target(hips[i], o->phase + offs[i], o->stride, lift, duty, ah);
        o->hip[i] = hips[i];
        leg_ik(hips[i], foot, l1, l2, bend[i], &o->knee[i], &o->ankle[i]);
    }
}

/* ---- African elephant (local units: metres, +x forward, +y up) ---- */
static inline double sd_ellipse2(double x, double y, double rx, double ry) {
    double k0 = sqrt(sq(x / rx) + sq(y / ry)), k1 = sqrt(sq(x / (rx * rx)) + sq(y / (ry * ry)));
    return k0 * (k0 - 1.0) / (k1 + 1e-12);
}
/* extrude a 2-D distance into a slab of half-thickness h */
static inline double extrude(double d2, double z, double h) {
    double wy = fabs(z) - h;
    return fmin(fmax(d2, wy), 0.0) + sqrt(sq(fmax(d2, 0.0)) + sq(fmax(wy, 0.0)));
}
/* African elephant ear: thin, slightly cupped plate with an irregular "map of Africa" outline */
static double elephant_ear(V q, double side, double flare) {
    V r = sub(q, vv(1.42, 2.42, side * 0.42));
    double c = cos(flare), s = sin(flare);
    V l = vv(r.x * c - r.z * side * s, r.y, r.x * s + r.z * side * c);
    l.z -= 0.09 * (sq((l.x + 0.5) / 0.55) + 0.4 * sq((l.y + 0.25) / 0.7));
    double d1 = sd_ellipse2(l.x + 0.48, l.y + 0.14, 0.5, 0.64);
    double d2 = sd_ellipse2(l.x + 0.26, l.y + 0.72, 0.27, 0.3);
    double d2d = smin(d1, d2, 0.15) + 0.03 * noise3(l.x * 5.0, l.y * 5.0, side * 3.1);
    return extrude(d2d, l.z, 0.012) - 0.014;
}
static double sdf_elephant(const Sobj *o, V p, int *mat) {
    double d = sd_ellipsoid(sub(p, vv(-0.15, 2.0, 0)), vv(1.55, 0.72, 0.78));
    d = smin(d, sd_ellipsoid(sub(p, vv(0.72, 2.1, 0)), vv(0.85, 0.74, 0.74)), 0.35);
    d = smin(d, sd_ellipsoid(sub(p, vv(-1.05, 2.03, 0)), vv(0.75, 0.7, 0.7)), 0.35);
    d = smin(d, sd_ellipsoid(sub(p, vv(-0.1, 1.72, 0)), vv(1.1, 0.42, 0.7)), 0.35);
    for (int i = 0; i < 4; i++) {
        double dl = sd_rcone(p, o->hip[i], o->knee[i], 0.34, 0.27);
        dl = smin(dl, sd_rcone(p, o->knee[i], o->ankle[i], 0.27, 0.25), 0.08);
        V fb = vv(o->ankle[i].x + 0.03, o->ankle[i].y - 0.1, o->ankle[i].z);
        double df = fmax(sd_rcone(p, o->ankle[i], fb, 0.25, 0.3), (o->ankle[i].y - 0.30) - p.y);
        dl = smin(dl, df, 0.06);
        d = smin(d, dl, 0.25);
    }
    d = smin(d, sd_rcone(p, vv(-1.72, 2.35, 0), vv(-1.92, 1.35, 0.05), 0.06, 0.028), 0.06);
    double dtuft = sd_ellipsoid(sub(p, vv(-1.93, 1.25, 0.05)), vv(0.05, 0.12, 0.05));

    V q = head_space(o, p, vv(1.2, 2.2, 0));
    double dh = sd_ellipsoid(sub(q, vv(1.72, 2.38, 0)), vv(0.55, 0.56, 0.5));
    dh = smin(dh, sd_ellipsoid(sub(q, vv(2.02, 2.14, 0)), vv(0.4, 0.58, 0.4)), 0.22);
    dh = smin(dh, sd_ellipsoid(sub(q, vv(1.78, 1.86, 0)), vv(0.42, 0.34, 0.4)), 0.22);
    double dtr = BIG;
    for (int k = 0; k + 1 < o->nch; k++) dtr = fmin(dtr, sd_rcone(q, o->ch[k], o->ch[k + 1], o->cr[k], o->cr[k + 1]));
    dh = smin(dh, dtr, 0.2);
    for (int s = -1; s <= 1; s += 2) dh = smin(dh, elephant_ear(q, s, o->ear_flare), 0.07);
    d = smin(d, dh, 0.3);
    double dk = BIG, de = BIG;
    for (int s = -1; s <= 1; s += 2) {
        if (o->tusk > 0.1) {
            V t0 = vv(2.2, 1.7, s * 0.2);
            V t1 = add(t0, mul(vv(0.24, -0.34, s * 0.07), o->tusk));
            V t2 = add(t1, mul(vv(0.28, 0.0, -s * 0.05), o->tusk));
            dk = fmin(dk, fmin(sd_rcone(q, t0, t1, 0.07, 0.055), sd_rcone(q, t1, t2, 0.055, 0.025)));
        }
        de = fmin(de, sd_sphere(sub(q, vv(2.1, 2.3, s * 0.38)), 0.04));
    }
    double dist = fmin(fmin(d, dk), fmin(de, dtuft));
    if (mat) {
        if (dk <= dist + 1e-5) *mat = M_IVORY;
        else if (de <= dist + 1e-5) *mat = M_EYE;
        else if (dtuft <= dist + 1e-5) *mat = M_HAIR;
        else *mat = M_ELE_SKIN;
    }
    return dist;
}

/* ---- Giraffe ---- */
static double sdf_giraffe(const Sobj *o, V p, int *mat) {
    V b = sub(p, vv(-0.05, 2.3, 0));
    const double c = 0.978, s = 0.208;
    V br = vv(c * b.x + s * b.y, -s * b.x + c * b.y, b.z);
    double db = sd_ellipsoid(br, vv(0.98, 0.48, 0.4));
    db = smin(db, sd_ellipsoid(sub(p, vv(0.55, 2.52, 0)), vv(0.5, 0.5, 0.38)), 0.3);
    db = smin(db, sd_ellipsoid(sub(p, vv(-0.72, 2.17, 0)), vv(0.45, 0.42, 0.35)), 0.3);
    double dl = BIG, dh = BIG;
    for (int i = 0; i < 4; i++) {
        double l = sd_rcone(p, o->hip[i], o->knee[i], i < 2 ? 0.12 : 0.14, 0.062);
        l = smin(l, sd_sphere(sub(p, o->knee[i]), 0.07), 0.04);
        l = smin(l, sd_rcone(p, o->knee[i], o->ankle[i], 0.058, 0.046), 0.03);
        dl = fmin(dl, l);
        V fb = vv(o->ankle[i].x + 0.03, o->ankle[i].y - 0.05, o->ankle[i].z);
        dh = fmin(dh, fmax(sd_rcone(p, o->ankle[i], fb, 0.05, 0.062), (o->ankle[i].y - 0.1) - p.y));
    }
    double d = smin(db, dl, 0.18);
    double dtail = sd_rcone(p, vv(-1.08, 2.42, 0), vv(-1.2, 1.45, 0.05), 0.04, 0.02);
    d = smin(d, dtail, 0.05);
    double dtuft = sd_ellipsoid(sub(p, vv(-1.21, 1.3, 0.05)), vv(0.05, 0.17, 0.05));

    V q = head_space(o, p, vv(0.62, 2.55, 0));
    double dn = sd_rcone(q, vv(0.62, 2.55, 0), vv(1.42, 4.3, 0), 0.3, 0.12);
    double dm = sd_capsule(q, vv(0.38, 2.74, 0), vv(1.32, 4.38, 0), 0.04);
    double dhd = sd_rcone(q, vv(1.44, 4.45, 0), vv(1.98, 4.2, 0), 0.14, 0.08);
    dhd = smin(dhd, sd_ellipsoid(sub(q, vv(1.6, 4.37, 0)), vv(0.2, 0.14, 0.11)), 0.08);
    double dos = BIG, dknob = BIG, dear = BIG;
    for (int s2 = -1; s2 <= 1; s2 += 2) {
        dos = fmin(dos, sd_rcone(q, vv(1.44, 4.55, s2 * 0.06), vv(1.39, 4.76, s2 * 0.085), 0.034, 0.028));
        dknob = fmin(dknob, sd_sphere(sub(q, vv(1.39, 4.78, s2 * 0.085)), 0.042));
        dear = fmin(dear, sd_ellipsoid(sub(q, vv(1.35, 4.57, s2 * 0.18)), vv(0.05, 0.035, 0.12)));
    }
    dhd = smin(smin(dhd, dos, 0.03), dear, 0.03);
    double dneck = smin(dn, dm, 0.05);
    d = smin(d, dneck, 0.25);
    d = smin(d, dhd, 0.1);
    double dist = fmin(fmin(d, dh), fmin(dtuft, dknob));
    if (mat) {
        if (dh <= dist + 1e-5) *mat = M_HOOF;
        else if (dtuft <= dist + 1e-5 || dknob <= dist + 1e-5) *mat = M_HAIR;
        else {
            double best = db; *mat = M_GIR_BODY;
            if (dl < best) { best = dl; *mat = M_GIR_LEG; }
            if (dtail < best) { best = dtail; *mat = M_GIR_LEG; }
            if (dn < best) { best = dn; *mat = M_GIR_NECK; }
            if (dm < best) { best = dm; *mat = M_GIR_MANE; }
            if (dhd < best) { best = dhd; *mat = M_GIR_HEAD; }
        }
    }
    return dist;
}

/* ---- Plains zebra ---- */
static double sdf_zebra(const Sobj *o, V p, int *mat) {
    double db = sd_ellipsoid(sub(p, vv(0.0, 1.12, 0)), vv(0.74, 0.33, 0.28));
    db = smin(db, sd_ellipsoid(sub(p, vv(0.48, 1.13, 0)), vv(0.36, 0.35, 0.27)), 0.2);
    db = smin(db, sd_ellipsoid(sub(p, vv(-0.5, 1.16, 0)), vv(0.4, 0.35, 0.29)), 0.2);
    db = smin(db, sd_ellipsoid(sub(p, vv(0.0, 0.99, 0)), vv(0.55, 0.24, 0.24)), 0.2);
    double dl = BIG, dh = BIG;
    for (int i = 0; i < 4; i++) {
        double l = sd_rcone(p, o->hip[i], o->knee[i], i < 2 ? 0.08 : 0.095, 0.045);
        l = smin(l, sd_rcone(p, o->knee[i], o->ankle[i], 0.042, 0.033), 0.02);
        dl = fmin(dl, l);
        V fb = vv(o->ankle[i].x + 0.02, o->ankle[i].y - 0.03, o->ankle[i].z);
        dh = fmin(dh, fmax(sd_rcone(p, o->ankle[i], fb, 0.035, 0.044), (o->ankle[i].y - 0.065) - p.y));
    }
    double d = smin(db, dl, 0.12);
    double dtail = sd_rcone(p, vv(-0.9, 1.3, 0), vv(-1.0, 0.8, 0.03), 0.03, 0.016);
    d = smin(d, dtail, 0.04);
    double dtuft = sd_ellipsoid(sub(p, vv(-1.01, 0.7, 0.03)), vv(0.035, 0.1, 0.035));

    V q = head_space(o, p, vv(0.6, 1.25, 0));
    double dn = sd_rcone(q, vv(0.6, 1.22, 0), vv(0.98, 1.7, 0), 0.24, 0.13);
    double dm = sd_capsule(q, vv(0.42, 1.44, 0), vv(0.9, 1.86, 0), 0.045);
    double dhd = sd_rcone(q, vv(1.0, 1.8, 0), vv(1.3, 1.36, 0), 0.12, 0.07);
    dhd = smin(dhd, sd_ellipsoid(sub(q, vv(1.04, 1.64, 0)), vv(0.13, 0.12, 0.085)), 0.06);
    double de = BIG;
    for (int s = -1; s <= 1; s += 2) de = fmin(de, sd_ellipsoid(sub(q, vv(0.96, 1.95, s * 0.075)), vv(0.035, 0.11, 0.045)));
    dhd = smin(dhd, de, 0.03);
    d = smin(d, smin(dn, dm, 0.04), 0.14);
    d = smin(d, dhd, 0.06);
    double dist = fmin(d, fmin(dh, dtuft));
    if (mat) {
        if (dh <= dist + 1e-5) *mat = M_HOOF;
        else if (dtuft <= dist + 1e-5) *mat = M_HAIR;
        else {
            double best = db; *mat = M_ZEB_BODY;
            if (dl < best) { best = dl; *mat = M_ZEB_LEG; }
            if (dtail < best) { best = dtail; *mat = M_ZEB_LEG; }
            if (dn < best) { best = dn; *mat = M_ZEB_NECK; }
            if (dm < best) { best = dm; *mat = M_ZEB_MANE; }
            if (dhd < best) { best = dhd; *mat = M_ZEB_HEAD; }
        }
    }
    return dist;
}

/* ---- Street tree: trunk, limbs, lumpy canopy of leaf clusters ---- */
static double sdf_tree(const Sobj *o, V p, int *mat) {
    double dw = sd_rcone(p, vv(0, -0.1, 0), o->ch[0], 0.2, 0.12);
    for (int i = 1; i < o->nch; i++) dw = smin(dw, sd_rcone(p, o->ch[0], o->ch[i], 0.1, 0.03), 0.08);
    double env = BIG;
    for (int i = 0; i < o->ncc; i++) env = smin(env, sd_sphere(sub(p, o->cc[i]), o->ccr[i]), 0.7);
    double dl;
    if (env > 0.9) dl = env - 0.45;
    else {
        double sd = (o->seed & 255) * 1.37;
        env += 0.45 * noise3(p.x * 0.75 + sd, p.y * 0.75, p.z * 0.75 - sd);
        uint32_t id;
        double f1 = voronoi3(mul(p, 1.0 / 0.24), NULL, &id) * 0.24;
        double cl = f1 - (0.09 + 0.08 * u01(id)) + 0.025 * noise3(p.x * 13.0, p.y * 13.0, p.z * 13.0);
        dl = fmax(env, cl);
        dl = fmax(dl, 0.35 * (noise3(p.x * 0.9 - sd, p.y * 0.9 + 3.1, p.z * 0.9) - 0.38));
    }
    if (mat) *mat = dl < dw ? M_LEAF : M_BARK;
    return fmin(dl, dw);
}

/* ---- Street light ---- */
static double sdf_lamp(const Sobj *o, V p, int *mat) {
    (void)o;
    double d = sd_rcone(p, vv(0, 0, 0), vv(0, 8.0, 0), 0.12, 0.075);
    d = smin(d, sd_rcone(p, vv(0, 0, 0), vv(0, 0.9, 0), 0.19, 0.14), 0.05);
    double arm = sd_capsule(p, vv(0, 7.8, 0), vv(2.3, 8.25, 0), 0.045);
    double head = sd_ellipsoid(sub(p, vv(2.55, 8.2, 0)), vv(0.42, 0.1, 0.18));
    d = fmin(smin(d, arm, 0.1), head);
    if (mat) *mat = (head <= d + 1e-5 && p.y < 8.16) ? M_LAMPGLASS : M_METAL;
    return d;
}

static inline double sd_roundbox(V p, V b, double r) {
    V q = vv(fabs(p.x) - b.x + r, fabs(p.y) - b.y + r, fabs(p.z) - b.z + r);
    return len(vmaxv(q, vs(0))) + fmin(fmax(q.x, fmax(q.y, q.z)), 0.0) - r;
}
/* ---- Traffic signal: pole, mast arm, two hanging three-aspect heads (red lit) ---- */
static double sdf_signal(const Sobj *o, V p, int *mat) {
    double side = o->tusk;
    double d = sd_rcone(p, vv(0, 0, 0), vv(0, 6.5, 0), 0.15, 0.1);
    d = smin(d, sd_rcone(p, vv(0, 0, 0), vv(0, 0.6, 0), 0.22, 0.17), 0.05);
    d = smin(d, sd_rcone(p, vv(0, 6.2, 0), vv(7.2, 6.35, 0), 0.1, 0.055), 0.15);
    double dh = BIG, dred = BIG, doff = BIG;
    for (int k = 0; k < 2; k++) {
        double hx = 4.2 + 2.6 * k;
        dh = fmin(dh, sd_roundbox(sub(p, vv(hx, 5.55, 0)), vv(0.17, 0.5, 0.14), 0.04));
        d = fmin(d, sd_capsule(p, vv(hx, 6.0, 0), vv(hx, 6.3, 0), 0.03));
        for (int j = 0; j < 3; j++) {
            double dl = sd_sphere(sub(p, vv(hx, 5.87 - 0.32 * j, side * 0.1)), 0.095);
            if (j == 0) dred = fmin(dred, dl); else doff = fmin(doff, dl);
        }
    }
    /* pedestrian signal box on the pole */
    dh = fmin(dh, sd_roundbox(sub(p, vv(0.0, 2.9, side * 0.22)), vv(0.2, 0.2, 0.1), 0.03));
    double dist = fmin(fmin(d, dh), fmin(dred, doff));
    if (mat) {
        if (dred <= dist + 1e-5) *mat = M_SIG_RED;
        else if (doff <= dist + 1e-5) *mat = M_SIG_OFF;
        else if (dh <= dist + 1e-5) *mat = M_SIG_HOUSING;
        else *mat = M_METAL;
    }
    return dist;
}

/* ---- Pigeon in flight: body, head, tail and two flapping wings ---- */
static double sdf_bird(const Sobj *o, V p, int *mat) {
    double d = sd_ellipsoid(p, vv(0.17, 0.06, 0.065));
    d = smin(d, sd_sphere(sub(p, vv(0.15, 0.03, 0)), 0.045), 0.03);
    d = smin(d, sd_ellipsoid(sub(p, vv(-0.2, 0.0, 0)), vv(0.09, 0.012, 0.055)), 0.03);
    double a = o->phase, ca = cos(a), sa = sin(a);
    V q = vv(p.x, p.y, fabs(p.z));
    double sw = q.y * sa + q.z * ca, tw = q.y * ca - q.z * sa; /* along / across the wing plane */
    double dw = sd_ellipsoid(vv(q.x + 0.02 + 0.12 * fmax(sw - 0.1, 0.0), tw, sw - 0.2), vv(0.1, 0.012, 0.21));
    d = smin(d, dw, 0.03);
    if (mat) *mat = M_BIRD;
    return d;
}

static double sdf_eval(const Sobj *o, V p, int *mat) {
    switch (o->kind) {
    case SK_ELEPHANT: return sdf_elephant(o, p, mat);
    case SK_GIRAFFE: return sdf_giraffe(o, p, mat);
    case SK_ZEBRA: return sdf_zebra(o, p, mat);
    case SK_TREE: return sdf_tree(o, p, mat);
    case SK_SIGNAL: return sdf_signal(o, p, mat);
    case SK_BIRD: return sdf_bird(o, p, mat);
    default: return sdf_lamp(o, p, mat);
    }
}

static V sdf_normal(const Sobj *o, V p) {
    const double h = 0.0012;
    V a = vv(1, -1, -1), b = vv(-1, -1, 1), c = vv(-1, 1, -1), d = vv(1, 1, 1);
    V n = add(add(mul(a, sdf_eval(o, madd(p, a, h), NULL)), mul(b, sdf_eval(o, madd(p, b, h), NULL))),
              add(mul(c, sdf_eval(o, madd(p, c, h), NULL)), mul(d, sdf_eval(o, madd(p, d, h), NULL))));
    return norm(n);
}

/* =========================================================================
 * Scene construction
 * ========================================================================= */
static void add_box(V mn, V mx, int sub_, int obj) {
    if (g_np >= MAX_PRIMS) return;
    Prim *p = &g_prims[g_np++];
    p->mn = mn; p->mx = mx; p->kind = PK_BOX; p->sub = sub_; p->obj = obj;
}
/* box on one side of the avenue: x in [xa, xb] mirrored for side = -1 */
static void add_box_side(int side, double xa, double xb, double y0, double y1, double z0, double z1, int sub_, int obj) {
    if (side > 0) add_box(vv(xa, y0, z0), vv(xb, y1, z1), sub_, obj);
    else add_box(vv(-xb, y0, z0), vv(-xa, y1, z1), sub_, obj);
}

static void init_bld(Bld *b, int style, uint32_t seed, double h) {
    Rng r = {seed * 0x9E3779B97F4A7C15ULL + 7};
    memset(b, 0, sizeof *b);
    b->style = style; b->seed = seed; b->height = h;
    b->lit = 0.05 + 0.12 * rnd(&r);
    b->shop_lit = 0.55;
    b->parapet = 1.1;
    b->f0 = 0.045; b->glass = vv(0.92, 0.95, 0.93); b->trans = 0.85;
    b->trim = vv(0.6, 0.57, 0.5);
    switch (style) {
    case ST_GLASS: {
        static const V tints[5] = {{0.40, 0.58, 0.62}, {0.50, 0.55, 0.60}, {0.70, 0.73, 0.76}, {0.60, 0.48, 0.36}, {0.34, 0.46, 0.58}};
        b->glass = tints[(int)(rnd(&r) * 4.999)];
        b->f0 = 0.10 + 0.14 * rnd(&r);
        b->trans = 0.35;
        b->frame = rnd(&r) < 0.5 ? vv(0.13, 0.14, 0.15) : vv(0.55, 0.57, 0.60);
        b->wall = mul(b->glass, 0.3);
        b->floor_h = 3.9 + 0.35 * rnd(&r); b->ground_h = 6.5; b->bay_w = 1.5;
        b->spandrel = 0.9 + 0.3 * rnd(&r);
        b->parapet = 1.4;
    } break;
    case ST_DARK:
        b->glass = vv(0.52, 0.42, 0.32); b->f0 = 0.14; b->trans = 0.3;
        b->frame = vv(0.09, 0.07, 0.05);
        b->wall = vv(0.06, 0.05, 0.04);
        b->floor_h = 4.0; b->ground_h = 7.0; b->bay_w = 1.6; b->spandrel = 1.1;
        b->parapet = 1.4;
        break;
    case ST_BRICK: {
        static const V bricks[5] = {{0.36, 0.15, 0.09}, {0.44, 0.22, 0.13}, {0.30, 0.17, 0.12}, {0.52, 0.37, 0.26}, {0.40, 0.25, 0.18}};
        b->wall = bricks[(int)(rnd(&r) * 4.999)];
        b->trim = vv(0.66, 0.62, 0.54);
        b->frame = rnd(&r) < 0.6 ? vv(0.72, 0.70, 0.66) : vv(0.05, 0.05, 0.05);
        b->floor_h = 3.3 + 0.35 * rnd(&r); b->ground_h = 4.8;
        b->bay_w = 1.9 + 0.8 * rnd(&r);
        b->win_w = 0.42 + 0.12 * rnd(&r); b->win_h = 0.52 + 0.1 * rnd(&r); b->win_sill = 0.24;
        b->recess = 0.16; b->muntins = 1;
    } break;
    case ST_STONE: {
        static const V stones[4] = {{0.64, 0.59, 0.50}, {0.57, 0.53, 0.46}, {0.70, 0.66, 0.58}, {0.55, 0.48, 0.39}};
        b->wall = stones[(int)(rnd(&r) * 3.999)];
        b->trim = mul(b->wall, 1.08);
        b->frame = rnd(&r) < 0.5 ? vv(0.09, 0.08, 0.07) : vv(0.35, 0.36, 0.35);
        b->floor_h = 3.6 + 0.4 * rnd(&r); b->ground_h = 5.5;
        b->bay_w = 2.3 + 1.0 * rnd(&r);
        b->win_w = 0.5 + 0.1 * rnd(&r); b->win_h = 0.58 + 0.08 * rnd(&r); b->win_sill = 0.22;
        b->recess = 0.26; b->muntins = 2;
    } break;
    default: {
        static const V conc[3] = {{0.52, 0.52, 0.50}, {0.66, 0.65, 0.62}, {0.45, 0.46, 0.47}};
        b->wall = conc[(int)(rnd(&r) * 2.999)];
        b->trim = mul(b->wall, 0.95);
        b->frame = vv(0.2, 0.21, 0.22);
        b->glass = vv(0.75, 0.85, 0.82); b->f0 = 0.06; b->trans = 0.6;
        b->floor_h = 3.6 + 0.3 * rnd(&r); b->ground_h = 5.0;
        b->bay_w = 1.5;
        b->win_h = 0.55; b->win_sill = 0.3; b->recess = 0.12;
    } break;
    }
    b->f0c = mul(b->glass, b->f0 / maxc(b->glass));
}

static void make_building(Rng *r, int side, double xa, double xb, double za, double zb, int row) {
    if (g_nb >= MAX_BLD) return;
    double zc = 0.5 * (za + zb), u = rnd(r), h;
    if (zc < 260) h = 16 + 44 * pow(u, 1.3);
    else if (zc < 1500) { h = 28 + 80 * u * u; if (rnd(r) < 0.22 + 0.1 * row) h = 110 + 240 * pow(rnd(r), 1.5); }
    else { h = 24 + 60 * u * u; if (rnd(r) < 0.12) h = 90 + 160 * rnd(r); }
    if (row > 0) h *= 0.8 + 0.6 * rnd(r);
    double s = rnd(r);
    int style;
    if (h > 110) style = s < 0.65 ? ST_GLASS : (s < 0.85 ? ST_DARK : ST_STONE);
    else if (h > 55) style = s < 0.3 ? ST_GLASS : s < 0.55 ? ST_STONE : s < 0.8 ? ST_CONCRETE : ST_BRICK;
    else style = s < 0.45 ? ST_BRICK : s < 0.72 ? ST_STONE : s < 0.87 ? ST_CONCRETE : ST_GLASS;
    int bi = g_nb++;
    init_bld(&g_bld[bi], style, hash_u32((uint32_t)(bi * 2654435761u) + 17u), h);
    double x0 = side > 0 ? xa : -xb, x1 = side > 0 ? xb : -xa;
    if (h > 70 && rnd(r) < 0.7) {
        double hp = 14 + 24 * rnd(r), ins = 2.5 + 4 * rnd(r);
        add_box(vv(x0, 0, za), vv(x1, hp, zb), BX_BUILDING, bi);
        double tx0 = x0 + ins, tx1 = x1 - ins, tz0 = za + ins, tz1 = zb - ins;
        if (tx1 - tx0 > 8 && tz1 - tz0 > 8) {
            if (style == ST_STONE && h > 120) {
                double h1 = hp + (h - hp) * 0.55;
                add_box(vv(tx0, hp, tz0), vv(tx1, h1, tz1), BX_BUILDING, bi);
                add_box(vv(tx0 + 3, h1, tz0 + 3), vv(tx1 - 3, h, tz1 - 3), BX_BUILDING, bi);
            } else add_box(vv(tx0, hp, tz0), vv(tx1, h, tz1), BX_BUILDING, bi);
        } else add_box(vv(x0, hp, za), vv(x1, h, zb), BX_BUILDING, bi);
    } else {
        add_box(vv(x0, 0, za), vv(x1, h, zb), BX_BUILDING, bi);
        if ((style == ST_BRICK || style == ST_STONE) && h < 90)
            add_box(vv(x0 - 0.45, h - 0.9, za - 0.45), vv(x1 + 0.45, h + 0.1, zb + 0.45), BX_CORNICE, bi);
    }
}

static void gen_city(void) {
    Rng r = {20260922};
    add_box(vv(10, 0, -400), vv(15, 0.15, 18), BX_SIDEWALK, -1); /* pavement along the park */
    for (int side = -1; side <= 1; side += 2) {
        for (int k = -3; k <= 50; k++) {
            double z0 = 18 + 98.0 * k, z1 = z0 + 80;
            if (side > 0 && z0 < 18) continue; /* open park behind-right of the camera */
            add_box_side(side, 10, 15, 0, 0.15, z0, z1, BX_SIDEWALK, -1);
            add_box_side(side, 15, 240, 0, 0.15, z0, z0 + 4, BX_SIDEWALK, -1);
            add_box_side(side, 15, 240, 0, 0.15, z1 - 4, z1, BX_SIDEWALK, -1);
            int rows = k > 16 ? 1 : 3;
            double xa = 15;
            for (int row = 0; row < rows; row++) {
                double depth = row == 0 ? 22 + 16 * rnd(&r) : 26 + 26 * rnd(&r);
                double xb = xa + depth, z = z0 + 4;
                while (z < z1 - 4 - 0.5) {
                    double w = 14 + 20 * rnd(&r);
                    if (z + w > z1 - 4 - 10) w = z1 - 4 - z;
                    make_building(&r, side, xa, xb, z, z + w, row);
                    z += w;
                }
                xa = xb;
            }
        }
    }
}

static Sobj *new_sobj(int kind, V pos, double hx, double hz, double scale, uint32_t seed) {
    Sobj *o = &g_sobj[g_ns++];
    memset(o, 0, sizeof *o);
    o->kind = kind; o->pos = pos; o->scale = scale; o->inv_scale = 1.0 / scale; o->seed = seed;
    o->F = norm(vv(hx, 0, hz));
    o->Z = vv(-o->F.z, 0, o->F.x);
    o->hy_c = o->hp_c = 1.0;
    o->step = 0.9;
    return o;
}
static void set_head(Sobj *o, double yaw, double pitch) {
    o->hy_c = cos(yaw); o->hy_s = sin(yaw); o->hp_c = cos(pitch); o->hp_s = sin(pitch);
}
static void finish_sobj(Sobj *o) {
    V mn = vs(BIG), mx = vs(-BIG);
    for (int i = 0; i < 8; i++) {
        V l = vv(i & 1 ? o->lmx.x : o->lmn.x, i & 2 ? o->lmx.y : o->lmn.y, i & 4 ? o->lmx.z : o->lmn.z);
        V w = add(o->pos, mul(dir_to_world(o, l), o->scale));
        mn = vminv(mn, w); mx = vmaxv(mx, w);
    }
    Prim *p = &g_prims[g_np++];
    p->mn = mn; p->mx = mx; p->kind = PK_SDF; p->sub = 0; p->obj = (int)(o - g_sobj);
}

static void elephant(V pos, double hx, double hz, double scale, double phase, double stride, double tusk,
                     double yaw, double pitch, double curl, uint32_t seed) {
    Sobj *o = new_sobj(SK_ELEPHANT, pos, hx, hz, scale, seed);
    o->phase = phase; o->stride = stride; o->tusk = tusk; o->ear_flare = 0.3 + 0.15 * tusk;
    set_head(o, yaw, pitch);
    const V hips[4] = {{0.9, 1.75, 0.42}, {0.9, 1.75, -0.42}, {-1.12, 1.75, 0.40}, {-1.12, 1.75, -0.40}};
    const double offs[4] = {0.25, 0.75, 0.0, 0.5}, bend[4] = {1, 1, 1, 1};
    pose_legs(o, hips, offs, bend, 0.24, 0.65, 0.30, 0.73, 0.73);
    /* trunk: cubic Bezier sampled into tapering round cones */
    V P0 = vv(2.3, 1.95, 0), P1 = vv(2.62, 1.45, 0), P2 = vv(2.5 + 0.2 * curl, 0.75, 0), P3 = vv(2.62 + 0.3 * curl, 0.32 + 0.25 * curl, 0.05);
    o->nch = 10;
    for (int i = 0; i < o->nch; i++) {
        double t = i / (double)(o->nch - 1), it = 1 - t;
        o->ch[i] = add(add(mul(P0, it * it * it), mul(P1, 3 * it * it * t)), add(mul(P2, 3 * it * t * t), mul(P3, t * t * t)));
        o->cr[i] = 0.26 - 0.185 * pow(t, 0.85);
    }
    o->lmn = vv(-2.15, -0.02, -1.5); o->lmx = vv(3.3, 3.1, 1.5);
    finish_sobj(o);
}
static void giraffe(V pos, double hx, double hz, double scale, double phase, double stride, double yaw, double pitch, uint32_t seed) {
    Sobj *o = new_sobj(SK_GIRAFFE, pos, hx, hz, scale, seed);
    o->phase = phase; o->stride = stride;
    set_head(o, yaw, pitch);
    const V hips[4] = {{0.55, 2.1, 0.2}, {0.55, 2.1, -0.2}, {-0.72, 2.05, 0.2}, {-0.72, 2.05, -0.2}};
    const double offs[4] = {0.03, 0.53, 0.0, 0.5}, bend[4] = {1, 1, -1, -1};
    pose_legs(o, hips, offs, bend, 0.32, 0.6, 0.1, 1.02, 1.0);
    o->lmn = vv(-1.45, -0.02, -0.8); o->lmx = vv(2.9, 5.1, 0.8);
    o->step = 0.85;
    finish_sobj(o);
}
static void zebra(V pos, double hx, double hz, double scale, double phase, double stride, double yaw, double pitch, uint32_t seed) {
    Sobj *o = new_sobj(SK_ZEBRA, pos, hx, hz, scale, seed);
    o->phase = phase; o->stride = stride;
    set_head(o, yaw, pitch);
    const V hips[4] = {{0.5, 0.92, 0.14}, {0.5, 0.92, -0.14}, {-0.55, 0.97, 0.13}, {-0.55, 0.97, -0.13}};
    const double offs[4] = {0.25, 0.75, 0.0, 0.5}, bend[4] = {1, 1, -1, -1};
    pose_legs(o, hips, offs, bend, 0.16, 0.62, 0.065, 0.47, 0.45);
    o->lmn = vv(-1.15, -0.02, -0.65); o->lmx = vv(1.65, 2.15, 0.65);
    finish_sobj(o);
}
static void tree(V pos, uint32_t seed) {
    Sobj *o = new_sobj(SK_TREE, pos, 1, 0, 1.0, seed);
    Rng r = {seed * 7919ULL + 3};
    double H0 = 5.2 + 0.8 * rnd(&r);
    o->ncc = 8;
    o->cc[0] = vv(0, H0, 0); o->ccr[0] = 1.8 + 0.3 * rnd(&r);
    for (int i = 1; i < 8; i++) {
        double a = 2 * PI * (i / 7.0 + 0.12 * rnd(&r)), rr = 1.3 + 0.8 * rnd(&r);
        o->cc[i] = vv(cos(a) * rr, H0 + (rnd(&r) - 0.5) * 2.0, sin(a) * rr);
        o->ccr[i] = 1.0 + 0.6 * rnd(&r);
    }
    V T = vv(0.05, 2.7, 0.03);
    o->ch[0] = T; o->nch = 6;
    for (int i = 1; i < 6; i++) o->ch[i] = mixv(T, o->cc[i + 1], 0.8);
    o->lmn = vv(-4.3, -0.02, -4.3); o->lmx = vv(4.3, H0 + 3.4, 4.3);
    o->step = 0.6;
    finish_sobj(o);
    if (g_ntree < 128) { g_tree_xz[g_ntree][0] = pos.x; g_tree_xz[g_ntree][1] = pos.z; g_ntree++; }
}
static void lamp(V pos, int side) {
    Sobj *o = new_sobj(SK_LAMP, pos, -side, 0, 1.0, 0);
    o->lmn = vv(-0.25, -0.02, -0.25); o->lmx = vv(3.05, 8.4, 0.25);
    finish_sobj(o);
}

static void signal(V pos, int side) {
    Sobj *o = new_sobj(SK_SIGNAL, pos, -side, 0, 1.0, 0);
    o->tusk = side > 0 ? 1.0 : -1.0; /* which local side the lamps face */
    o->lmn = vv(-0.3, -0.02, -0.4); o->lmx = vv(7.3, 6.5, 0.4);
    finish_sobj(o);
}

static void bird(V pos, double hx, double hz, double flap) {
    Sobj *o = new_sobj(SK_BIRD, pos, hx, hz, 1.0, 0);
    o->phase = flap;
    o->lmn = vv(-0.32, -0.45, -0.5); o->lmx = vv(0.25, 0.45, 0.5);
    finish_sobj(o);
}

static void build_city(void) {
    gen_city();
    for (double z = 38; z < 640; z += 32) { lamp(vv(-10.6, 0.15, z), -1); lamp(vv(10.6, 0.15, z + 16), 1); }
    signal(vv(-10.9, 0.15, 17.3), -1);
    signal(vv(10.9, 0.15, 17.3), 1);
    signal(vv(-10.9, 0.15, 115.3), -1);
    signal(vv(10.9, 0.15, 115.3), 1);
    const double tl[] = {30, 44, 60, 74, 128, 142, 158, 172, 226, 244, 262};
    const double tr[] = {27, 41, 55, 69, 84, 131, 146, 161, 176, 229, 247};
    for (int i = 0; i < (int)(sizeof tl / sizeof *tl); i++) tree(vv(-12.8, 0.15, tl[i]), 100 + i);
    for (int i = 0; i < (int)(sizeof tr / sizeof *tr); i++) tree(vv(12.8, 0.15, tr[i]), 200 + i);

    /* elephant family walking towards the camera */
    elephant(vv(3.4, 0, 11.5), -0.55, -0.83, 1.0, 0.10, 0.8, 1.0, 0.18, 0.05, 0.0, 11);
    elephant(vv(5.7, 0, 12.7), -0.5, -0.87, 0.52, 0.62, 0.45, 0.0, -0.1, -0.05, 0.6, 12);
    elephant(vv(6.8, 0, 17.4), -0.62, -0.78, 0.9, 0.38, 0.8, 0.7, 0.1, 0.0, 0.3, 13);
    /* zebras crossing the avenue on the mid-block crosswalk */
    zebra(vv(-8.3, 0, 20.3), -1.0, 0.1, 0.97, 0.35, 0.9, 0.0, 0.05, 24);
    zebra(vv(-5.6, 0, 21.9), -1.0, 0.05, 1.0, 0.15, 0.9, 0.1, 0.1, 21);
    zebra(vv(-3.0, 0, 20.1), -1.0, 0.16, 1.03, 0.85, 0.9, 0.25, 0.25, 23);
    zebra(vv(-0.4, 0, 21.7), -1.0, 0.0, 0.97, 0.55, 0.9, -0.2, 0.0, 22);
    zebra(vv(2.3, 0, 20.6), -1.0, -0.05, 0.99, 0.7, 0.9, -0.15, 0.6, 25);
    /* giraffes: one browsing a street tree, one ambling down the road */
    giraffe(vv(-9.5, 0, 30.3), -1.0, 0.08, 1.0, 0.0, 0.0, 0.0, 0.32, 31);
    giraffe(vv(-4.4, 0, 42.0), 0.25, -0.97, 1.05, 0.3, 1.6, 0.25, 0.0, 32);
    /* a flock of pigeons wheeling over the avenue */
    bird(vv(-1.5, 13.2, 36.0), 1.0, 0.25, 0.55);
    bird(vv(0.4, 14.1, 38.5), 1.0, 0.3, -0.25);
    bird(vv(-3.2, 12.4, 39.0), 1.0, 0.2, 0.2);
    bird(vv(1.9, 12.9, 35.2), 0.9, 0.4, 0.75);
    bird(vv(-0.6, 15.2, 41.5), 1.0, 0.1, -0.45);
    bird(vv(3.4, 14.6, 40.2), 0.95, 0.3, 0.35);
}

static void build_test_scene(void) {
    elephant(vv(-1.0, 0, 12.0), -1.0, -0.25, 1.0, 0.10, 0.8, 1.0, 0.15, 0.05, 0.0, 11);
    giraffe(vv(-6.5, 0, 17.0), -1.0, 0.3, 1.0, 0.3, 1.6, 0.2, 0.0, 32);
    zebra(vv(4.0, 0, 10.0), -1.0, 0.2, 1.0, 0.15, 0.9, 0.2, 0.1, 21);
    zebra(vv(6.5, 0, 13.0), 0.3, -1.0, 1.0, 0.55, 0.9, -0.2, 0.3, 22);
}

/* ---- bounding volume hierarchy over boxes and SDF objects ---- */
static int g_sort_axis;
static double centroid(const Prim *p, int ax) { return ax == 0 ? p->mn.x + p->mx.x : ax == 1 ? p->mn.y + p->mx.y : p->mn.z + p->mx.z; }
static int cmp_prim(const void *a, const void *b) {
    double ca = centroid(&g_prims[*(const int *)a], g_sort_axis), cb = centroid(&g_prims[*(const int *)b], g_sort_axis);
    return (ca > cb) - (ca < cb);
}
static int bvh_build(int start, int count) {
    int ni = g_nn++;
    V mn = vs(BIG), mx = vs(-BIG), cmn = vs(BIG), cmx = vs(-BIG);
    for (int i = start; i < start + count; i++) {
        const Prim *p = &g_prims[g_pi[i]];
        mn = vminv(mn, p->mn); mx = vmaxv(mx, p->mx);
        V c = mul(add(p->mn, p->mx), 0.5);
        cmn = vminv(cmn, c); cmx = vmaxv(cmx, c);
    }
    g_nodes[ni].mn = mn; g_nodes[ni].mx = mx;
    if (count <= 3) { g_nodes[ni].start = start; g_nodes[ni].count = count; g_nodes[ni].left = g_nodes[ni].right = -1; return ni; }
    V e = sub(cmx, cmn);
    g_sort_axis = (e.x > e.y && e.x > e.z) ? 0 : (e.y > e.z ? 1 : 2);
    qsort(g_pi + start, count, sizeof(int), cmp_prim);
    int half = count / 2;
    int l = bvh_build(start, half);
    int r = bvh_build(start + half, count - half);
    g_nodes[ni].left = l; g_nodes[ni].right = r; g_nodes[ni].count = 0;
    return ni;
}
static void build_bvh(void) {
    g_nodes = calloc((size_t)(2 * g_np + 2), sizeof(Node));
    g_pi = malloc(sizeof(int) * (size_t)g_np);
    for (int i = 0; i < g_np; i++) g_pi[i] = i;
    g_nn = 0;
    bvh_build(0, g_np);
}

/* =========================================================================
 * Ray intersection
 * ========================================================================= */
enum { HT_NONE, HT_GROUND, HT_BOX, HT_SDF };
typedef struct { double t; V p, n, lp; int type, prim, face; } Hit;

static inline int ray_box(V o, V inv, V mn, V mx, double *tn, double *tf) {
    double t1 = (mn.x - o.x) * inv.x, t2 = (mx.x - o.x) * inv.x;
    double lo = fmin(t1, t2), hi = fmax(t1, t2);
    t1 = (mn.y - o.y) * inv.y; t2 = (mx.y - o.y) * inv.y;
    lo = fmax(lo, fmin(t1, t2)); hi = fmin(hi, fmax(t1, t2));
    t1 = (mn.z - o.z) * inv.z; t2 = (mx.z - o.z) * inv.z;
    lo = fmax(lo, fmin(t1, t2)); hi = fmin(hi, fmax(t1, t2));
    *tn = lo; *tf = hi;
    return hi >= lo && hi > 0;
}
static inline int box_hit(V o, V d, V inv, V mn, V mx, double tmax, double *t, int *face) {
    double tx0 = (mn.x - o.x) * inv.x, tx1 = (mx.x - o.x) * inv.x;
    double ty0 = (mn.y - o.y) * inv.y, ty1 = (mx.y - o.y) * inv.y;
    double tz0 = (mn.z - o.z) * inv.z, tz1 = (mx.z - o.z) * inv.z;
    double nx = fmin(tx0, tx1), fx = fmax(tx0, tx1);
    double ny = fmin(ty0, ty1), fy = fmax(ty0, ty1);
    double nz = fmin(tz0, tz1), fz = fmax(tz0, tz1);
    double tn = fmax(nx, fmax(ny, nz)), tf = fmin(fx, fmin(fy, fz));
    if (tf < tn || tn <= 1e-9 || tn >= tmax) return 0;
    *t = tn;
    if (tn == nx) *face = d.x > 0 ? 1 : 0;
    else if (tn == ny) *face = d.y > 0 ? 3 : 2;
    else *face = d.z > 0 ? 5 : 4;
    return 1;
}
static inline V safe_inv(V d) {
    return vv(fabs(d.x) > 1e-12 ? 1.0 / d.x : copysign(1e12, d.x),
              fabs(d.y) > 1e-12 ? 1.0 / d.y : copysign(1e12, d.y),
              fabs(d.z) > 1e-12 ? 1.0 / d.z : copysign(1e12, d.z));
}
/* sphere tracing inside the object's local bounding box */
static int hit_sobj(const Sobj *o, V ro, V rd, double tmax, double *thit) {
    V q = sub(ro, o->pos);
    V lo = mul(vv(dot(q, o->F), q.y, dot(q, o->Z)), o->inv_scale);
    V ld = vv(dot(rd, o->F), rd.y, dot(rd, o->Z));
    double t0, t1;
    if (!ray_box(lo, safe_inv(ld), o->lmn, o->lmx, &t0, &t1)) return 0;
    double ts = fmax(t0, 0.0), te = fmin(t1, tmax * o->inv_scale);
    if (ts >= te) return 0;
    double t = ts;
    for (int i = 0; i < 300; i++) {
        V p = madd(lo, ld, t);
        double d = sdf_eval(o, p, NULL);
        double eps = (2.5e-4 + t * o->scale * 1.6e-4) * o->inv_scale;
        if (d < eps) { *thit = t * o->scale; return 1; }
        t += d * o->step;
        if (t > te) return 0;
    }
    return 0;
}

static int intersect(V o, V d, double tmax, Hit *h, int shadow) {
    double best = tmax;
    int type = HT_NONE, prim = -1, face = 0;
    if (d.y < -1e-12) {
        double t = -o.y / d.y;
        if (t > 0 && t < best) {
            if (shadow) return 1;
            best = t; type = HT_GROUND;
        }
    }
    V inv = safe_inv(d);
    int stn[128];
    double stt[128];
    int sp = 0;
    double tn, tf;
    if (g_nn > 0 && ray_box(o, inv, g_nodes[0].mn, g_nodes[0].mx, &tn, &tf) && tn < best) { stn[sp] = 0; stt[sp] = tn; sp++; }
    while (sp > 0) {
        sp--;
        if (stt[sp] >= best) continue;
        const Node *nd = &g_nodes[stn[sp]];
        if (nd->count > 0) {
            for (int i = nd->start; i < nd->start + nd->count; i++) {
                int pi = g_pi[i];
                const Prim *p = &g_prims[pi];
                double t;
                if (p->kind == PK_BOX) {
                    int f;
                    if (box_hit(o, d, inv, p->mn, p->mx, best, &t, &f)) {
                        if (shadow) return 1;
                        best = t; type = HT_BOX; prim = pi; face = f;
                    }
                } else if (hit_sobj(&g_sobj[p->obj], o, d, best, &t)) {
                    if (shadow) return 1;
                    best = t; type = HT_SDF; prim = pi;
                }
            }
        } else {
            double ta, tb, fa, fb;
            int ha = ray_box(o, inv, g_nodes[nd->left].mn, g_nodes[nd->left].mx, &ta, &fa) && ta < best;
            int hb = ray_box(o, inv, g_nodes[nd->right].mn, g_nodes[nd->right].mx, &tb, &fb) && tb < best;
            if (ha && hb) {
                if (ta <= tb) { stn[sp] = nd->right; stt[sp++] = tb; stn[sp] = nd->left; stt[sp++] = ta; }
                else { stn[sp] = nd->left; stt[sp++] = ta; stn[sp] = nd->right; stt[sp++] = tb; }
            } else if (ha) { stn[sp] = nd->left; stt[sp++] = ta; }
            else if (hb) { stn[sp] = nd->right; stt[sp++] = tb; }
        }
    }
    if (type == HT_NONE) return 0;
    if (h) {
        static const V fn[6] = {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}};
        h->t = best; h->type = type; h->prim = prim; h->face = face;
        h->p = madd(o, d, best);
        if (type == HT_GROUND) { h->n = vv(0, 1, 0); h->p.y = 0; }
        else if (type == HT_BOX) h->n = fn[face];
        else {
            const Sobj *s = &g_sobj[g_prims[prim].obj];
            h->lp = to_local(s, h->p);
            h->n = dir_to_world(s, sdf_normal(s, h->lp));
        }
    }
    return 1;
}

/* =========================================================================
 * Materials
 * ========================================================================= */
typedef struct {
    V alb, f0;       /* diffuse albedo, specular reflectance at normal incidence */
    double a;        /* GGX alpha */
    V n;             /* shading normal */
    V emit;          /* emitted radiance (e.g. interiors seen through glass) */
    int glass;       /* 1: delta reflection with reflectance refl (+ optional diffuse backing) */
    V refl;
    double sunvis;   /* analytic sun visibility (window recesses) */
    double ao;
} Mat;

static inline void mat_init(Mat *m, V n) {
    m->alb = vs(0.5); m->f0 = vs(0.04); m->a = 0.8; m->n = n; m->emit = vs(0);
    m->glass = 0; m->refl = vs(0); m->sunvis = 1.0; m->ao = 1.0;
}

/* ---- facade helpers ---- */
static V interior_radiance(const Bld *b, V d, V N, V T, double ug, double vg, double cw, double chh, uint32_t rh, int shop) {
    double r1 = u01(rh), r2 = u01(hash_u32(rh ^ 0xA511E9B3u)), r3 = u01(hash_u32(rh ^ 0x63D83595u));
    int lit = r1 < (shop ? b->shop_lit : b->lit);
    double depth = shop ? 9.0 : 4.0 + 5.0 * r2;
    double dt = dot(d, T), dv = d.y, dn = fmax(-dot(d, N), 1e-3);
    double t = depth / dn;
    int surf = 0;
    if (dt > 1e-6) { double ts = (cw - ug) / dt; if (ts < t) { t = ts; surf = 1; } }
    else if (dt < -1e-6) { double ts = -ug / dt; if (ts < t) { t = ts; surf = 1; } }
    if (dv > 1e-6) { double tv = (chh - vg) / dv; if (tv < t) { t = tv; surf = 2; } }
    else if (dv < -1e-6) { double tv = -vg / dv; if (tv < t) { t = tv; surf = 3; } }
    double hu = ug + dt * t, hv = vg + dv * t, hd = dn * t;
    V wallc = shop ? vv(0.6, 0.52, 0.44) : (r3 < 0.5 ? vv(0.62, 0.6, 0.56) : vv(0.5, 0.52, 0.55));
    V alb;
    int panel = 0;
    switch (surf) {
    case 0:
        alb = wallc;
        if (hv < 0.8 + 0.6 * u01(hash_u32(rh + (uint32_t)(int)(hu * 1.3)))) alb = mul(alb, 0.35);
        break;
    case 1: alb = mul(wallc, 0.85); break;
    case 2:
        alb = vs(0.7);
        if (lit && fractd(hu / 1.2 + 0.25) < 0.5 && fractd(hd / 2.4) < 0.35) panel = 1;
        break;
    default: alb = shop ? vv(0.4, 0.34, 0.28) : vv(0.2, 0.18, 0.16); break;
    }
    if (lit) {
        V lampc = shop ? vv(1.0, 0.86, 0.66) : vv(1.0, 0.8, 0.58);
        V L = mul(mulv(alb, lampc), g_int_lamp * (0.6 + 0.4 * exp(-hd / 6.0)));
        if (panel) L = add(L, mul(lampc, g_int_lamp * 2.2));
        return L;
    }
    return mul(mulv(alb, g_int_day), exp(-hd / 3.5) + 0.15);
}

static double recess_vis(double u, double v, double depth, double u0, double u1, double v0, double v1, V N, V T) {
    double ln = dot(g_sun_dir, N);
    if (ln <= 1e-4) return 0.0;
    double k = depth / ln;
    double us = u + dot(g_sun_dir, T) * k, vs_ = v + g_sun_dir.y * k;
    return (us >= u0 && us <= u1 && vs_ >= v0 && vs_ <= v1) ? 1.0 : 0.0;
}

static void set_glass(Mat *m, V F0, V d, V n) {
    double c = fabs(dot(d, n));
    double f = pow(1.0 - c, 5.0);
    m->glass = 1; m->n = n;
    m->refl = add(F0, mul(sub(vs(1), F0), f));
    m->alb = vs(0);
}

/* a window opening with a recessed pane: parallax, reveals, sash, blinds and a room behind */
static void window_opening(const Bld *b, Mat *m, V d, V N, V T, double cu, double cv,
                           double u0, double u1, double v0, double v1, double depth,
                           uint32_t wh, double room_w, double room_h, int ribbon) {
    double dt = dot(d, T), dv = d.y, dn = fmax(-dot(d, N), 1e-4);
    double k = depth / dn;
    double ug = cu + dt * k, vg = cv + dv * k;
    if (ug >= u0 && ug <= u1 && vg >= v0 && vg <= v1) {
        m->sunvis = recess_vis(ug, vg, depth, u0, u1, v0, v1, N, T);
        double fw = 0.055, eu = fmin(ug - u0, u1 - ug), ev = fmin(vg - v0, v1 - vg);
        int frame = eu < fw || ev < fw;
        if (b->muntins == 1) frame |= fabs(vg - 0.5 * (v0 + v1)) < 0.03;
        if (b->muntins == 2) frame |= fabs(ug - 0.5 * (u0 + u1)) < 0.025 || fabs(vg - (v0 + 0.68 * (v1 - v0))) < 0.025;
        if (ribbon) frame |= fabs(fractd((ug - u0) / 1.5 + 0.5) - 0.5) * 1.5 < 0.03;
        if (frame) { m->alb = b->frame; m->a = 0.45; m->f0 = vs(0.04); m->n = N; return; }
        double rw = room_w, ru = ug;
        uint32_t rh = wh;
        if (ribbon) { int ri = (int)floor(ug / rw); ru = ug - ri * rw; rh = hash_u32(wh ^ (uint32_t)(ri * 7919)); }
        double br = u01(hash_u32(rh ^ 0x1234567u));
        set_glass(m, vs(b->f0), d, N);
        double Fm = maxc(m->refl);
        if (br < 0.35 && vg > v1 - (v1 - v0) * (0.2 + 0.75 * u01(hash_u32(rh ^ 0x7654321u)))) {
            /* blinds right behind the glass: diffuse backing lit through the pane */
            V bc = vv(0.62, 0.58, 0.52);
            m->alb = mul(bc, (1.0 - Fm) * 0.8);
            m->emit = mul(mulv(bc, g_skyavg), (1.0 - Fm) * 0.35);
        } else {
            V Li = interior_radiance(b, d, N, T, ru, vg, rw, room_h, rh, 0);
            m->emit = mul(mulv(Li, b->glass), (1.0 - Fm) * b->trans / maxc(b->glass));
        }
    } else {
        double fu = BIG, fv = BIG;
        if (dt > 1e-9) fu = (u1 - cu) / (dt * k); else if (dt < -1e-9) fu = (u0 - cu) / (dt * k);
        if (dv > 1e-9) fv = (v1 - cv) / (dv * k); else if (dv < -1e-9) fv = (v0 - cv) / (dv * k);
        double f = clampd(fmin(fu, fv), 0.0, 1.0);
        double ur = cu + dt * k * f, vr = cv + dv * k * f, dr = depth * f;
        double eu = 0, ev = 0;
        if (fu < fv) { m->n = dt > 0 ? mul(T, -1) : T; eu = dt > 0 ? -1e-3 : 1e-3; }
        else { m->n = dv > 0 ? vv(0, -1, 0) : vv(0, 1, 0); ev = dv > 0 ? -1e-3 : 1e-3; }
        m->alb = (fv <= fu && dv < 0) ? b->trim : mul(m->alb, 0.95);
        m->a = 0.8;
        m->sunvis = recess_vis(ur + eu, vr + ev, dr, u0, u1, v0, v1, N, T);
        m->ao = 0.8;
    }
}

static void wall_texture(const Bld *b, Mat *m, double u, double v, uint32_t fs) {
    V c = b->wall;
    m->a = 0.85; m->f0 = vs(0.035);
    switch (b->style) {
    case ST_BRICK: {
        double bh = 0.077, bl = 0.235;
        double row = floor(v / bh), off = fmod(row, 2.0) * 0.5;
        double fu = fractd(u / bl + off) * bl, fvv = fractd(v / bh) * bh;
        uint32_t h = hash2i((int)floor(u / bl + off), (int)row) ^ fs;
        double var = u01(hash_u32(h));
        c = mul(c, 0.82 + 0.3 * var);
        if (var > 0.93) c = mul(c, 0.7);
        if (fu < 0.012 || fvv < 0.011) c = mixv(c, vv(0.5, 0.47, 0.42), 0.8);
    } break;
    case ST_STONE: {
        double bh = 0.6, bl = 1.2;
        double row = floor(v / bh), off = fmod(row, 2.0) * 0.5;
        double fu = fractd(u / bl + off) * bl, fvv = fractd(v / bh) * bh;
        uint32_t h = hash2i((int)floor(u / bl + off), (int)row) ^ fs;
        c = mul(c, 0.92 + 0.14 * u01(h));
        if (fu < 0.006 || fvv < 0.006) c = mul(c, 0.8);
    } break;
    case ST_CONCRETE: {
        double fu = fractd(u / 3.0) * 3.0, fvv = fractd(v / 1.2) * 1.2;
        uint32_t h = hash2i((int)floor(u / 3.0), (int)floor(v / 1.2)) ^ fs;
        c = mul(c, 0.94 + 0.1 * u01(h));
        if (fu < 0.015 || fvv < 0.015) c = mul(c, 0.7);
    } break;
    default: break;
    }
    double streak = noise2(u * 1.7, v * 0.08 + (fs & 1023) * 0.37);
    c = mul(c, 1.0 - 0.12 * sstep(0.1, 0.6, streak));
    c = mul(c, 1.0 - 0.18 * sstep(3.0, 0.0, v));
    m->alb = c;
}

static void storefront(const Bld *b, Mat *m, V d, V N, V T, double u, double v, double Wf, uint32_t fs) {
    int ns = (int)floor(Wf / 5.0 + 0.5);
    if (ns < 1) ns = 1;
    double sw = Wf / ns;
    int si = (int)floor(u / sw);
    if (si >= ns) si = ns - 1;
    if (si < 0) si = 0;
    double su = u - si * sw;
    uint32_t sh = hash_u32(fs ^ (uint32_t)(si * 2654435761u));
    int lobby = (b->style == ST_GLASS || b->style == ST_DARK);
    double top = lobby ? b->ground_h - 0.3 : b->ground_h - 1.3;
    if (v < 0.45 && !lobby) { m->alb = vv(0.12, 0.115, 0.11); m->a = 0.35; return; }
    if (v < top) {
        double fw = 0.07, mid = sw * (0.35 + 0.3 * u01(sh));
        if (su < fw || su > sw - fw || fabs(su - mid) < fw * 0.5 || v < (lobby ? 0.05 : 0.45) + fw || v > top - fw) {
            m->alb = vv(0.04, 0.04, 0.04); m->f0 = vs(0.25); m->a = 0.3;
            return;
        }
        set_glass(m, vs(0.045), d, N);
        double base = lobby ? 0.0 : 0.45;
        V Li = interior_radiance(b, d, N, T, su, v - base, sw, top - base, sh, 1);
        m->emit = mul(Li, (1.0 - maxc(m->refl)) * 0.85);
        return;
    }
    if (!lobby && v < top + 0.9) {
        static const V sc[6] = {{0.05, 0.12, 0.07}, {0.25, 0.04, 0.04}, {0.04, 0.06, 0.16}, {0.03, 0.03, 0.03}, {0.2, 0.05, 0.08}, {0.1, 0.1, 0.1}};
        m->alb = sc[sh % 6]; m->a = 0.4; m->f0 = vs(0.05);
        double lu = (su - 0.5) / fmax(sw - 1.0, 0.5), lv = (v - top - 0.25) / 0.4;
        if (lu > 0.15 && lu < 0.85 && lv > 0 && lv < 1) {
            double cpos = fractd(lu * 11.0);
            if (cpos > 0.18 && cpos < 0.82 && u01(hash_u32(sh + (uint32_t)(int)(lu * 11.0) * 31u + (uint32_t)(int)(lv * 3.0))) > 0.3)
                m->alb = vv(0.78, 0.7, 0.5);
        }
    }
}

static void curtain_wall(const Bld *b, Mat *m, V d, V N, V T, double uu, double bw, double vf, uint32_t wh) {
    double mw = b->style == ST_DARK ? 0.1 : 0.05;
    if (uu < mw * 0.5 || uu > bw - mw * 0.5 || vf < 0.04 || vf > b->floor_h - 0.04) {
        m->alb = mul(b->frame, 0.25); m->f0 = b->frame; m->a = 0.3;
        return;
    }
    double r1 = u01(wh) - 0.5, r2 = u01(hash_u32(wh + 1)) - 0.5;
    V n = norm(add(N, add(mul(T, r1 * 0.014), vv(0, r2 * 0.014, 0))));
    set_glass(m, b->f0c, d, n);
    double Fm = maxc(m->refl);
    if (vf > b->floor_h - b->spandrel) {
        m->emit = mul(b->wall, 0.02);
    } else {
        V Li = interior_radiance(b, d, N, T, uu, vf, bw, b->floor_h - b->spandrel, wh, 0);
        m->emit = mul(mulv(Li, b->glass), (1.0 - Fm) * b->trans / maxc(b->glass));
    }
}

static void shade_facade(const Prim *pr, const Bld *b, int face, V p, V d, V N, Mat *m) {
    V T;
    double u, Wf;
    if (face <= 1) { T = vv(0, 0, 1); u = p.z - pr->mn.z; Wf = pr->mx.z - pr->mn.z; }
    else { T = vv(1, 0, 0); u = p.x - pr->mn.x; Wf = pr->mx.x - pr->mn.x; }
    u = clampd(u, 0, Wf);
    double v = p.y;
    uint32_t fs = hash_u32(b->seed ^ (uint32_t)(face * 0x9E3779B1u) ^ (uint32_t)(int)(pr->mn.y * 7.0));
    int curtain = (b->style == ST_GLASS || b->style == ST_DARK);
    wall_texture(b, m, u, v, fs);
    if (v > pr->mx.y - b->parapet) {
        if (curtain) { m->alb = mul(b->frame, 0.3); m->f0 = b->frame; m->a = 0.35; }
        else if (v > pr->mx.y - 0.3) m->alb = b->trim;
        return;
    }
    int nb = (int)floor(Wf / b->bay_w + 0.5);
    if (nb < 1) nb = 1;
    double bw = Wf / nb;
    int bay = (int)floor(u / bw);
    if (bay >= nb) bay = nb - 1;
    double uu = u - bay * bw;
    if (pr->mn.y < 0.5 && v < b->ground_h) { storefront(b, m, d, N, T, u, v, Wf, fs); return; }
    if (!curtain && v < b->ground_h + 0.35) { m->alb = b->trim; return; }
    double fv = v - b->ground_h;
    int fl = (int)floor(fv / b->floor_h);
    double vf = fv - fl * b->floor_h;
    uint32_t wh = hash_u32(fs ^ hash_u32((uint32_t)bay * 7919u + (uint32_t)fl * 104729u));
    if (curtain) { curtain_wall(b, m, d, N, T, uu, bw, vf, wh); return; }
    double v0 = b->win_sill * b->floor_h, v1 = v0 + b->win_h * b->floor_h;
    if (b->style == ST_CONCRETE) {
        uint32_t fh = hash_u32(fs ^ (uint32_t)fl * 104729u);
        if (u > 0.6 && u < Wf - 0.6 && vf >= v0 && vf <= v1)
            window_opening(b, m, d, N, T, u, vf, 0.6, Wf - 0.6, v0, v1, b->recess, fh, 6.0, b->floor_h, 1);
        return;
    }
    double ww = b->win_w * bw, u0 = 0.5 * (bw - ww), u1 = u0 + ww;
    if (uu >= u0 && uu <= u1 && vf >= v0 && vf <= v1) {
        window_opening(b, m, d, N, T, uu, vf, u0, u1, v0, v1, b->recess, wh, bw, b->floor_h, 0);
    } else {
        if (b->style == ST_BRICK && uu > u0 - 0.1 && uu < u1 + 0.1 && ((vf > v1 && vf < v1 + 0.24) || (vf < v0 && vf > v0 - 0.09)))
            m->alb = b->trim;
        if (b->style == ST_STONE && vf < 0.14) m->alb = mul(b->trim, 1.04);
    }
}

static void shade_sidewalk(const Prim *pr, int face, V p, Mat *m) {
    m->f0 = vs(0.04);
    if (face != 2) { m->alb = vv(0.44, 0.43, 0.41); m->a = 0.6; return; }
    double x = p.x, z = p.z, ax = fabs(x);
    int avenue = pr->mn.x > -15.5 && pr->mx.x < 15.5;
    if (avenue && ax < 10.32) {
        m->alb = mul(vv(0.47, 0.46, 0.44), 0.9 + 0.15 * noise2(x * 3.0, z * 3.0)); m->a = 0.55;
        return;
    }
    for (int i = 0; i < g_ntree; i++) {
        if (fabs(x - g_tree_xz[i][0]) < 0.75 && fabs(z - g_tree_xz[i][1]) < 0.75) {
            double g = sstep(-0.1, 0.4, noise2(x * 6.0, z * 6.0));
            m->alb = mixv(vv(0.11, 0.085, 0.065), vv(0.08, 0.13, 0.04), g); m->a = 0.95;
            return;
        }
    }
    const double s = 1.5;
    double gx = x / s, gz = z / s;
    double dj = fmin(0.5 - fabs(fractd(gx) - 0.5), 0.5 - fabs(fractd(gz) - 0.5)) * s;
    double joint = 1.0 - sstep(0.004, 0.012, dj);
    uint32_t h = hash2i((int)floor(gx), (int)floor(gz));
    double tone = 0.42 + 0.07 * u01(h) + 0.05 * fbm2(x * 0.8, z * 0.8, 2) + 0.02 * noise2(x * 40, z * 40);
    V c = vv(tone * 1.02, tone, tone * 0.96);
    if (noise2(x * 9.0, z * 9.0) > 0.55) c = mul(c, 0.7);
    m->alb = mul(c, 1.0 - 0.45 * joint);
    m->a = 0.85;
}

static void shade_ground(V p, Mat *m) {
    double x = p.x, z = p.z;
    m->n = vv(0, 1, 0); m->f0 = vs(0.04);
    if (SCENE == 1) {
        double n = fbm2(x * 0.3, z * 0.3, 4);
        m->alb = mul(vv(0.36, 0.28, 0.2), 0.8 + 0.4 * n); m->a = 0.9;
        return;
    }
    double kk = floor((z - 9.0) / 98.0 + 0.5), dz = z - (9.0 + 98.0 * kk);
    int in_cross = fabs(dz) < 9.0;
    if (!in_cross && fabs(x) > 10.0) {
        if (x > 15.0 && z < 18.0) {
            double n = fbm2(x * 0.5, z * 0.5, 3);
            m->alb = mul(vv(0.09, 0.15, 0.045), 0.8 + 0.5 * n); m->a = 0.9;
        } else { m->alb = vs(0.33); m->a = 0.8; }
        return;
    }
    double n1 = fbm2(x * 0.045, z * 0.045, 3), n2 = fbm2(x * 0.6, z * 0.6, 2);
    double g1 = noise2(x * 37.0, z * 37.0), g2 = noise2(x * 91.0 + 3.1, z * 91.0 - 1.7);
    double a = 0.115 + 0.026 * n1 + 0.014 * n2 + 0.014 * g1 + 0.012 * g2;
    double rough = 0.72 + 0.1 * n2;
    if (!in_cross) {
        double lp = fmod(fabs(x), 3.333) - 1.667;
        double tr = exp(-sq((fabs(lp) - 0.85) / 0.28));
        a *= 1.0 - 0.18 * tr; rough -= 0.2 * tr;
    }
    {   /* repair patches */
        int cx = (int)floor(x / 4.0), cz = (int)floor(z / 7.0);
        uint32_t hh = hash2i(cx + 1000, cz);
        if (u01(hh) < 0.07) {
            double px0 = cx * 4.0 + u01(hash_u32(hh + 1)) * 1.5, px1 = px0 + 1.2 + u01(hash_u32(hh + 2)) * 2.2;
            double pz0 = cz * 7.0 + u01(hash_u32(hh + 3)) * 2.0, pz1 = pz0 + 1.5 + u01(hash_u32(hh + 4)) * 4.0;
            if (x > px0 && x < px1 && z > pz0 && z < pz1) {
                double e = fmin(fmin(x - px0, px1 - x), fmin(z - pz0, pz1 - z));
                a = (e < 0.03) ? 0.085 : 0.1 + 0.012 * g1;
                rough = 0.62;
            }
        }
    }
    double f2, f1 = voronoi2(x * 0.28, z * 0.28, &f2);
    double edge = (f2 - f1) / 0.28;
    double cmask = sstep(0.1, 0.4, fbm2(x * 0.2 + 5.0, z * 0.2, 2) + 0.2);
    double crack = (1.0 - sstep(0.004, 0.02, edge)) * cmask;
    V c = vv(a * 0.97, a * 0.98, a * 1.02);
    if (crack > 0.5 && noise2(x * 3.0, z * 3.0) > 0.15) c = vv(0.06, 0.1, 0.03);
    else c = mul(c, 1.0 - 0.55 * crack);
    double paint = 0;
    V pc = vv(0.72, 0.72, 0.68);
    if (!in_cross) {
        double ax = fabs(x);
        if (ax > 0.1 && ax < 0.22) { paint = 1; pc = vv(0.62, 0.45, 0.09); }
        for (int i = 1; i <= 2; i++) if (fabs(ax - 3.333 * i) < 0.075 && fractd(z / 12.0) < 0.3) paint = 1;
        if (fabs(ax - 9.55) < 0.075) paint = 1;
        double cw = fabs(dz) - 9.0;
        int crosswalk = cw > 0.8 && cw < 4.6;
        if (crosswalk) {
            paint = 0; pc = vv(0.72, 0.72, 0.68);
            if (ax < 9.4 && fractd((x + 10.0) / 1.2) < 0.5) paint = 1;
        }
        if ((cw > 5.2 && cw < 5.65) && ((dz < 0 && x > 0.3) || (dz > 0 && x < -0.3))) { paint = 1; pc = vv(0.72, 0.72, 0.68); }
    }
    double wear = sstep(-0.35, 0.1, fbm2(x * 1.3, z * 1.3, 3)) * (0.8 + 0.2 * g1);
    c = mixv(c, pc, paint * wear);
    rough = mixd(rough, 0.5, paint * wear);
    /* oil drips down the middle of each lane: darker and slightly glossy */
    if (!in_cross || fabs(x) < 10.0) {
        double lane = fabs(fmod(fabs(x), 3.333) - 1.667);
        double oil = sstep(0.25, 0.55, fbm2(x * 0.9 + 4.4, z * 0.35, 3)) * exp(-sq(lane / 0.55));
        c = mul(c, 1.0 - 0.35 * oil);
        rough = mixd(rough, 0.3, oil);
    }
    /* gutter along the kerb, with moss and weeds creeping out of it */
    if (!in_cross && fabs(x) > 9.72) {
        double g = sstep(0.05, 0.3, fbm2(x * 3.0, z * 0.9, 3));
        c = mixv(vv(0.2, 0.195, 0.185), vv(0.06, 0.1, 0.03), g);
        rough = 0.85;
    }
    /* cast-iron manhole covers */
    {
        static const double mh[4][2] = {{-2.6, 9.6}, {4.9, 30.5}, {-5.4, 52.0}, {1.8, 74.0}};
        for (int i = 0; i < 4; i++) {
            double r = sqrt(sq(x - mh[i][0]) + sq(z - mh[i][1]));
            if (r < 0.36) {
                double grid = (fabs(fractd((x - mh[i][0]) * 12.0) - 0.5) < 0.18) != (fabs(fractd((z - mh[i][1]) * 12.0) - 0.5) < 0.18);
                c = r > 0.33 ? vs(0.05) : mul(vv(0.085, 0.08, 0.075), grid ? 0.7 : 1.1);
                rough = 0.45;
            }
        }
    }
    /* puddles, biased towards the kerbs, plus one in front of the elephants */
    double pm = fbm2(x * 0.11 + 13.7, z * 0.11 - 5.2, 3);
    double wet = sstep(0.24, 0.3, pm + 0.14 * sstep(7.0, 9.8, fabs(x)));
    double pd = sqrt(sq((x - 2.1) / 1.9) + sq((z - 6.9) / 2.6)) + 0.22 * fbm2(x * 0.9, z * 0.9, 3);
    wet = fmax(wet, sstep(1.0, 0.85, pd));
    c = mul(c, 1.0 - 0.45 * wet);
    rough = mixd(rough, 0.012, wet * wet);
    m->alb = c;
    m->a = clampd(rough, 0.012, 1.0);
    m->f0 = vs(mixd(0.04, 0.02, wet));
}

/* ---- animal / object surface appearance ---- */
static double ele_wrinkle(V p) {
    double a = noise3(p.x * 3.0, p.y * 24.0, p.z * 3.0);                 /* horizontal creases */
    double b = 1.0 - fabs(noise3(p.x * 24.0, p.y * 24.0, p.z * 24.0));   /* cracked-skin network */
    return 0.7 * a + 0.3 * b * b * b;
}
static V bump(V n, V p, double (*f)(V), double eps, double k, double *val) {
    double f0 = f(p);
    V g = vv(f(add(p, vv(eps, 0, 0))) - f0, f(add(p, vv(0, eps, 0))) - f0, f(add(p, vv(0, 0, eps))) - f0);
    g = mul(g, 1.0 / eps);
    g = sub(g, mul(n, dot(g, n)));
    if (val) *val = f0;
    return norm(sub(n, mul(g, k)));
}
static double fur_bump(V p) { return noise3(p.x * 40.0, p.y * 40.0, p.z * 40.0) * 0.5 + noise3(p.x * 13.0, p.y * 13.0, p.z * 13.0); }

static V giraffe_color(int part, V p, V q, V nl) {
    V cream = vv(0.80, 0.72, 0.56);
    double scale = 0.26;
    V pp = p;
    if (part == M_GIR_HEAD) { scale = 0.09; pp = q; }
    else if (part == M_GIR_NECK) { scale = 0.21; pp = q; }
    else if (part == M_GIR_LEG) scale = 0.14;
    else if (part == M_GIR_MANE) return vv(0.25, 0.13, 0.05);
    double f2;
    uint32_t id;
    double f1 = voronoi3(mul(pp, 1.0 / scale), &f2, &id);
    double edge = f2 - f1, bw = part == M_GIR_LEG ? 0.22 : 0.1;
    double border = 1.0 - sstep(bw * 0.6, bw, edge);
    double tone = u01(id);
    V patch = vv(0.36 + 0.09 * tone, 0.15 + 0.05 * tone, 0.05 + 0.015 * tone);
    V c = mixv(patch, cream, border);
    if (part == M_GIR_LEG) c = mixv(c, cream, sstep(1.2, 0.5, p.y));
    if (part == M_GIR_HEAD) c = mixv(c, cream, 0.35);
    c = mixv(c, cream, sstep(-0.3, -0.8, nl.y) * 0.6);
    return c;
}
static double zebra_black(int part, V p, V q) {
    double s;
    if (part == M_ZEB_LEG) {
        s = p.y / 0.058 + 0.3 * sin(p.x * 30.0);
    } else if (part == M_ZEB_NECK || part == M_ZEB_MANE) {
        V A = vv(0.6, 1.22, 0), dir = vv(0.62, 0.78, 0);
        s = dot(sub(q, A), dir) / 0.095 + 0.3 * sin(q.y * 9.0);
    } else if (part == M_ZEB_HEAD) {
        V A = vv(1.0, 1.8, 0), dir = vv(0.563, -0.826, 0);
        double t = dot(sub(q, A), dir);
        if (t > 0.42) return 1.0; /* dark muzzle */
        s = t / 0.035;
    } else {
        double ang = sstep(-0.25, -0.75, p.x) * 1.05;
        double uu = p.x * cos(ang) + (p.y - 1.1) * sin(ang);
        s = uu / 0.105 + 0.55 * sin(p.y * 6.0 + p.x * 2.0) + 0.25 * noise3(p.x * 3, p.y * 3, p.z * 3);
        double belly = sstep(0.93, 0.8, p.y);
        double st = 0.5 + 0.5 * sin(2 * PI * s);
        return sstep(0.4 + 0.55 * belly, 0.5 + 0.55 * belly, st);
    }
    double st = 0.5 + 0.5 * sin(2 * PI * s);
    return sstep(0.4, 0.52, st);
}

static void shade_sobj(const Sobj *o, V lp, V nl, int mat, Mat *m) {
    V n = nl;
    m->f0 = vs(0.035);
    switch (mat) {
    case M_ELE_SKIN: {
        double w;
        n = bump(nl, lp, ele_wrinkle, 0.002, 0.012, &w);
        double b1 = fbm3(mul(lp, 1.2), 3), b2 = noise3(lp.x * 5, lp.y * 5, lp.z * 5);
        V skin = vv(0.095, 0.086, 0.078), dust = vv(0.2, 0.145, 0.1), mud = vv(0.06, 0.045, 0.035);
        double dm = clampd(0.6 * sstep(1.2, 0.25, lp.y) + 0.3 * b1 + 0.08, 0.0, 0.8);
        V c = mixv(skin, dust, dm);
        c = mixv(c, mud, 0.7 * sstep(0.1, 0.35, fbm3(mul(lp, 2.3), 3)) * sstep(1.6, 0.9, lp.y));
        c = mul(c, 1.0 + 0.1 * b2 - 0.3 * clampd(w, 0.0, 1.0));
        m->alb = c; m->a = 0.65;
    } break;
    case M_IVORY: m->alb = vv(0.72, 0.66, 0.52); m->a = 0.25; m->f0 = vs(0.045); break;
    case M_EYE: m->alb = vv(0.03, 0.02, 0.015); m->a = 0.05; m->f0 = vs(0.05); break;
    case M_HAIR: m->alb = vv(0.03, 0.025, 0.02); m->a = 0.8; break;
    case M_HOOF: m->alb = vv(0.06, 0.055, 0.05); m->a = 0.5; break;
    case M_GIR_BODY: case M_GIR_LEG: case M_GIR_NECK: case M_GIR_HEAD: case M_GIR_MANE: {
        V q = head_space(o, lp, vv(0.62, 2.55, 0));
        n = bump(nl, lp, fur_bump, 0.003, 0.002, NULL);
        m->alb = giraffe_color(mat, lp, q, nl); m->a = 0.85;
    } break;
    case M_ZEB_BODY: case M_ZEB_LEG: case M_ZEB_NECK: case M_ZEB_HEAD: case M_ZEB_MANE: {
        V q = head_space(o, lp, vv(0.6, 1.25, 0));
        n = bump(nl, lp, fur_bump, 0.003, 0.002, NULL);
        double k = zebra_black(mat, lp, q);
        m->alb = mixv(vv(0.76, 0.73, 0.67), vv(0.025, 0.022, 0.022), k); m->a = 0.85;
    } break;
    case M_BARK: {
        double b = noise3(lp.x * 8, lp.y * 2, lp.z * 8);
        m->alb = mul(vv(0.2, 0.17, 0.13), 0.75 + 0.35 * b); m->a = 0.9;
    } break;
    case M_LEAF: {
        uint32_t id;
        voronoi3(mul(lp, 1.0 / 0.24), NULL, &id);
        double t = u01(id), t2 = u01(hash_u32(id ^ 0xabcdefu));
        V c = mixv(vv(0.04, 0.075, 0.02), vv(0.1, 0.13, 0.028), t);
        if (t2 > 0.88) c = vv(0.14, 0.13, 0.03);
        /* individual leaves: jitter the normal per ~4 cm cell so clusters shade like foliage */
        uint32_t lh = hash3i((int)floor(lp.x * 25.0), (int)floor(lp.y * 25.0), (int)floor(lp.z * 25.0));
        V jr = vv(u01(lh) - 0.5, u01(hash_u32(lh + 1)) - 0.5, u01(hash_u32(lh + 2)) - 0.5);
        n = norm(add(nl, mul(jr, 1.5)));
        if (dot(n, nl) < 0.2) n = nl;
        /* darker towards the inside of the crown */
        double env = BIG;
        for (int i = 0; i < o->ncc; i++) env = smin(env, sd_sphere(sub(lp, o->cc[i]), o->ccr[i]), 0.7);
        c = mul(c, 0.55 + 0.45 * sstep(-1.2, 0.0, env));
        m->alb = mul(c, 0.8 + 0.4 * u01(hash_u32(lh + 3))); m->a = 0.5; m->f0 = vs(0.04);
    } break;
    case M_METAL: m->alb = vv(0.05, 0.055, 0.05); m->f0 = vs(0.08); m->a = 0.35; break;
    case M_LAMPGLASS: m->alb = vv(0.6, 0.6, 0.55); m->a = 0.2; break;
    case M_SIG_HOUSING: m->alb = vv(0.5, 0.38, 0.05); m->a = 0.45; m->f0 = vs(0.05); break;
    case M_SIG_RED: m->alb = vv(0.3, 0.02, 0.01); m->a = 0.1; m->emit = vv(9.0, 0.28, 0.08); break;
    case M_BIRD: m->alb = vv(0.1, 0.1, 0.11); m->a = 0.7; break;
    case M_SIG_OFF: m->alb = vv(0.02, 0.02, 0.02); m->a = 0.1; m->f0 = vs(0.05); break;
    default: break;
    }
    m->n = dir_to_world(o, n);
}

static void shade(const Hit *h, V d, Mat *m) {
    mat_init(m, h->n);
    if (h->type == HT_GROUND) { shade_ground(h->p, m); return; }
    if (h->type == HT_BOX) {
        const Prim *p = &g_prims[h->prim];
        if (p->sub == BX_SIDEWALK) { shade_sidewalk(p, h->face, h->p, m); return; }
        const Bld *b = &g_bld[p->obj];
        if (p->sub == BX_CORNICE) {
            m->alb = b->style == ST_BRICK ? vv(0.34, 0.31, 0.27) : b->trim;
            if (h->face == 3) m->alb = mul(m->alb, 0.6);
            m->a = 0.75;
            return;
        }
        if (h->face == 2) {
            m->alb = mul(vv(0.27, 0.26, 0.25), 0.85 + 0.25 * fbm2(h->p.x * 0.3, h->p.z * 0.3, 2)); m->a = 0.9;
            return;
        }
        shade_facade(p, b, h->face, h->p, d, h->n, m);
        return;
    }
    const Sobj *o = &g_sobj[g_prims[h->prim].obj];
    int mat = M_NONE;
    sdf_eval(o, h->lp, &mat);
    V nl = vv(dot(h->n, o->F), h->n.y, dot(h->n, o->Z));
    shade_sobj(o, h->lp, nl, mat, m);
}

/* ---- BRDF: Lambert + GGX (Smith height-correlated), Schlick Fresnel ---- */
static inline double D_ggx(double NoH, double a) { double a2 = a * a, d = NoH * NoH * (a2 - 1.0) + 1.0; return a2 / (PI * d * d); }
static inline double Vis_smith(double NoV, double NoL, double a) {
    double a2 = a * a;
    double gv = NoL * sqrt(NoV * NoV * (1.0 - a2) + a2), gl = NoV * sqrt(NoL * NoL * (1.0 - a2) + a2);
    return 0.5 / fmax(gv + gl, 1e-12);
}
static inline V F_schlick(V f0, double c) { double f = pow(1.0 - clampd(c, 0, 1), 5.0); return add(f0, mul(sub(vs(1), f0), f)); }

static V brdf_eval(const Mat *m, V wo, V wi) {
    V n = m->n;
    double NoL = dot(n, wi), NoV = dot(n, wo);
    if (NoL <= 0 || NoV <= 0) return vs(0);
    V h = norm(add(wo, wi));
    double NoH = fmax(dot(n, h), 0.0), VoH = fmax(dot(wo, h), 0.0);
    V F = F_schlick(m->f0, VoH);
    V spec = mul(F, D_ggx(NoH, m->a) * Vis_smith(NoV, NoL, m->a));
    V diff = mul(m->alb, INV_PI * (1.0 - maxc(F_schlick(m->f0, NoV))));
    return add(diff, spec);
}
static inline double G1_smith(double NoV, double a) { return 2.0 * NoV / (NoV + sqrt(a * a + (1.0 - a * a) * NoV * NoV)); }

/* BRDF importance sampling: visible-normal GGX (Heitz 2018) mixed with a cosine lobe */
static int brdf_sample(const Mat *m, V wo, Rng *rng, V *wi, V *weight) {
    V n = m->n;
    double NoV = dot(n, wo);
    if (NoV <= 1e-6) return 0;
    double Fs = maxc(F_schlick(m->f0, NoV)), dl = lum(m->alb) * (1.0 - Fs);
    double ps = clampd(Fs / (Fs + dl + 1e-6), 0.08, 0.92);
    V t, b;
    onb(n, &t, &b);
    double a = m->a;
    if (rnd(rng) < ps) {
        V Ve = vv(dot(wo, t), dot(wo, b), NoV);
        V Vh = norm(vv(a * Ve.x, a * Ve.y, Ve.z));
        double l2 = Vh.x * Vh.x + Vh.y * Vh.y;
        V T1 = l2 > 0 ? mul(vv(-Vh.y, Vh.x, 0), 1.0 / sqrt(l2)) : vv(1, 0, 0);
        V T2 = cross(Vh, T1);
        double r = sqrt(rnd(rng)), ph = 2 * PI * rnd(rng);
        double t1 = r * cos(ph), t2 = r * sin(ph), s = 0.5 * (1.0 + Vh.z);
        t2 = (1.0 - s) * sqrt(fmax(0.0, 1.0 - t1 * t1)) + s * t2;
        V Nh = add(add(mul(T1, t1), mul(T2, t2)), mul(Vh, sqrt(fmax(0.0, 1.0 - t1 * t1 - t2 * t2))));
        V Ne = norm(vv(a * Nh.x, a * Nh.y, fmax(1e-6, Nh.z)));
        V hv = add(add(mul(t, Ne.x), mul(b, Ne.y)), mul(n, Ne.z));
        *wi = sub(mul(hv, 2.0 * dot(wo, hv)), wo);
    } else {
        double u1 = rnd(rng), u2 = rnd(rng), r = sqrt(u1), ph = 2 * PI * u2;
        *wi = add(add(mul(t, r * cos(ph)), mul(b, r * sin(ph))), mul(n, sqrt(fmax(0.0, 1.0 - u1))));
    }
    double NoL = dot(n, *wi);
    if (NoL <= 1e-6) return 0;
    V h = norm(add(wo, *wi));
    double NoH = fmax(dot(n, h), 0.0);
    double pdf_s = G1_smith(NoV, a) * D_ggx(NoH, a) / (4.0 * NoV);
    double pdf = ps * pdf_s + (1.0 - ps) * NoL * INV_PI;
    if (pdf <= 1e-12) return 0;
    *weight = mul(brdf_eval(m, wo, *wi), NoL / pdf);
    return 1;
}

static V sample_sun(Rng *rng) {
    double ct = 1.0 - rnd(rng) * (1.0 - g_sun_cos), st = sqrt(fmax(0.0, 1.0 - ct * ct)), ph = 2 * PI * rnd(rng);
    V t, b;
    onb(g_sun_dir, &t, &b);
    return norm(add(add(mul(t, st * cos(ph)), mul(b, st * sin(ph))), mul(g_sun_dir, ct)));
}

/* =========================================================================
 * Light transport
 * ========================================================================= */
/* haze along a segment: returns transmittance, writes in-scattered radiance */
static double fog_segment(V o, V d, double t, int shafts, Rng *rng, V *Lin) {
    double tau = fog_tau(o, d, t);
    double T = exp(-tau), w = 1.0 - T;
    V L = mul(g_amb, w * g_fog_alb);
    if (shafts && w > 1e-5) {
        double taus = -log(1.0 - rnd(rng) * w);
        double ts = fog_invert(o, d, taus);
        if (ts < t && ts < 1e6) {
            V ps = madd(o, d, ts);
            if (ps.y > 0 && !intersect(ps, g_sun_dir, BIG, NULL, 1)) {
                double ph = hg_phase(dot(d, g_sun_dir), g_fog_g);
                L = add(L, mul(g_sun_E, ph * w * g_fog_alb * sun_gain(ps.y)));
            }
        }
    }
    *Lin = L;
    return T;
}

static V radiance(V o, V d, Rng *rng) {
    V L = vs(0), T = vs(1);
    int camera_like = 1;
    double min_a = 0.0; /* path regularisation: glossy lobes widen after a diffuse bounce */
    for (int depth = 0; depth < MAXDEPTH; depth++) {
        Hit h;
        int hit = intersect(o, d, BIG, &h, 0);
        V Lf;
        double Tf = fog_segment(o, d, hit ? h.t : BIG, camera_like, rng, &Lf);
        L = add(L, mulv(T, Lf));
        T = mul(T, Tf);
        if (!hit) {
            V s = sky_radiance(d);
            if (camera_like && dot(d, g_sun_dir) > g_sun_cos) s = add(s, g_sun_L);
            L = add(L, mulv(T, s));
            break;
        }
        Mat m;
        shade(&h, d, &m);
        if (m.a < min_a) m.a = min_a;
        V wo = mul(d, -1.0), gn = h.n;
        if (dot(gn, wo) < 0) gn = mul(gn, -1.0);
        if (dot(m.n, wo) <= 0) m.n = gn;
        L = add(L, mulv(T, m.emit));
        V po = madd(h.p, gn, 1.5e-3 + h.t * 2e-5);
        if (maxc(m.alb) > 0 || !m.glass) {
            V wl = sample_sun(rng);
            double NoL = dot(m.n, wl);
            if (NoL > 0 && dot(gn, wl) > 0 && m.sunvis > 0) {
                V f = m.glass ? mul(m.alb, INV_PI) : brdf_eval(&m, wo, wl);
                if (maxc(f) > 0 && !intersect(po, wl, BIG, NULL, 1))
                    L = add(L, mulv(T, mulv(f, mul(g_sun_E, NoL * m.sunvis * sun_gain(h.p.y)))));
            }
        }
        if (m.glass) {
            T = mulv(T, m.refl);
            V r = reflect(d, m.n);
            if (dot(r, gn) <= 0) r = reflect(d, gn);
            o = po; d = r;
            if (maxc(T) < 2e-3) break;
            continue;
        }
        V wi, wgt;
        if (!brdf_sample(&m, wo, rng, &wi, &wgt)) break;
        if (dot(wi, gn) <= 0) break;
        T = mulv(T, mul(wgt, m.ao));
        o = po; d = wi; camera_like = 0;
        min_a = 0.3;
        if (depth >= 1) {
            double q = clampd(maxc(T), 0.05, 0.95);
            if (rnd(rng) > q) break;
            T = mul(T, 1.0 / q);
        }
    }
    return L;
}

/* =========================================================================
 * Camera
 * ========================================================================= */
static V cam_o, cam_f, cam_r, cam_u;
static double cam_tan, cam_aspect;

static void setup_camera(void) {
    cam_o = vv(CAM[0], CAM[1], CAM[2]);
    cam_f = norm(sub(vv(CAM[3], CAM[4], CAM[5]), cam_o));
    cam_r = norm(cross(vv(0, 1, 0), cam_f));
    cam_u = cross(cam_f, cam_r);
    cam_tan = tan(CAM[6] * 0.5 * PI / 180.0);
    cam_aspect = (double)W / H;
}
static void camera_ray(double px, double py, Rng *rng, V *o, V *d) {
    double sx = (2.0 * px / W - 1.0) * cam_tan * cam_aspect, sy = (1.0 - 2.0 * py / H) * cam_tan;
    V dir = norm(add(cam_f, add(mul(cam_r, sx), mul(cam_u, sy))));
    if (LENS_R > 0) {
        V focal = madd(cam_o, dir, FOCUS / dot(dir, cam_f));
        double r = LENS_R * sqrt(rnd(rng)), ph = 2 * PI * rnd(rng);
        V lo = add(cam_o, add(mul(cam_r, r * cos(ph)), mul(cam_u, r * sin(ph))));
        *o = lo; *d = norm(sub(focal, lo));
    } else { *o = cam_o; *d = dir; }
}

/* =========================================================================
 * Film: bloom, chromatic aberration, vignette, ACES, sRGB, grain, PNG
 * ========================================================================= */
static void gblur(const float *src, float *dst, int w, int h, double sigma) {
    int r = (int)ceil(sigma * 3.0);
    double *k = malloc(sizeof(double) * (size_t)(2 * r + 1)), ks = 0;
    for (int i = -r; i <= r; i++) { k[i + r] = exp(-0.5 * i * i / (sigma * sigma)); ks += k[i + r]; }
    for (int i = 0; i <= 2 * r; i++) k[i] /= ks;
    float *tmp = malloc(sizeof(float) * (size_t)w * h * 3);
#pragma omp parallel for
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int c = 0; c < 3; c++) {
                double s = 0;
                for (int i = -r; i <= r; i++) { int xx = x + i < 0 ? 0 : (x + i >= w ? w - 1 : x + i); s += k[i + r] * src[((size_t)y * w + xx) * 3 + c]; }
                tmp[((size_t)y * w + x) * 3 + c] = (float)s;
            }
#pragma omp parallel for
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int c = 0; c < 3; c++) {
                double s = 0;
                for (int i = -r; i <= r; i++) { int yy = y + i < 0 ? 0 : (y + i >= h ? h - 1 : y + i); s += k[i + r] * tmp[((size_t)yy * w + x) * 3 + c]; }
                dst[((size_t)y * w + x) * 3 + c] = (float)s;
            }
    free(tmp); free(k);
}
static V img_bilinear(const float *img, int w, int h, double x, double y) {
    x = clampd(x, 0, w - 1.001); y = clampd(y, 0, h - 1.001);
    int x0 = (int)x, y0 = (int)y;
    double tx = x - x0, ty = y - y0;
    const float *a = img + ((size_t)y0 * w + x0) * 3, *b = a + 3, *c = a + (size_t)w * 3, *d = c + 3;
    return vv(mixd(mixd(a[0], b[0], tx), mixd(c[0], d[0], tx), ty), mixd(mixd(a[1], b[1], tx), mixd(c[1], d[1], tx), ty),
              mixd(mixd(a[2], b[2], tx), mixd(c[2], d[2], tx), ty));
}
static V aces(V c) {
    V v = vv(0.59719 * c.x + 0.35458 * c.y + 0.04823 * c.z, 0.07600 * c.x + 0.90834 * c.y + 0.01566 * c.z,
             0.02840 * c.x + 0.13383 * c.y + 0.83777 * c.z);
    V a = vv(v.x * (v.x + 0.0245786) - 0.000090537, v.y * (v.y + 0.0245786) - 0.000090537, v.z * (v.z + 0.0245786) - 0.000090537);
    V b = vv(v.x * (0.983729 * v.x + 0.4329510) + 0.238081, v.y * (0.983729 * v.y + 0.4329510) + 0.238081,
             v.z * (0.983729 * v.z + 0.4329510) + 0.238081);
    v = vv(a.x / b.x, a.y / b.y, a.z / b.z);
    return vv(1.60475 * v.x - 0.53108 * v.y - 0.07367 * v.z, -0.10208 * v.x + 1.10813 * v.y - 0.00605 * v.z,
              -0.00327 * v.x - 0.07276 * v.y + 1.07602 * v.z);
}
static double srgb(double c) {
    c = clampd(c, 0.0, 1.0);
    return c <= 0.0031308 ? 12.92 * c : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

static void develop(const float *hdr, uint8_t *out, int w, int h) {
    /* veiling glare: a small fraction of the image spread by a wide, heavy-tailed PSF */
    int f = 4, bw = (w + f - 1) / f, bh = (h + f - 1) / f;
    float *small = calloc((size_t)bw * bh * 3, sizeof(float)), *blur = malloc(sizeof(float) * (size_t)bw * bh * 3);
    float *glare = calloc((size_t)bw * bh * 3, sizeof(float));
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int c = 0; c < 3; c++) small[((size_t)(y / f) * bw + x / f) * 3 + c] += hdr[((size_t)y * w + x) * 3 + c] / (f * f);
    const double sig[3] = {1.5, 5.0, 16.0}, wt[3] = {0.5, 0.3, 0.2};
    for (int s = 0; s < 3; s++) {
        gblur(small, blur, bw, bh, sig[s] * bw / 480.0);
        for (size_t i = 0; i < (size_t)bw * bh * 3; i++) glare[i] += (float)(wt[s] * blur[i]);
    }
    const double kg = 0.045, ca = 0.0007;
    Rng grain = {987654321};
#pragma omp parallel for
    for (int y = 0; y < h; y++) {
        Rng g = {grain.s + (uint64_t)y * 0x9E3779B97F4A7C15ULL};
        for (int x = 0; x < w; x++) {
            double cx = (x + 0.5) - w * 0.5, cy = (y + 0.5) - h * 0.5;
            V c;
            c.x = img_bilinear(hdr, w, h, w * 0.5 + cx * (1 + ca) - 0.5, h * 0.5 + cy * (1 + ca) - 0.5).x;
            c.y = hdr[((size_t)y * w + x) * 3 + 1];
            c.z = img_bilinear(hdr, w, h, w * 0.5 + cx * (1 - ca) - 0.5, h * 0.5 + cy * (1 - ca) - 0.5).z;
            V gl = img_bilinear(glare, bw, bh, (x + 0.5) / f - 0.5, (y + 0.5) / f - 0.5);
            c = add(mul(c, 1.0 - kg), mul(gl, kg));
            double sx = (2.0 * (x + 0.5) / w - 1.0) * cam_tan * cam_aspect, sy = (2.0 * (y + 0.5) / h - 1.0) * cam_tan;
            double c2 = 1.0 / (1.0 + sx * sx + sy * sy);
            c = mul(c, EXPOSURE * mixd(1.0, c2 * c2, 0.55));
            c = aces(c);
            double gn = (rnd(&g) + rnd(&g) + rnd(&g) - 1.5) * 0.018;
            for (int k = 0; k < 3; k++) {
                double val = k == 0 ? c.x : (k == 1 ? c.y : c.z);
                double e = srgb(val);
                e += gn * sqrt(fmax(e, 0.0)) * (1.0 - e) + (rnd(&g) - 0.5) / 255.0;
                out[((size_t)y * w + x) * 3 + k] = (uint8_t)clampd(e * 255.0 + 0.5, 0, 255);
            }
        }
    }
    free(small); free(blur); free(glare);
}

static void png_chunk(FILE *f, const char *type, const uint8_t *data, uint32_t n) {
    uint8_t b[4] = {(uint8_t)(n >> 24), (uint8_t)(n >> 16), (uint8_t)(n >> 8), (uint8_t)n};
    fwrite(b, 1, 4, f); fwrite(type, 1, 4, f);
    if (n) fwrite(data, 1, n, f);
    uLong crc = crc32(0L, (const Bytef *)type, 4);
    if (n) crc = crc32(crc, data, n);
    uint8_t c[4] = {(uint8_t)(crc >> 24), (uint8_t)(crc >> 16), (uint8_t)(crc >> 8), (uint8_t)crc};
    fwrite(c, 1, 4, f);
}
static int write_png(const char *fn, const uint8_t *rgb, int w, int h) {
    size_t stride = (size_t)w * 3, rawn = (stride + 1) * h;
    uint8_t *raw = malloc(rawn);
    for (int y = 0; y < h; y++) {
        uint8_t *row = raw + (size_t)y * (stride + 1);
        const uint8_t *src = rgb + (size_t)y * stride;
        row[0] = 1; /* Sub filter */
        for (size_t i = 0; i < stride; i++) row[1 + i] = (uint8_t)(src[i] - (i >= 3 ? src[i - 3] : 0));
    }
    uLongf cn = compressBound(rawn);
    uint8_t *comp = malloc(cn);
    compress2(comp, &cn, raw, rawn, 9);
    FILE *f = fopen(fn, "wb");
    if (!f) { free(raw); free(comp); return 0; }
    static const uint8_t sig[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    fwrite(sig, 1, 8, f);
    uint8_t ihdr[13] = {(uint8_t)(w >> 24), (uint8_t)(w >> 16), (uint8_t)(w >> 8), (uint8_t)w,
                        (uint8_t)(h >> 24), (uint8_t)(h >> 16), (uint8_t)(h >> 8), (uint8_t)h, 8, 2, 0, 0, 0};
    png_chunk(f, "IHDR", ihdr, 13);
    png_chunk(f, "IDAT", comp, (uint32_t)cn);
    png_chunk(f, "IEND", NULL, 0);
    fclose(f); free(raw); free(comp);
    return 1;
}

/* =========================================================================
 * Watercolour stylisation
 * -------------------------------------------------------------------------
 * The path-traced frame is repainted, per pixel, as transparent pigment on paper:
 *   abstraction  generalised Kuwahara filter (8 soft sectors, radius grows with depth) flattens
 *                texture into washes while keeping shapes; the animals stay crisp
 *   wobble       washes are sampled through a noise displacement field, so edges look hand-made
 *   wet-in-wet   the sky, the far city and random patches bleed into a blurred copy of the wash;
 *                a darker "backrun" rim forms where a wet patch meets a dry one
 *   pigment      colour is darkened by a density factor d (Bousseau et al. 2006):
 *                    C' = C (1 - (1 - C)(d - 1))
 *                d combines edge darkening (pigment pooling at wash boundaries), turbulent flow,
 *                granulation into the paper grain and fine dispersion
 *   pencil       a faint graphite underdrawing traced from discontinuities in a primary-ray
 *                geometry buffer: object id, depth (second difference of 1/z) and normals
 *   paper        cold-press paper height field (cellular + gradient noise), embossed by a
 *                raking light, with an irregular unpainted margin
 * ========================================================================= */
typedef struct { float depth, nx, ny, nz, sun; int32_t id, kind; } GPix;
enum { GK_SKY, GK_GROUND, GK_PAVEMENT, GK_BUILDING, GK_ANIMAL, GK_TREE, GK_PROP };
static double WC_EXPOSURE = 1.9;

static void compute_gbuffer(GPix *gb, int w, int h) {
#pragma omp parallel for schedule(dynamic, 4)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            double sx = (2.0 * (x + 0.5) / w - 1.0) * cam_tan * cam_aspect, sy = (1.0 - 2.0 * (y + 0.5) / h) * cam_tan;
            V d = norm(add(cam_f, add(mul(cam_r, sx), mul(cam_u, sy))));
            GPix *g = gb + (size_t)y * w + x;
            Hit hh;
            if (!intersect(cam_o, d, BIG, &hh, 0)) {
                g->depth = 1e9f; g->nx = (float)-d.x; g->ny = (float)-d.y; g->nz = (float)-d.z;
                g->id = 0; g->kind = GK_SKY; g->sun = 1.0f;
                continue;
            }
            V nn = dot(hh.n, d) > 0 ? mul(hh.n, -1.0) : hh.n;
            g->depth = (float)hh.t; g->nx = (float)nn.x; g->ny = (float)nn.y; g->nz = (float)nn.z;
            /* is this point in sunlight? (drives warm lights / cool shadows) */
            double NoL = dot(nn, g_sun_dir);
            g->sun = (float)(NoL > 0 && !intersect(madd(hh.p, nn, 2e-3 + hh.t * 2e-5), g_sun_dir, BIG, NULL, 1) ? sstep(0.0, 0.12, NoL) : 0.0);
            if (hh.type == HT_GROUND) { g->id = 1; g->kind = GK_GROUND; }
            else if (hh.type == HT_BOX) {
                const Prim *p = &g_prims[hh.prim];
                if (p->sub == BX_SIDEWALK) { g->id = 10 + hh.prim; g->kind = GK_PAVEMENT; }
                else { g->id = 100000 + p->obj; g->kind = GK_BUILDING; }
            } else {
                const Sobj *o = &g_sobj[g_prims[hh.prim].obj];
                g->id = 200000 + (int)(o - g_sobj);
                g->kind = o->kind <= SK_ZEBRA ? GK_ANIMAL : (o->kind == SK_TREE ? GK_TREE : GK_PROP);
            }
        }
}

/* generalised Kuwahara filter (Papari et al. 2007) with a per-pixel radius */
typedef struct { short dx, dy, k0, k1; float w0, w1; } KTap;
#define KMAXR 9
static void kuwahara(const float *src, float *dst, const float *rad, int w, int h) {
    KTap *taps[KMAXR + 1];
    int ntap[KMAXR + 1];
    for (int r = 1; r <= KMAXR; r++) {
        taps[r] = malloc(sizeof(KTap) * (size_t)(2 * r + 1) * (2 * r + 1));
        ntap[r] = 0;
        double sig = 0.5 * r;
        for (int dy = -r; dy <= r; dy++)
            for (int dx = -r; dx <= r; dx++) {
                if (dx * dx + dy * dy > r * r) continue;
                double g = exp(-(dx * dx + dy * dy) / (2 * sig * sig));
                KTap t;
                t.dx = (short)dx; t.dy = (short)dy;
                if (dx == 0 && dy == 0) { t.k0 = t.k1 = -1; t.w0 = (float)(g / 8.0); t.w1 = 0; }
                else {
                    double s = atan2((double)dy, (double)dx) / (2 * PI / 8);
                    if (s < 0) s += 8;
                    int k0 = (int)floor(s);
                    double f = s - k0;
                    t.k0 = (short)(k0 % 8); t.k1 = (short)((k0 + 1) % 8);
                    t.w0 = (float)(g * (1 - f)); t.w1 = (float)(g * f);
                }
                taps[r][ntap[r]++] = t;
            }
    }
#pragma omp parallel for schedule(dynamic, 4)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            int r = (int)(rad[(size_t)y * w + x] + 0.5);
            r = r < 1 ? 1 : (r > KMAXR ? KMAXR : r);
            double m[8][3] = {{0}}, s2[8] = {0}, ws[8] = {0};
            for (int i = 0; i < ntap[r]; i++) {
                const KTap *t = &taps[r][i];
                int xx = x + t->dx, yy = y + t->dy;
                xx = xx < 0 ? 0 : (xx >= w ? w - 1 : xx);
                yy = yy < 0 ? 0 : (yy >= h ? h - 1 : yy);
                const float *c = src + ((size_t)yy * w + xx) * 3;
                double q = c[0] * c[0] + c[1] * c[1] + c[2] * c[2];
                if (t->k0 < 0) {
                    for (int k = 0; k < 8; k++) { m[k][0] += t->w0 * c[0]; m[k][1] += t->w0 * c[1]; m[k][2] += t->w0 * c[2]; s2[k] += t->w0 * q; ws[k] += t->w0; }
                } else {
                    m[t->k0][0] += t->w0 * c[0]; m[t->k0][1] += t->w0 * c[1]; m[t->k0][2] += t->w0 * c[2]; s2[t->k0] += t->w0 * q; ws[t->k0] += t->w0;
                    m[t->k1][0] += t->w1 * c[0]; m[t->k1][1] += t->w1 * c[1]; m[t->k1][2] += t->w1 * c[2]; s2[t->k1] += t->w1 * q; ws[t->k1] += t->w1;
                }
            }
            double num[3] = {0, 0, 0}, den = 0;
            for (int k = 0; k < 8; k++) {
                if (ws[k] <= 0) continue;
                double mr = m[k][0] / ws[k], mg = m[k][1] / ws[k], mb = m[k][2] / ws[k];
                double var = fmax(s2[k] / ws[k] - (mr * mr + mg * mg + mb * mb), 0.0);
                double a = 1.0 / (1.0 + pow(var * 600.0, 4.0));
                num[0] += a * mr; num[1] += a * mg; num[2] += a * mb; den += a;
            }
            float *o = dst + ((size_t)y * w + x) * 3;
            o[0] = (float)(num[0] / den); o[1] = (float)(num[1] / den); o[2] = (float)(num[2] / den);
        }
    for (int r = 1; r <= KMAXR; r++) free(taps[r]);
}

static double paper_height(double x, double y) {
    double f1 = voronoi2(x / 6.0, y / 6.0, NULL);
    return 0.5 * (1.0 - fmin(f1, 1.0)) + 0.3 * (0.5 + 0.5 * noise2(x / 2.6 + 11.0, y / 2.6)) +
           0.2 * (0.5 + 0.5 * fbm2(x / 16.0, y / 16.0 - 3.0, 2));
}
static inline int clampi(int v, int a, int b) { return v < a ? a : (v > b ? b : v); }

static void watercolor(const float *hdr, const GPix *gb, uint8_t *out, int w, int h) {
    size_t n = (size_t)w * h;
    float *base = malloc(n * 3 * sizeof(float)), *rad = malloc(n * sizeof(float));
    float *wash = malloc(n * 3 * sizeof(float)), *wet = malloc(n * 3 * sizeof(float));
    float *comp = malloc(n * 3 * sizeof(float)), *wetm = malloc(n * sizeof(float));
    float *lum_ = malloc(n * 3 * sizeof(float)), *lumb = malloc(n * 3 * sizeof(float));
    float *line = malloc(n * 3 * sizeof(float)), *lineb = malloc(n * 3 * sizeof(float));

    /* 1. high-key watercolour palette: soft shoulder, lifted darks, cool shadows, warm light */
    float *sunm = malloc(n * 3 * sizeof(float)), *sunb = malloc(n * 3 * sizeof(float));
    for (size_t i = 0; i < n; i++) sunm[3 * i] = sunm[3 * i + 1] = sunm[3 * i + 2] = gb[i].sun;
    gblur(sunm, sunb, w, h, 1.5);
#pragma omp parallel for
    for (size_t i = 0; i < n; i++) {
        V c = mul(vv(hdr[3 * i], hdr[3 * i + 1], hdr[3 * i + 2]), WC_EXPOSURE);
        c = vv(srgb(1.0 - exp(-c.x)), srgb(1.0 - exp(-c.y)), srgb(1.0 - exp(-c.z)));
        double px = (double)(i % (size_t)w), py = (double)(i / (size_t)w);
        c = mulv(c, vv(1.0 + 0.07 * noise2(px / 260.0 + 1.7, py / 260.0), 1.0 + 0.05 * noise2(px / 260.0, py / 260.0 + 4.1),
                       1.0 + 0.08 * noise2(px / 260.0 - 3.9, py / 260.0 - 2.2)));
        double sun = sunb[3 * i], L = lum(c);
        c = mixv(c, mulv(c, vv(0.8, 0.87, 1.24)), 0.8 * (1.0 - sun) * sstep(0.95, 0.4, L)); /* ultramarine shadows */
        c = mixv(c, mulv(c, vv(1.07, 1.01, 0.9)), 0.6 * sun);                                /* warm sunlit washes */
        L = lum(c);
        c = add(vs(L), mul(sub(c, vs(L)), 1.0 + 0.35 * sstep(0.3, 0.7, L)));                /* luminous, not muddy */
        c = add(vs(0.16), mul(c, 0.84));
        base[3 * i] = (float)clampd(c.x, 0, 1); base[3 * i + 1] = (float)clampd(c.y, 0, 1); base[3 * i + 2] = (float)clampd(c.z, 0, 1);
        const GPix *g = gb + i;
        double r = 4.0 + 5.0 * sstep(10.0, 120.0, g->depth);
        if (g->kind == GK_ANIMAL) r = 3.5;
        if (g->kind == GK_TREE) r = fmax(r, 6.0);
        if (g->kind == GK_SKY) r = 9.0;
        rad[i] = (float)r;
    }
    /* 2. abstraction into washes */
    kuwahara(base, wash, rad, w, h);
    /* 3. glazes: soft luminance bands with wandering thresholds, like layers of dried washes */
#pragma omp parallel for schedule(dynamic, 8)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            float *c = wash + 3 * ((size_t)y * w + x);
            double L = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
            double v = L * 6.0 + 0.55 * fbm2(x / 70.0, y / 70.0 + 5.0, 3);
            double q = (floor(v) + sstep(0.35, 0.65, fractd(v)) - 0.55 * fbm2(x / 70.0, y / 70.0 + 5.0, 3)) / 6.0;
            double k = mixd(1.0, clampd(q, 0.02, 1.0) / fmax(L, 0.02), 0.6);
            for (int j = 0; j < 3; j++) c[j] = (float)clampd(c[j] * k, 0.0, 1.0);
        }
    gblur(wash, wet, w, h, 3.5);
    /* 4. wobbled sampling, wet-in-wet blending */
#pragma omp parallel for schedule(dynamic, 8)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            size_t i = (size_t)y * w + x;
            double wx = x + 2.6 * noise2(x / 36.0, y / 36.0) + 1.0 * noise2(x / 8.0 + 5.1, y / 8.0);
            double wy = y + 2.6 * noise2(x / 36.0 + 17.3, y / 36.0 - 8.1) + 1.0 * noise2(x / 8.0 - 2.7, y / 8.0 + 4.4);
            V a = img_bilinear(wash, w, h, wx, wy);
            V b = img_bilinear(wet, w, h, wx + 7.0 * noise2(x / 45.0 - 3.3, y / 45.0), wy + 7.0 * noise2(x / 45.0, y / 45.0 + 6.6));
            const GPix *g = gb + i;
            double far = g->kind == GK_SKY ? 1.0 : sstep(60.0, 600.0, g->depth);
            double wm = sstep(0.4, 0.6, 0.3 + 0.55 * fbm2(x / 200.0, y / 200.0, 4) + 0.45 * far);
            if (g->kind == GK_ANIMAL) wm *= 0.2;
            V c = mixv(a, b, wm);
            if (g->kind == GK_GROUND) {
                double fg = sstep(0.74, 1.0, (double)y / h + 0.06 * fbm2(x / 90.0, y / 90.0, 3)) * (1.0 - 0.6 * sstep(0.46, 0.6, (double)x / w));
                c = mixv(c, vs(1.0), 0.6 * fg);
            }
            comp[3 * i] = (float)c.x; comp[3 * i + 1] = (float)c.y; comp[3 * i + 2] = (float)c.z;
            wetm[i] = (float)wm;
            float L = (float)lum(c);
            lum_[3 * i] = lum_[3 * i + 1] = lum_[3 * i + 2] = L;
        }
    gblur(lum_, lumb, w, h, 1.0);
    /* 5. pencil underdrawing (offset from the paint) and paper gaps between neighbouring washes */
#pragma omp parallel for schedule(dynamic, 8)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            size_t i = (size_t)y * w + x;
            int sx = clampi(x + (int)lround(1.6 * noise2(x / 55.0 + 40.0, y / 55.0)), 1, w - 2);
            int sy = clampi(y + (int)lround(1.6 * noise2(x / 55.0, y / 55.0 - 40.0)), 1, h - 2);
            const GPix *c = gb + (size_t)sy * w + sx, *l = c - 1, *r = c + 1, *u = c - w, *dn = c + w;
            double e = (c->id != l->id || c->id != r->id || c->id != u->id || c->id != dn->id) ? 1.0 : 0.0;
            double dd = fabs(c->depth / r->depth + c->depth / l->depth - 2.0) + fabs(c->depth / u->depth + c->depth / dn->depth - 2.0);
            e = fmax(e, sstep(0.08, 0.25, dd));
            double nd = (1.0 - (l->nx * r->nx + l->ny * r->ny + l->nz * r->nz)) + (1.0 - (u->nx * dn->nx + u->ny * dn->ny + u->nz * dn->nz));
            e = fmax(e, sstep(0.15, 0.45, nd));
            double dm = fmin(fmin(c->depth, l->depth), fmin(r->depth, fmin(u->depth, dn->depth)));
            e *= sstep(300.0, 50.0, dm);
            if (c->kind == GK_TREE) e *= 0.25;
            e *= 0.15 + 0.85 * sstep(-0.25, 0.4, noise2(x / 38.0, y / 38.0 + 9.0));
            line[3 * i] = (float)e;
            /* a hair-line of bare paper where two objects' washes meet (sampled with its own offset) */
            int gx2 = clampi(x + (int)lround(2.2 * noise2(x / 30.0 - 12.0, y / 30.0)), 1, w - 2);
            int gy2 = clampi(y + (int)lround(2.2 * noise2(x / 30.0, y / 30.0 + 12.0)), 1, h - 2);
            const GPix *c2 = gb + (size_t)gy2 * w + gx2;
            double gap = (c2->id != (c2 - 1)->id || c2->id != (c2 + 1)->id || c2->id != (c2 - w)->id || c2->id != (c2 + w)->id) ? 1.0 : 0.0;
            gap *= sstep(200.0, 40.0, c2->depth) * sstep(0.0, 0.5, noise2(x / 25.0 + 3.0, y / 25.0));
            line[3 * i + 1] = (float)gap;
            line[3 * i + 2] = 0;
        }
    gblur(line, lineb, w, h, 0.75);
    /* 6. pigment density, pencil, paper and the brushed edge of the painting */
    const V paper = vv(0.97, 0.955, 0.92), graphite = vv(0.34, 0.33, 0.36);
    enum { NSPLAT = 26 };
    double spx[NSPLAT], spy[NSPLAT], spr[NSPLAT];
    V spc[NSPLAT];
    {
        Rng sr = {424242};
        for (int k = 0; k < NSPLAT; k++) {
            double u = rnd(&sr);
            spx[k] = (0.04 + 0.92 * rnd(&sr)) * w;
            spy[k] = (0.3 + 0.66 * sqrt(rnd(&sr))) * h;
            spr[k] = 1.5 + 6.0 * u * u * u;
            V sc = img_bilinear(comp, w, h, clampd(spx[k] + 40.0 * (rnd(&sr) - 0.5), 0, w - 1), clampd(spy[k] - 25.0 * rnd(&sr), 0, h - 1));
            double sl = lum(sc);
            spc[k] = add(vs(sl), mul(sub(sc, vs(sl)), 1.6)); /* the same pigment as nearby, more saturated */
        }
    }
#pragma omp parallel for schedule(dynamic, 8)
    for (int y = 0; y < h; y++) {
        Rng dith = {(uint64_t)y * 0x9E3779B97F4A7C15ULL + 77};
        for (int x = 0; x < w; x++) {
            size_t i = (size_t)y * w + x;
            const GPix *g = gb + i;
            int xl = x > 0 ? x - 1 : x, xr = x < w - 1 ? x + 1 : x, yu = y > 0 ? y - 1 : y, yd = y < h - 1 ? y + 1 : y;
            double gx = lumb[3 * ((size_t)y * w + xr)] - lumb[3 * ((size_t)y * w + xl)];
            double gy = lumb[3 * ((size_t)yd * w + x)] - lumb[3 * ((size_t)yu * w + x)];
            double gmag = 0.5 * sqrt(gx * gx + gy * gy);
            double hp = paper_height(x, y);
            /* brush direction: vertical on façades, horizontal on the ground and in the sky */
            double stroke = (g->kind == GK_BUILDING && fabs(g->ny) < 0.5) ? noise2(x / 13.0, y / 240.0) : noise2(x / 260.0, y / 11.0);
            double d = 1.0;
            d *= 1.0 + 0.8 * sstep(0.006, 0.05, gmag);                       /* pooling at wash edges */
            d *= 1.0 + 0.42 * fbm2(x / 160.0 + 3.0, y / 160.0, 4);           /* turbulent flow */
            d *= 1.0 + 0.8 * (0.5 - hp);                                     /* granulation */
            d *= 1.0 + 0.14 * stroke;                                        /* brush marks */
            d *= 1.0 + 0.08 * noise2(x / 1.6, y / 1.6);                      /* dispersion */
            d *= 1.0 + 0.4 * exp(-sq((wetm[i] - 0.5) / 0.07));               /* backrun rims */
            V c = vv(comp[3 * i], comp[3 * i + 1], comp[3 * i + 2]);
            double L0 = lum(c);
            c = vv(c.x * (1 - (1 - c.x) * (d - 1)), c.y * (1 - (1 - c.y) * (d - 1)), c.z * (1 - (1 - c.z) * (d - 1)));
            c = vv(clampd(c.x, 0, 1), clampd(c.y, 0, 1), clampd(c.z, 0, 1));
            c = mixv(c, vs(1.0), sstep(0.8, 0.93, L0));                      /* highlights are bare paper */
            for (int k = 0; k < NSPLAT; k++) {                                   /* paint splatters */
                double sd = sqrt(sq(x - spx[k]) + sq(y - spy[k])) - spr[k] * (1.0 + 0.25 * noise2(x / 2.0 + k, y / 2.0));
                if (sd < 1.0) c = mixv(c, mulv(c, mul(spc[k], sd > -1.0 ? 0.9 : 1.0)), 0.55 * sstep(1.0, -0.5, sd));
            }
            c = mixv(c, vs(1.0), 0.6 * clampd(lineb[3 * i + 1], 0, 1));      /* gaps between washes */
            c = mulv(c, mixv(vs(1.0), graphite, clampd(lineb[3 * i] * 0.32, 0.0, 0.45)));
            /* crisp, irregular edge where the painting stops, dry-brushed on the paper grain */
            double edge = fmin(fmin(x, w - 1 - x), fmin(y, h - 1 - y)) / (double)h;
            double corner = sq((x - 0.5 * w) / (0.6 * w)) + sq((y - 0.5 * h) / (0.62 * h));
            double reach = fmin(edge - 0.035, (1.18 - corner) * 0.12) + 0.022 * fbm2(x / 55.0, y / 55.0, 5);
            double mpaint = sstep(0.0, 0.004, reach) * sstep(0.25, 0.6, hp + reach * 18.0);
            double rim = sstep(0.0, 0.006, reach) * sstep(0.02, 0.008, reach);
            c = vv(c.x * (1 - (1 - c.x) * 0.5 * rim), c.y * (1 - (1 - c.y) * 0.5 * rim), c.z * (1 - (1 - c.z) * 0.5 * rim));
            c = mixv(vs(1.0), c, mpaint);
            double shade = 1.0 + 0.06 * (paper_height(x - 1.0, y - 1.0) - paper_height(x + 1.0, y + 1.0));
            V f = mul(mulv(paper, c), shade);
            out[3 * i] = (uint8_t)clampd(f.x * 255.0 + rnd(&dith), 0, 255);
            out[3 * i + 1] = (uint8_t)clampd(f.y * 255.0 + rnd(&dith), 0, 255);
            out[3 * i + 2] = (uint8_t)clampd(f.z * 255.0 + rnd(&dith), 0, 255);
        }
    }
    free(base); free(rad); free(wash); free(wet); free(comp); free(wetm); free(lum_); free(lumb); free(line); free(lineb);
    free(sunm); free(sunb);
}

/* =========================================================================
 * Main
 * ========================================================================= */
static double now(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }

int main(int argc, char **argv) {
    const char *out = "cityscape_wildlife.png", *rawout = NULL, *rawin = NULL, *gbufpath = NULL;
    int style = 0; /* 0: photographic film, 1: watercolour */
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-w") && i + 1 < argc) W = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-h") && i + 1 < argc) H = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-spp") && i + 1 < argc) SPP = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-depth") && i + 1 < argc) MAXDEPTH = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-scene") && i + 1 < argc) SCENE = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-exp") && i + 1 < argc) EXPOSURE = atof(argv[++i]);
        else if (!strcmp(argv[i], "-sun") && i + 2 < argc) { SUN_AZ = atof(argv[++i]); SUN_EL = atof(argv[++i]); }
        else if (!strcmp(argv[i], "-lens") && i + 2 < argc) { LENS_R = atof(argv[++i]); FOCUS = atof(argv[++i]); }
        else if (!strcmp(argv[i], "-cam") && i + 7 < argc) { for (int k = 0; k < 7; k++) CAM[k] = atof(argv[++i]); CAM_SET = 1; }
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "-seed") && i + 1 < argc) SEED = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-raw") && i + 1 < argc) rawout = argv[++i];
        else if (!strcmp(argv[i], "-develop") && i + 1 < argc) rawin = argv[++i];
        else if (!strcmp(argv[i], "-style") && i + 1 < argc) { i++; style = !strncmp(argv[i], "water", 5); }
        else if (!strcmp(argv[i], "-gbuf") && i + 1 < argc) gbufpath = argv[++i];
        else if (!strcmp(argv[i], "-wcexp") && i + 1 < argc) WC_EXPOSURE = atof(argv[++i]);
        else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 1; }
    }
    float *img;
    if (rawin) { /* re-develop a saved linear HDR frame */
        FILE *f = fopen(rawin, "rb");
        if (!f || fread(&W, 4, 1, f) != 1 || fread(&H, 4, 1, f) != 1) { fprintf(stderr, "cannot read %s\n", rawin); return 1; }
        img = malloc(sizeof(float) * (size_t)W * H * 3);
        if (fread(img, sizeof(float), (size_t)W * H * 3, f) != (size_t)W * H * 3) { fprintf(stderr, "short read\n"); return 1; }
        fclose(f);
        setup_camera();
    } else {
        double t0 = now();
        setup_sky();
        if (SCENE == 1) {
            build_test_scene();
            if (!CAM_SET) { double c[7] = {0.5, 1.7, 0.0, -0.5, 1.9, 12.0, 42.0}; memcpy(CAM, c, sizeof c); }
        } else build_city();
        build_bvh();
        setup_camera();
        fprintf(stderr, "scene: %d prims (%d buildings, %d sdf objects), %d BVH nodes, setup %.1fs\n", g_np, g_nb, g_ns, g_nn, now() - t0);
        img = calloc((size_t)W * H * 3, sizeof(float));
        int done = 0;
        double t1 = now();
#pragma omp parallel for schedule(dynamic, 1)
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                V acc = vs(0);
                for (int s = 0; s < SPP; s++) {
                    uint64_t si = (uint64_t)s + (uint64_t)SEED * (uint64_t)SPP;
                    Rng rng = {((uint64_t)(y * W + x) * 0x9E3779B97F4A7C15ULL) ^ (si * 0xD1B54A32D192ED03ULL)};
                    rng_next(&rng);
                    double r1 = 2 * rnd(&rng), r2 = 2 * rnd(&rng);
                    double dx = r1 < 1 ? sqrt(r1) - 1 : 1 - sqrt(2 - r1), dy = r2 < 1 ? sqrt(r2) - 1 : 1 - sqrt(2 - r2);
                    V o, d;
                    camera_ray(x + 0.5 + dx, y + 0.5 + dy, &rng, &o, &d);
                    V L = radiance(o, d, &rng);
                    double l = lum(L);
                    if (!(l >= 0.0) || isnan(L.x) || isnan(L.y) || isnan(L.z) || isinf(l)) continue;
                    if (l > 40.0) L = mul(L, 40.0 / l);
                    acc = add(acc, L);
                }
                acc = mul(acc, 1.0 / SPP);
                float *px = img + ((size_t)y * W + x) * 3;
                px[0] = (float)acc.x; px[1] = (float)acc.y; px[2] = (float)acc.z;
            }
            int dn;
#pragma omp atomic capture
            dn = ++done;
            if (dn % 8 == 0 || dn == H) {
                double el = now() - t1;
                fprintf(stderr, "\r%5.1f%%  elapsed %6.0fs  eta %6.0fs   ", 100.0 * dn / H, el, el / dn * (H - dn));
            }
        }
        fprintf(stderr, "\nrender %.1fs\n", now() - t1);
        if (rawout) {
            FILE *f = fopen(rawout, "wb");
            if (f) { fwrite(&W, 4, 1, f); fwrite(&H, 4, 1, f); fwrite(img, sizeof(float), (size_t)W * H * 3, f); fclose(f); }
        }
    }
    uint8_t *ldr = malloc((size_t)W * H * 3);
    if (style == 1) {
        GPix *gb = malloc(sizeof(GPix) * (size_t)W * H);
        int have = 0, gw = 0, gh = 0;
        FILE *gf = gbufpath ? fopen(gbufpath, "rb") : NULL;
        if (gf) {
            have = fread(&gw, 4, 1, gf) == 1 && fread(&gh, 4, 1, gf) == 1 && gw == W && gh == H &&
                   fread(gb, sizeof(GPix), (size_t)W * H, gf) == (size_t)W * H;
            fclose(gf);
        }
        if (!have) {
            double t0 = now();
            if (rawin) { setup_sky(); if (SCENE == 1) build_test_scene(); else build_city(); build_bvh(); }
            setup_camera();
            compute_gbuffer(gb, W, H);
            fprintf(stderr, "geometry buffer %.1fs\n", now() - t0);
            if (gbufpath && (gf = fopen(gbufpath, "wb"))) {
                fwrite(&W, 4, 1, gf); fwrite(&H, 4, 1, gf); fwrite(gb, sizeof(GPix), (size_t)W * H, gf); fclose(gf);
            }
        }
        double t1 = now();
        watercolor(img, gb, ldr, W, H);
        fprintf(stderr, "watercolour %.1fs\n", now() - t1);
        free(gb);
    } else develop(img, ldr, W, H);
    if (!write_png(out, ldr, W, H)) { fprintf(stderr, "cannot write %s\n", out); return 1; }
    fprintf(stderr, "wrote %s (%dx%d)\n", out, W, H);
    free(ldr); free(img);
    return 0;
}
