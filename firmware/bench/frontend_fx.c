// frontend_fx.c - see frontend_fx.h. Formulas: handoff/features_spec.md (v1.2); scaling derivations inline.
#include "frontend_fx.h"

#if !defined(ARDUINO) || defined(BUNDLE_ID)

#include <math.h>
#include <string.h>

#include "arm_math.h"
#include "arm_const_structs.h"
#include "hann_fx.h"   // g_hann_q15_1024, g_hann_q31_1024, g_hann_q15_256, g_hann_q31_256 (ml/export_handoff.py)
#include "hann_1024.h" // g_hann_1024 (float), for the hybrid path

#define ENV_K0 171   // R4: bins 171..426 = 2.0-5.0 kHz
#define ENV_NB 256
#define ENV_NK 32

static arm_rfft_instance_q15 s_rq15_1024, s_rq15_256;
static arm_rfft_instance_q31 s_rq31_1024, s_rq31_256;
static q15_t s_a15[2 * FX_N], s_b15[2 * FX_N];   // q15 work buffers (rfft needs a 2N output)
static q31_t s_a31[2 * FX_N], s_b31[2 * FX_N];   // q31 work buffers
static arm_rfft_fast_instance_f32 s_rf_1024, s_rf_256;   // hybrid path
static float s_fa[FX_N], s_fb[FX_N];

static const float k_log10_2 = 0.30102999566f;

int fx_init(void) {
  if (arm_rfft_init_q15(&s_rq15_1024, FX_N, 0, 1) != ARM_MATH_SUCCESS) return -1;
  if (arm_rfft_init_q15(&s_rq15_256, ENV_NB, 0, 1) != ARM_MATH_SUCCESS) return -1;
  if (arm_rfft_init_q31(&s_rq31_1024, FX_N, 0, 1) != ARM_MATH_SUCCESS) return -1;
  if (arm_rfft_init_q31(&s_rq31_256, ENV_NB, 0, 1) != ARM_MATH_SUCCESS) return -1;
  if (arm_rfft_fast_init_f32(&s_rf_1024, FX_N) != ARM_MATH_SUCCESS) return -1;
  if (arm_rfft_fast_init_f32(&s_rf_256, ENV_NB) != ARM_MATH_SUCCESS) return -1;
  return 0;
}

uint32_t fx_static_ram_bytes(int path) {
  if (path == 1) return (uint32_t)(sizeof s_rq31_1024 + sizeof s_rq31_256 + sizeof s_a31 + sizeof s_b31);
  if (path == 2) return (uint32_t)(sizeof s_rf_1024 + sizeof s_rf_256 + sizeof s_fa + sizeof s_fb);
  return (uint32_t)(sizeof s_rq15_1024 + sizeof s_rq15_256 + sizeof s_a15 + sizeof s_b15);
}

void fx_to_float(const int16_t *q, float *x) {
  const float inv = 1.0f / FX_LSB_PER_G;
  for (int i = 0; i < FX_N; ++i) x[i] = (float)q[i] * inv;
}

void fx_from_float(const float *x, int16_t *q) {
  for (int i = 0; i < FX_N; ++i) {
    float v = x[i] * FX_LSB_PER_G;
    v = v >= 0.0f ? v + 0.5f : v - 0.5f;
    q[i] = (int16_t)(v > 32767.0f ? 32767 : v < -32768.0f ? -32768 : (int32_t)v);
  }
}

// ---- block floating point: left shift that brings max |x| into [2^14, 2^15) (q15) or [2^30, 2^31) (q31) --------
static int headroom_q15(const q15_t *x, uint32_t n) {
  q15_t m;
  uint32_t idx;
  arm_absmax_q15(x, n, &m, &idx);
  return m > 0 ? (int)__CLZ((uint32_t)m) - 17 : 0;
}

static int headroom_q31(const q31_t *x, uint32_t n) {
  q31_t m;
  uint32_t idx;
  arm_absmax_q31(x, n, &m, &idx);
  return m > 0 ? (int)__CLZ((uint32_t)m) - 1 : 0;
}

// ---- R1: exact integer moments ------------------------------------------------------------------------------------
// Pass 1: sum, sum |x|, max, min (on cores with the DSP extension: two samples per instruction, SMLAD/SSUB16/SEL).
// Pass 2: central moments of d = x - c around the integer mean c, in 64-bit accumulators (SMLAL/UMLAL; d^4 as
// round(d^2 / 2^8)^2, 48 significant bits). The residual |mean - c| <= 0.5 LSB is folded back in float, so a large
// DC offset (e.g. gravity on a vertically mounted sensor) costs no precision. c is bounded to +/-13573 LSB (3.3 g)
// so that |d| <= 46340 and d^2 fits in int32 without a per-sample clamp.
void fx_r1(const int16_t *q, float *f) {
  int32_t sum, sabs, mx, mn;
#if defined(ARM_MATH_DSP)
  {
    const uint32_t ones = 0x00010001u;
    uint32_t mx2 = __PKHBT((uint32_t)(uint16_t)q[0], (uint32_t)(uint16_t)q[0], 16), mn2 = mx2;
    int32_t s = 0, sa = 0;
    const int16_t *p = q;
    for (int i = 0; i < FX_N / 2; ++i, p += 2) {
      const uint32_t v = (uint32_t)read_q15x2((q15_t *)p);
      s = (int32_t)__SMLAD(v, ones, (uint32_t)s);
      (void)__SSUB16(v, mx2);
      mx2 = __SEL(v, mx2);                   // per half: v >= max ? v : max
      (void)__SSUB16(v, mn2);
      mn2 = __SEL(mn2, v);                   // per half: v >= min ? min : v
      const uint32_t neg = __QSUB16(0u, v);  // saturating -v (|-32768| -> 32767)
      (void)__SSUB16(v, 0u);
      sa = (int32_t)__SMLAD(__SEL(v, neg), ones, (uint32_t)sa);
    }
    sum = s;
    sabs = sa;
    const int32_t mxl = (int16_t)mx2, mxh = (int16_t)(mx2 >> 16), mnl = (int16_t)mn2, mnh = (int16_t)(mn2 >> 16);
    mx = mxl > mxh ? mxl : mxh;
    mn = mnl < mnh ? mnl : mnh;
  }
#else
  sum = 0;
  sabs = 0;
  mx = q[0];
  mn = q[0];
  for (int i = 0; i < FX_N; ++i) {
    const int32_t v = q[i];
    sum += v;
    sabs += v < 0 ? -v : v;
    if (v > mx) mx = v;
    if (v < mn) mn = v;
  }
#endif
  int32_t c = (sum >= 0 ? sum + FX_N / 2 : sum - FX_N / 2) / FX_N;
  c = c > 13573 ? 13573 : c < -13573 ? -13573 : c;
  int64_t a2, a3;
  uint64_t a4;
#if defined(__ARM_ARCH_7EM__) || defined(__ARM_ARCH_7M__)
  {  // 64-bit multiply-accumulates kept in register pairs (SMLAL/UMLAL); -Os otherwise spills them every sample
    uint32_t l2 = 0, h2 = 0, l3 = 0, h3 = 0, l4 = 0, h4 = 0;
    for (int i = 0; i < FX_N; ++i) {
      const int32_t d = (int32_t)q[i] - c;
      const int32_t d2 = d * d;
      const uint32_t h = ((uint32_t)d2 + 128u) >> 8;
      __asm("smlal %0, %1, %2, %3" : "+r"(l2), "+r"(h2) : "r"(d), "r"(d));
      __asm("smlal %0, %1, %2, %3" : "+r"(l3), "+r"(h3) : "r"(d2), "r"(d));
      __asm("umlal %0, %1, %2, %3" : "+r"(l4), "+r"(h4) : "r"(h), "r"(h));
    }
    a2 = (int64_t)(((uint64_t)h2 << 32) | l2);
    a3 = (int64_t)(((uint64_t)h3 << 32) | l3);
    a4 = ((uint64_t)h4 << 32) | l4;
  }
#else
  a2 = 0;
  a3 = 0;
  a4 = 0;
  for (int i = 0; i < FX_N; ++i) {
    const int32_t d = (int32_t)q[i] - c;
    const int32_t d2 = d * d;
    const uint32_t h = ((uint32_t)d2 + 128u) >> 8;
    a2 += (uint32_t)d2;                      // d^2 >= 0
    a3 += (int64_t)d2 * d;
    a4 += (uint64_t)h * h;
  }
#endif
  const float n = (float)FX_N, inv_s = 1.0f / FX_LSB_PER_G;
  const float delta = (float)(sum - FX_N * c) / n;   // mean - c (exact)
  const float e2 = (float)a2 / n, e3 = (float)a3 / n, e4 = (float)a4 * (65536.0f / n);
  const float m2 = e2 - delta * delta;               // central moments about the true mean (in LSB)
  const float m3 = e3 - 3.0f * delta * e2 + 2.0f * delta * delta * delta;
  const float m4 = e4 - 4.0f * delta * e3 + 6.0f * delta * delta * e2 - 3.0f * delta * delta * delta * delta;
  const float mu = ((float)c + delta) * inv_s;
  const float s = sqrtf(m2) * inv_s;
  const float rms = sqrtf(m2 * inv_s * inv_s + mu * mu);
  const float peak = (float)(mx > -mn ? mx : -mn) * inv_s;
  const float mabs = (float)sabs / n * inv_s;
  f[0] = mu;
  f[1] = rms;
  f[2] = s;
  f[3] = peak;
  f[4] = (float)(mx - mn) * inv_s;
  f[5] = peak / rms;
  f[6] = m4 / (m2 * m2);
  f[7] = m3 / (m2 * sqrtf(m2));
  f[8] = rms / mabs;
  f[9] = peak / mabs;
}

// ---- R2 ---------------------------------------------------------------------------------------------------------
// q15: xn = x << sh, xw = xn * hann (q15), arm_rfft_q15 returns X' = DFT(xw) / 1024 (format 11.5). In g units
// DFT(x * hann) = X' * 1024 / (2^sh * S), so sum |X|^2 over a band = acc * 2^(20 - 2 sh) / S^2.
void fx_r2_q15(const int16_t *q, float *f) {
  fx_r1(q, f);
  const int sh = headroom_q15(q, FX_N);
  arm_shift_q15(q, (int8_t)sh, s_a15, FX_N);
  arm_mult_q15(s_a15, g_hann_q15_1024, s_a15, FX_N);
  arm_rfft_q15(&s_rq15_1024, s_a15, s_b15);   // X'[k] = (s_b15[2k], s_b15[2k+1]), k = 0..512
  const float c = (float)(20 - 2 * sh) * k_log10_2 - 2.0f * log10f(FX_LSB_PER_G);
  for (int b = 0; b < 14; ++b) {
    int64_t acc = 0;
    const q15_t *p = &s_b15[2 * 32 * b];
    for (int k = 0; k < 32; ++k, p += 2) {
      const q31_t re_im = read_q15x2((q15_t *)p);
      acc = __SMLALD(re_im, re_im, acc);       // re^2 + im^2
    }
    f[10 + b] = acc > 0 ? log10f((float)acc) + c : -12.0f;
  }
}

// q31: xn = (x << 16) << sh, xw = xn * hann (q31), arm_rfft_q31 returns X' = DFT(xw) / 1024 (format 11.21).
// acc = sum (X'^2 >> 16), so sum |X|^2 in g units = acc * 2^16 * 2^20 / (2^(32 + 2 sh) * S^2) = acc * 2^(4 - 2 sh) / S^2.
void fx_r2_q31(const int16_t *q, float *f) {
  fx_r1(q, f);
  for (int i = 0; i < FX_N; ++i) s_a31[i] = (q31_t)q[i] << 16;
  const int sh = headroom_q31(s_a31, FX_N);
  arm_shift_q31(s_a31, (int8_t)sh, s_a31, FX_N);
  arm_mult_q31(s_a31, g_hann_q31_1024, s_a31, FX_N);
  arm_rfft_q31(&s_rq31_1024, s_a31, s_b31);
  const float c = (float)(4 - 2 * sh) * k_log10_2 - 2.0f * log10f(FX_LSB_PER_G);
  for (int b = 0; b < 14; ++b) {
    int64_t acc = 0;
    const q31_t *p = &s_b31[2 * 32 * b];
    for (int k = 0; k < 32; ++k, p += 2) {
      acc += ((int64_t)p[0] * p[0]) >> 16;
      acc += ((int64_t)p[1] * p[1]) >> 16;
    }
    f[10 + b] = acc > 0 ? log10f((float)acc) + c : -12.0f;
  }
}

// ---- R4 ---------------------------------------------------------------------------------------------------------
// The envelope spectrum is divided by its own mean (spec step 6), so every scale factor cancels: the block shifts
// between stages only manage dynamic range and need no bookkeeping.
static void r4_tail(const float *E, float *f) {   // E = |envelope spectrum| bins 1..32, any common scale
  float sum = 0.0f;
  for (int k = 0; k < ENV_NK; ++k) sum += E[k];
  const float inv_mean = sum > 0.0f ? (float)ENV_NK / sum : 0.0f;
  for (int k = 0; k < ENV_NK; ++k) f[9 + k] = log10f(E[k] * inv_mean + 1e-6f);
}

void fx_r4_q15(const int16_t *q, float *f) {
  float r1[10];
  fx_r1(q, r1);
  for (int i = 0; i < 9; ++i) f[i] = r1[i + 1];
  int sh = headroom_q15(q, FX_N);
  arm_shift_q15(q, (int8_t)sh, s_a15, FX_N);
  arm_rfft_q15(&s_rq15_1024, s_a15, s_b15);                       // un-windowed 1024-point rFFT
  memcpy(s_a15, &s_b15[2 * ENV_K0], 2u * ENV_NB * sizeof(q15_t));  // 2.0-5.0 kHz band -> 256 complex samples
  sh = headroom_q15(s_a15, 2 * ENV_NB);
  arm_shift_q15(s_a15, (int8_t)sh, s_a15, 2 * ENV_NB);
  arm_cfft_q15(&arm_cfft_sR_q15_len256, s_a15, 1, 1);              // inverse cFFT = analytic band signal
  q15_t *env = s_b15;
  arm_cmplx_mag_q15(s_a15, env, ENV_NB);                           // envelope (2.14)
  sh = headroom_q15(env, ENV_NB);
  arm_shift_q15(env, (int8_t)sh, env, ENV_NB);
  int32_t m = 0;
  for (int i = 0; i < ENV_NB; ++i) m += env[i];
  const q15_t mean = (q15_t)(m / ENV_NB);
  q15_t *u = &s_b15[ENV_NB];
  arm_offset_q15(env, (q15_t)-mean, u, ENV_NB);                    // remove the mean (|u| < 2^15)
  arm_mult_q15(u, g_hann_q15_256, u, ENV_NB);
  sh = headroom_q15(u, ENV_NB);
  arm_shift_q15(u, (int8_t)sh, s_a15, ENV_NB);
  arm_rfft_q15(&s_rq15_256, s_a15, s_b15);                        // envelope spectrum, U[k] at 2k
  q15_t E15[ENV_NK];
  arm_cmplx_mag_q15(&s_b15[2], E15, ENV_NK);                        // bins 1..32
  float E[ENV_NK];
  for (int k = 0; k < ENV_NK; ++k) E[k] = (float)E15[k];
  r4_tail(E, f);
}

void fx_r4_q31(const int16_t *q, float *f) {
  float r1[10];
  fx_r1(q, r1);
  for (int i = 0; i < 9; ++i) f[i] = r1[i + 1];
  for (int i = 0; i < FX_N; ++i) s_a31[i] = (q31_t)q[i] << 16;
  int sh = headroom_q31(s_a31, FX_N);
  arm_shift_q31(s_a31, (int8_t)sh, s_a31, FX_N);
  arm_rfft_q31(&s_rq31_1024, s_a31, s_b31);
  memcpy(s_a31, &s_b31[2 * ENV_K0], 2u * ENV_NB * sizeof(q31_t));
  sh = headroom_q31(s_a31, 2 * ENV_NB);
  arm_shift_q31(s_a31, (int8_t)sh, s_a31, 2 * ENV_NB);
  arm_cfft_q31(&arm_cfft_sR_q31_len256, s_a31, 1, 1);
  q31_t *env = s_b31;
  arm_cmplx_mag_q31(s_a31, env, ENV_NB);                           // 2.30
  sh = headroom_q31(env, ENV_NB);
  arm_shift_q31(env, (int8_t)sh, env, ENV_NB);
  int64_t m = 0;
  for (int i = 0; i < ENV_NB; ++i) m += env[i];
  const q31_t mean = (q31_t)(m / ENV_NB);
  q31_t *u = &s_b31[ENV_NB];
  arm_offset_q31(env, -mean, u, ENV_NB);
  arm_mult_q31(u, g_hann_q31_256, u, ENV_NB);
  sh = headroom_q31(u, ENV_NB);
  arm_shift_q31(u, (int8_t)sh, s_a31, ENV_NB);
  arm_rfft_q31(&s_rq31_256, s_a31, s_b31);
  q31_t E31[ENV_NK];
  arm_cmplx_mag_q31(&s_b31[2], E31, ENV_NK);
  float E[ENV_NK];
  for (int k = 0; k < ENV_NK; ++k) E[k] = (float)E31[k];
  r4_tail(E, f);
}

// ---- hybrid: integer R1, float FFT stages ------------------------------------------------------------------------
// Same arithmetic as frontend.c after the int16 -> float conversion, which is fused into the first pass over the
// samples (with the Hann window for R2).
void fx_r2_hyb(const int16_t *q, float *f) {
  fx_r1(q, f);
  const float inv = 1.0f / FX_LSB_PER_G;
  for (int i = 0; i < FX_N; ++i) s_fa[i] = (float)q[i] * inv * g_hann_1024[i];
  arm_rfft_fast_f32(&s_rf_1024, s_fa, s_fb, 0);
  for (int b = 0; b < 14; ++b) {
    float acc = 0.0f;
    for (int k = 32 * b; k < 32 * b + 32; ++k) {
      if (k == 0) {
        acc += s_fb[0] * s_fb[0];
      } else {
        const float re = s_fb[2 * k], im = s_fb[2 * k + 1];
        acc += re * re + im * im;
      }
    }
    f[10 + b] = log10f(acc + 1e-12f);
  }
}

void fx_r4_hyb(const int16_t *q, float *f) {
  float r1[10];
  fx_r1(q, r1);
  for (int i = 0; i < 9; ++i) f[i] = r1[i + 1];
  const float inv = 1.0f / FX_LSB_PER_G;
  for (int i = 0; i < FX_N; ++i) s_fa[i] = (float)q[i] * inv;
  arm_rfft_fast_f32(&s_rf_1024, s_fa, s_fb, 0);
  memcpy(s_fa, &s_fb[2 * ENV_K0], 2u * ENV_NB * sizeof(float));
  arm_cfft_f32(&arm_cfft_sR_f32_len256, s_fa, 1, 1);
  float *env = &s_fb[0];
  arm_cmplx_mag_f32(s_fa, env, ENV_NB);
  float mean = 0.0f;
  for (int m = 0; m < ENV_NB; ++m) mean += env[m];
  mean /= (float)ENV_NB;
  float *u = &s_fb[ENV_NB];
  for (int m = 0; m < ENV_NB; ++m) u[m] = (env[m] - mean) * ((float)g_hann_q31_256[m] * (1.0f / 2147483648.0f));
  float *U = &s_fb[2 * ENV_NB];
  arm_rfft_fast_f32(&s_rf_256, u, U, 0);
  float E[ENV_NK];
  arm_cmplx_mag_f32(&U[2], E, ENV_NK);
  r4_tail(E, f);
}

#endif  // !ARDUINO || BUNDLE_ID
