// cascade.c - stage-1 gates (cascade.h). Float32 on the gate path (single-precision FPU), double only in the
// one-off calibration. Host reference: ml/cascade_export.py.
#include "cascade.h"

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

float casc_mahal(const float *g9, const float *mu, const float *ci) {
  float d[CASC_NG], s = 0.0f;
  for (int i = 0; i < CASC_NG; ++i) d[i] = g9[i] - mu[i];
  for (int i = 0; i < CASC_NG; ++i) {
    float r = 0.0f;
    for (int j = 0; j < CASC_NG; ++j) r += ci[i * CASC_NG + j] * d[j];
    s += d[i] * r;
  }
  return s;
}

void casc_eval(const casc_params_t *p, const casc_cal_t *cal, const int16_t *w, casc_eval_t *e) {
  fx_r1(w, e->fx);
  casc_gate_features(e->fx, e->g);
  e->sup = casc_tree(p, e->fx, &e->leaf);
  e->d = casc_mahal(e->g, p->mu, p->ci);
  e->healthy = e->d > p->thr;
  if (cal && cal->valid) {
    e->dsc = casc_mahal(e->g, cal->mu, cal->ci);
    e->sc = e->dsc > cal->thr;
  } else {
    e->dsc = 0.0f;
    e->sc = -1;
  }
}

int casc_decide(const casc_params_t *p, const casc_cal_t *cal, int g, const int16_t *w, casc_state_t *st) {
  float fx[CASC_NFX], gf[CASC_NG];
  fx_r1(w, fx);
  if (g == CASC_SUP) return casc_tree(p, fx, 0);
  casc_gate_features(fx, gf);
  if (g == CASC_SELFCAL) {
    if (!cal || !cal->valid) return -1;
    return casc_mahal(gf, cal->mu, cal->ci) > cal->thr;
  }
  const int h = casc_mahal(gf, p->mu, p->ci) > p->thr;
  if (g == CASC_HEALTHY) return h;
  const int either = h | casc_tree(p, fx, 0);
  if (g == CASC_EITHER) return either;
  const int out = either & st->prev_either;  // CASC_EITHER2
  st->prev_either = either;
  return out;
}

static void sort_f(float *a, int n) {  // insertion sort, n <= CASC_CAL_MAX
  for (int i = 1; i < n; ++i) {
    float x = a[i];
    int j = i - 1;
    while (j >= 0 && a[j] > x) { a[j + 1] = a[j]; --j; }
    a[j + 1] = x;
  }
}

int casc_calibrate(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal) {
  static float G[CASC_CAL_MAX][CASC_NG];
  double mean[CASC_NG] = {0}, m2[CASC_NG][CASC_NG] = {{0}}, L[CASC_NG][CASC_NG] = {{0}}, inv[CASC_NG][CASC_NG];
  float fx[CASC_NFX], dist[CASC_CAL_MAX];
  cal->valid = 0;
  if (n < 2 || n > CASC_CAL_MAX) return -1;
  for (int k = 0; k < n; ++k) {  // Welford
    fx_r1(w[k], fx);
    casc_gate_features(fx, G[k]);
    double delta[CASC_NG];
    for (int i = 0; i < CASC_NG; ++i) {
      delta[i] = (double)G[k][i] - mean[i];
      mean[i] += delta[i] / (double)(k + 1);
    }
    for (int i = 0; i < CASC_NG; ++i)
      for (int j = 0; j <= i; ++j) m2[i][j] += delta[i] * ((double)G[k][j] - mean[j]);
  }
  // Cholesky of cov + ridge I = L L^T (lower triangle of m2 holds the sums)
  for (int i = 0; i < CASC_NG; ++i) {
    for (int j = 0; j <= i; ++j) {
      double s = m2[i][j] / (double)(n - 1) + (i == j ? (double)p->ridge : 0.0);
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
  for (int c = 0; c < CASC_NG; ++c) {
    double y[CASC_NG], x[CASC_NG];
    for (int i = 0; i < CASC_NG; ++i) {
      double s = (i == c) ? 1.0 : 0.0;
      for (int k = 0; k < i; ++k) s -= L[i][k] * y[k];
      y[i] = s / L[i][i];
    }
    for (int i = CASC_NG - 1; i >= 0; --i) {
      double s = y[i];
      for (int k = i + 1; k < CASC_NG; ++k) s -= L[k][i] * x[k];
      x[i] = s / L[i][i];
    }
    for (int i = 0; i < CASC_NG; ++i) inv[i][c] = x[i];
  }
  for (int i = 0; i < CASC_NG; ++i) {
    cal->mu[i] = (float)mean[i];
    for (int j = 0; j < CASC_NG; ++j) cal->ci[i * CASC_NG + j] = (float)(0.5 * (inv[i][j] + inv[j][i]));
  }
  for (int k = 0; k < n; ++k) dist[k] = casc_mahal(G[k], cal->mu, cal->ci);
  sort_f(dist, n);
  const float med = (n & 1) ? dist[n / 2] : 0.5f * (dist[n / 2 - 1] + dist[n / 2]);
  cal->thr = p->rho * med;
  cal->n = n;
  cal->valid = 1;
  return 0;
}
