// fxbench.c - runs inside QEMU with the vmcount plugin: instruction counts and estimated Cortex-M4 cycles of the
// float and fixed-point front ends on the same windows (tools/vmcu/README.md explains the cycle model).
//
// Every measured region includes the z-score, as the FRONT phase of CONTRACT.md 7.5 does. The float front end is
// measured from float samples; its int16 -> float conversion (what it costs to feed it from a sensor) is measured
// separately as CONV. One unmeasured warm-up call precedes each measured call. Output, per window j:
//   L <label> <j>      then the plugin's VMC line for that region
// Built and run by tools/fxfe/run_fxfe.py.
#include <math.h>
#include <stdio.h>

#include "frontend.h"
#include "frontend_fx.h"
#include "fx_inputs.h"     // FX_N_WIN windows as int16 (generated)
#include "norm_constants.h"

extern void vm_mark_begin(void);
extern void vm_mark_end(void);
extern void initialise_monitor_handles(void);

static float s_x[FX_N];
static float s_f[64];

typedef void (*fe_fn)(int j);

static void conv(int j) { fx_to_float(g_fx_win[j], s_x); }
static void f32_r1(int j) { (void)j; fe_r1(s_x, s_f); fe_zscore(s_f, g_r1_mean, g_r1_std, 10); }
static void f32_r2(int j) { (void)j; fe_r2(s_x, s_f); fe_zscore(s_f, g_r2_mean, g_r2_std, 24); }
static void f32_r4(int j) { (void)j; fe_r4(s_x, s_f); fe_zscore(s_f, g_r4_mean, g_r4_std, 41); }
static void fx1(int j) { fx_r1(g_fx_win[j], s_f); fe_zscore(s_f, g_r1_mean, g_r1_std, 10); }
static void fx2_15(int j) { fx_r2_q15(g_fx_win[j], s_f); fe_zscore(s_f, g_r2_mean, g_r2_std, 24); }
static void fx2_31(int j) { fx_r2_q31(g_fx_win[j], s_f); fe_zscore(s_f, g_r2_mean, g_r2_std, 24); }
static void fx4_15(int j) { fx_r4_q15(g_fx_win[j], s_f); fe_zscore(s_f, g_r4_mean, g_r4_std, 41); }
static void fx4_31(int j) { fx_r4_q31(g_fx_win[j], s_f); fe_zscore(s_f, g_r4_mean, g_r4_std, 41); }
// Healthy-only gate of the cascade (ml/cascade.py): Mahalanobis distance of 9 R1 features (log of rms, std, peak,
// p2p; crest, kurtosis, skewness, shape, impulse) to the healthy mean. Constants are placeholders of the right size;
// the cost does not depend on their values.
static float g_mu[9] = {0.1f, 0.1f, 0.1f, 0.1f, 3.f, 3.f, 0.f, 1.2f, 4.f};
static float g_ci[81];
static volatile int s_alarm;
static float s_r1[10];
static int s_r1_j = -1;
static void gate(int j) {
  if (s_r1_j != j) {        // first (warm-up) call computes R1; the measured call then costs the gate alone,
    fx_r1(g_fx_win[j], s_r1);   // since stage 1 has already computed R1
    s_r1_j = j;
  }
  float g[9];
  for (int i = 0; i < 4; ++i) g[i] = logf(s_r1[1 + i]) - g_mu[i];
  for (int i = 4; i < 9; ++i) g[i] = s_r1[1 + i] - g_mu[i];
  float d2 = 0.0f;
  for (int a = 0; a < 9; ++a) {
    float t = 0.0f;
    for (int b = 0; b < 9; ++b) t += g_ci[9 * a + b] * g[b];
    d2 += g[a] * t;
  }
  s_alarm = d2 > 12.0f;
}
static void hy2(int j) { fx_r2_hyb(g_fx_win[j], s_f); fe_zscore(s_f, g_r2_mean, g_r2_std, 24); }
static void hy4(int j) { fx_r4_hyb(g_fx_win[j], s_f); fe_zscore(s_f, g_r4_mean, g_r4_std, 41); }

static const struct { const char *name; fe_fn fn; } k_regions[] = {
    {"CONV", conv},     {"F32_R1", f32_r1}, {"F32_R2", f32_r2},   {"F32_R4", f32_r4},   {"FX_R1", fx1},
    {"FX_R2_Q15", fx2_15}, {"FX_R2_Q31", fx2_31}, {"FX_R4_Q15", fx4_15}, {"FX_R4_Q31", fx4_31},
    {"HYB_R2", hy2},    {"HYB_R4", hy4},    {"GATE_MAHAL", gate},
};

int main(void) {
  initialise_monitor_handles();
  if (fe_init() != 0 || fx_init() != 0) {
    printf("ERR init\n");
    return 1;
  }
  printf("FXB start windows=%d ram_f32=%lu ram_q15=%lu ram_q31=%lu ram_hyb=%lu\n", FX_N_WIN,
         (unsigned long)fe_static_ram_bytes(), (unsigned long)fx_static_ram_bytes(0),
         (unsigned long)fx_static_ram_bytes(1), (unsigned long)fx_static_ram_bytes(2));
  for (int j = 0; j < FX_N_WIN; ++j) {
    for (unsigned r = 0; r < sizeof k_regions / sizeof k_regions[0]; ++r) {
      if (r >= 1 && r <= 3) conv(j);   // the float front end reads s_x
      k_regions[r].fn(j);              // warm-up
      if (r >= 1 && r <= 3) conv(j);
      printf("L %s %d\n", k_regions[r].name, j);
      vm_mark_begin();
      k_regions[r].fn(j);
      vm_mark_end();
    }
  }
  printf("FXB done\n");
  return 0;
}
