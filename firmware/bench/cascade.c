// cascade.c - stage-1 gates (cascade.h). Float32 on the gate path (single-precision FPU), double only in the
// one-off calibration. Host reference: ml/cascade_export.py.
#include "cascade.h"

// Like frontend_fx.c: on the board only in bundle builds (the stub sketch has no generated headers).
#if !defined(ARDUINO) || defined(BUNDLE_ID)

#include <math.h>

#include "frontend_fx.h"

void casc_gate_features(const float *fx10, float *g9) {
  for (int i = 0; i < 4; ++i) g9[i] = logf(fx10[1 + i]);
  for (int i = 4; i < CASC_NG; ++i) g9[i] = fx10[1 + i];
}

int casc_tree(const casc_params_t *p, const float *fx10, int *leaf) {
  int n = 0;
  while (p->left[n] >= 0) n = (fx10[p->feature[n]] <= p->threshold[n]) ? p->left[n] : p->right[n];
  if (leaf) *leaf = n;
  return p->alarm[n] ? 1 : 0;
}

float casc_mahal_n(const float *g, const float *mu, const float *ci, int nd) {
  float d[CASC_NG_MAX > CASC_NG ? CASC_NG_MAX : CASC_NG], s = 0.0f;
  for (int i = 0; i < nd; ++i) d[i] = g[i] - mu[i];
  for (int i = 0; i < nd; ++i) {
    float r = 0.0f;
    for (int j = 0; j < nd; ++j) r += ci[i * nd + j] * d[j];
    s += d[i] * r;
  }
  return s;
}

float casc_mahal(const float *g9, const float *mu, const float *ci) { return casc_mahal_n(g9, mu, ci, CASC_NG); }

void casc_eval(const casc_params_t *p, const casc_cal_t *cal, const int16_t *w, casc_eval_t *e) {
  fx_r1(w, e->fx);
  casc_gate_features(e->fx, e->g);
  e->sup = casc_tree(p, e->fx, &e->leaf);
  e->d = casc_mahal(e->g, p->mu, p->ci);
  e->healthy = e->d > p->thr;
  if (cal && cal->valid) {
    e->dsc = casc_mahal_n(e->g, cal->mu, cal->ci, cal->d);
    e->sc = e->dsc > cal->thr;
  } else {
    e->dsc = 0.0f;
    e->sc = -1;
  }
}

int casc_decide_fx(const casc_params_t *p, const casc_cal_t *cal, int g, const float *fx, casc_state_t *st) {
  float gf[CASC_NG];
  if (g == CASC_SUP) return casc_tree(p, fx, 0);
  casc_gate_features(fx, gf);
  if (g == CASC_SELFCAL) {
    if (!cal || !cal->valid) return -1;
    return casc_mahal_n(gf, cal->mu, cal->ci, cal->d) > cal->thr;
  }
  const int h = casc_mahal(gf, p->mu, p->ci) > p->thr;
  if (g == CASC_HEALTHY) return h;
  const int either = h | casc_tree(p, fx, 0);
  if (g == CASC_EITHER) return either;
  const int out = either & st->prev_either;  // CASC_EITHER2
  st->prev_either = either;
  return out;
}

int casc_decide(const casc_params_t *p, const casc_cal_t *cal, int g, const int16_t *w, casc_state_t *st) {
  float fx[CASC_NFX];
  fx_r1(w, fx);
  return casc_decide_fx(p, cal, g, fx, st);
}

static void sort_f(float *a, int n) {  // insertion sort, n <= CASC_CAL_MAX
  for (int i = 1; i < n; ++i) {
    float x = a[i];
    int j = i - 1;
    while (j >= 0 && a[j] > x) { a[j + 1] = a[j]; --j; }
    a[j + 1] = x;
  }
}

static float s_G[CASC_CAL_MAX * CASC_NG_MAX];   // calibration features, row-major n x d

int casc_calibrate_feats(const float *G, int n, int d, float ridge, float rho, casc_cal_t *cal) {
  // static: 3 x 23 x 23 doubles do not belong on an Mbed thread stack
  static double mean[CASC_NG_MAX], m2[CASC_NG_MAX][CASC_NG_MAX], L[CASC_NG_MAX][CASC_NG_MAX],
      inv[CASC_NG_MAX][CASC_NG_MAX];
  static float dist[CASC_CAL_MAX];
  cal->valid = 0;
  if (n < 2 || n > CASC_CAL_MAX || d < 1 || d > CASC_NG_MAX) return -1;
  for (int i = 0; i < d; ++i) {
    mean[i] = 0.0;
    for (int j = 0; j < d; ++j) m2[i][j] = L[i][j] = 0.0;
  }
  for (int k = 0; k < n; ++k) {  // Welford
    const float *g = &G[k * d];
    double delta[CASC_NG_MAX];
    for (int i = 0; i < d; ++i) {
      delta[i] = (double)g[i] - mean[i];
      mean[i] += delta[i] / (double)(k + 1);
    }
    for (int i = 0; i < d; ++i)
      for (int j = 0; j <= i; ++j) m2[i][j] += delta[i] * ((double)g[j] - mean[j]);
  }
  // Cholesky of cov + ridge I = L L^T (lower triangle of m2 holds the sums)
  for (int i = 0; i < d; ++i) {
    for (int j = 0; j <= i; ++j) {
      double s = m2[i][j] / (double)(n - 1) + (i == j ? (double)ridge : 0.0);
      for (int k = 0; k < j; ++k) s -= L[i][k] * L[j][k];
      if (i == j) {
        if (s <= 0.0) return -2;
        L[i][i] = sqrt(s);
      } else {
        L[i][j] = s / L[j][j];
      }
    }
  }
  // inverse = L^-T L^-1, column by column: solve L y = e_c, then L^T x = y
  for (int c = 0; c < d; ++c) {
    double y[CASC_NG_MAX], x[CASC_NG_MAX];
    for (int i = 0; i < d; ++i) {
      double s = (i == c) ? 1.0 : 0.0;
      for (int k = 0; k < i; ++k) s -= L[i][k] * y[k];
      y[i] = s / L[i][i];
    }
    for (int i = d - 1; i >= 0; --i) {
      double s = y[i];
      for (int k = i + 1; k < d; ++k) s -= L[k][i] * x[k];
      x[i] = s / L[i][i];
    }
    for (int i = 0; i < d; ++i) inv[i][c] = x[i];
  }
  for (int i = 0; i < d; ++i) {
    cal->mu[i] = (float)mean[i];
    for (int j = 0; j < d; ++j) cal->ci[i * d + j] = (float)(0.5 * (inv[i][j] + inv[j][i]));
  }
  for (int k = 0; k < n; ++k) dist[k] = casc_mahal_n(&G[k * d], cal->mu, cal->ci, d);
  sort_f(dist, n);
  const float med = (n & 1) ? dist[n / 2] : 0.5f * (dist[n / 2 - 1] + dist[n / 2]);
  cal->thr = rho * med;
  cal->n = n;
  cal->d = d;
  cal->valid = 1;
  return 0;
}

int casc_calibrate(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal) {
  float fx[CASC_NFX];
  cal->valid = 0;
  if (n < 2 || n > CASC_CAL_MAX) return -1;
  for (int k = 0; k < n; ++k) {
    fx_r1(w[k], fx);
    casc_gate_features(fx, &s_G[k * CASC_NG]);
  }
  return casc_calibrate_feats(s_G, n, CASC_NG, p->ridge, p->rho, cal);
}

#if CASC_G6_ENABLED
void casc_g6_features(const int16_t *w, float *f24, float *g23) {
  fx_r2_hyb(w, f24);
  casc_gate_features(f24, g23);
  for (int i = 0; i < 14; ++i) g23[CASC_NG + i] = f24[10 + i];
}

int casc_calibrate_g6(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal) {
  float f24[24];
  cal->valid = 0;
  if (n < 2 || n > CASC_CAL_MAX) return -1;
  for (int k = 0; k < n; ++k) casc_g6_features(w[k], f24, &s_G[k * CASC_G6_FEATS]);
  return casc_calibrate_feats(s_G, n, CASC_G6_FEATS, p->ridge, p->rho6, cal);
}

int casc_g6_decide(const casc_cal_t *cal6, const float *g23) {
  if (!cal6 || !cal6->valid) return -1;
  return casc_mahal_n(g23, cal6->mu, cal6->ci, CASC_G6_FEATS) > cal6->thr;
}
#endif

#endif  // !ARDUINO || BUNDLE_ID
