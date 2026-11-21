// cascade.h - stage-1 gates of the detect-then-diagnose cascade (docs/cascade_preregistration.md §4.2).
//
// Portable C99, no allocation, so the same file builds for the board, the virtual MCU and the PC (host simulation).
// Parameters come from handoff/cascade/<dataset>/gate_params.h (ml/cascade_export.py) through casc_params_t.
// Input: fx_r1 features (frontend_fx.h) of an int16 window.
//
//   G1 sup      depth-2 tree on the 10 fx_r1 features
//   G2 healthy  Mahalanobis distance of the 9 gate features to the factory healthy mean, alarm when d > thr
//   G3 either   G1 | G2
//   G4 either2  G3 on this window and on the previous one (state in casc_state_t, reset per stream)
//   G5 selfcal  G2 with mean, inverse covariance and threshold from casc_calibrate() on n healthy windows
//   G6 selfcal_r2  Paderborn (pre-registration Deviation 4): G5 on 23 features, g9 + the 14 log band powers of
//               fx_r2_hyb, from casc_calibrate_g6(). Built only with CASC_NG_MAX >= 23 (the Paderborn cascade bundle).
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CASC_NFX 10     // fx_r1 outputs
#define CASC_NG 9       // gate features: log(rms, std, peak, p2p), crest, kurtosis, skewness, shape, impulse
#ifndef CASC_NG_MAX
#define CASC_NG_MAX 9   // largest calibrated feature count: 9 (G5), 23 with G6 (-DCASC_NG_MAX=23)
#endif
#ifndef CASC_CAL_MAX
#define CASC_CAL_MAX 64 // largest calibration set (-DCASC_CAL_MAX=128 for G6)
#endif
#define CASC_G6_FEATS 23
#define CASC_G6_ENABLED (CASC_NG_MAX >= CASC_G6_FEATS)

enum { CASC_SUP = 1, CASC_HEALTHY = 2, CASC_EITHER = 3, CASC_EITHER2 = 4, CASC_SELFCAL = 5, CASC_SELFCAL_R2 = 6 };

typedef struct {
  int n_nodes;
  const int8_t *feature, *left, *right;
  const float *threshold;
  const uint8_t *alarm;
  const float *mu, *ci;  // factory healthy mean [9], inverse covariance [81] (row-major)
  float thr;             // factory Mahalanobis threshold
  float rho;             // selfcal: threshold = rho * median(calibration distances)
  float ridge;           // added to the calibration covariance diagonal
  float rho6;            // G6: the same ratio for the 23-feature gate (0 without G6)
} casc_params_t;

typedef struct {
  int valid;             // 0 until casc_calibrate() succeeded
  int n, d;              // windows, features (ci is row-major d x d)
  float mu[CASC_NG_MAX], ci[CASC_NG_MAX * CASC_NG_MAX], thr;
} casc_cal_t;

typedef struct {
  int prev_either;       // G4: alarm of G3 on the previous window
} casc_state_t;

// Everything a gate computes for one window (GVERIFY reports all of it).
typedef struct {
  float fx[CASC_NFX], g[CASC_NG];
  int leaf, sup, healthy, sc;  // sc = -1 when not calibrated
  float d, dsc;
} casc_eval_t;

void casc_gate_features(const float *fx10, float *g9);
int casc_tree(const casc_params_t *p, const float *fx10, int *leaf);  // alarm 0/1
float casc_mahal(const float *g9, const float *mu, const float *ci);
float casc_mahal_n(const float *g, const float *mu, const float *ci, int d);  // ci row-major d x d
// All base gates on one window; cal may be 0 or invalid.
void casc_eval(const casc_params_t *p, const casc_cal_t *cal, const int16_t *w, casc_eval_t *e);
// Decision of gate g on one window, updating the stream state (G4). Returns -1 for G5 without calibration.
int casc_decide(const casc_params_t *p, const casc_cal_t *cal, int g, const int16_t *w, casc_state_t *st);
// Same decision from fx_r1 features the caller already has (fx_r1 + casc_decide_fx == casc_decide). The cascade
// commands use this so that the gate's features can be handed to stage 2 (CASCADE m = 2).
int casc_decide_fx(const casc_params_t *p, const casc_cal_t *cal, int g, const float *fx10, casc_state_t *st);
// Self-calibration on n (2..CASC_CAL_MAX) windows: double-precision mean and covariance, + ridge, Cholesky inverse,
// threshold = rho * median of the n distances (mean of the two middle values for even n, as numpy.median).
// Returns 0 on success, -1 on a bad n, -2 if the covariance is not positive definite.
int casc_calibrate(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal);
// The same on n x d precomputed features G (row-major), threshold = rho * median.
int casc_calibrate_feats(const float *G, int n, int d, float ridge, float rho, casc_cal_t *cal);

#if CASC_G6_ENABLED
// G6 features of one window: f24 = fx_r2_hyb output, g23 = {g9 of f24[0..9], f24[10..23]}. Needs fx_init_hyb().
void casc_g6_features(const int16_t *w, float *f24, float *g23);
int casc_calibrate_g6(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal);
int casc_g6_decide(const casc_cal_t *cal6, const float *g23);   // -1 when not calibrated
#endif

#ifdef __cplusplus
}
#endif
