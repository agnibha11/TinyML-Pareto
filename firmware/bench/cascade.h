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
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CASC_NFX 10     // fx_r1 outputs
#define CASC_NG 9       // gate features: log(rms, std, peak, p2p), crest, kurtosis, skewness, shape, impulse
#define CASC_CAL_MAX 64 // largest calibration set

enum { CASC_SUP = 1, CASC_HEALTHY = 2, CASC_EITHER = 3, CASC_EITHER2 = 4, CASC_SELFCAL = 5, CASC_N_GATES = 5 };

typedef struct {
  int n_nodes;
  const int8_t *feature, *left, *right;
  const float *threshold;
  const uint8_t *alarm;
  const float *mu, *ci;  // factory healthy mean [9], inverse covariance [81] (row-major)
  float thr;             // factory Mahalanobis threshold
  float rho;             // selfcal: threshold = rho * median(calibration distances)
  float ridge;           // added to the calibration covariance diagonal
} casc_params_t;

typedef struct {
  int valid;             // 0 until casc_calibrate() succeeded
  int n;
  float mu[CASC_NG], ci[CASC_NG * CASC_NG], thr;
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
// All base gates on one window; cal may be 0 or invalid.
void casc_eval(const casc_params_t *p, const casc_cal_t *cal, const int16_t *w, casc_eval_t *e);
// Decision of gate g on one window, updating the stream state (G4). Returns -1 for G5 without calibration.
int casc_decide(const casc_params_t *p, const casc_cal_t *cal, int g, const int16_t *w, casc_state_t *st);
// Self-calibration on n (2..CASC_CAL_MAX) windows: double-precision mean and covariance, + ridge, Cholesky inverse,
// threshold = rho * median of the n distances (mean of the two middle values for even n, as numpy.median).
// Returns 0 on success, -1 on a bad n, -2 if the covariance is not positive definite.
int casc_calibrate(const casc_params_t *p, const int16_t (*w)[1024], int n, casc_cal_t *cal);

#ifdef __cplusplus
}
#endif
