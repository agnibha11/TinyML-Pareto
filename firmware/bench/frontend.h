// frontend.h - C front end R0..R4 (handoff/features_spec.md v1.2), float32, no dynamic allocation.
//
// Portable C99 + CMSIS-DSP (arm_rfft_fast_f32), so the same file builds for the board (Arduino_CMSIS-DSP 5.7.0), the
// virtual MCU (tools/vmcu) and the PC (host simulation). Checked against ml/features.py by the M2 parity test
// (tools/vmcu/run_vmcu.py -> results/vmcu/vmcu_parity.csv).
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FE_N 1024

// Call once before any fe_r2 / fe_r3 / fe_r4 call (initialises the CMSIS-DSP rFFT instances and R4's 256-point
// Hann window). Returns 0 on success.
int fe_init(void);

// R0: out[i] = (x[i] - mu) / sigma, i = 0..1023.
void fe_r0(const float *x, float mu, float sigma, float *out);
// R1: 10 time-domain features (order of features_spec.md).
void fe_r1(const float *x, float *out10);
// R2: R1 + 14 log10 band powers of the Hann-windowed 1024-point rFFT (0-5250 Hz, spec v1.1).
#define FE_R2_BANDS 14
void fe_r2(const float *x, float *out24);
// R3: log10(|X[k]| + 1e-6), k = 1..256.
void fe_r3(const float *x, float *out256);
// R4: R1 + 32 log envelope-spectrum bins (spec v1.2): 2.0-5.0 kHz band of the 1024-point rFFT shifted to baseband,
// |256-point inverse cFFT| = envelope, minus its mean, times a 256-point Hann, |256-point rFFT| bins 1..32,
// divided by their mean, log10(. + 1e-6).
#define FE_ENV_K0 171
#define FE_ENV_NB 256
#define FE_ENV_NK 32
void fe_r4(const float *x, float *out42);
// In-place z-score: f[i] = (f[i] - mean[i]) / std[i].
void fe_zscore(float *f, const float *mean, const float *std, int n);

// Bytes of static RAM the front end uses (work buffers + FFT instance), for the memory table.
uint32_t fe_static_ram_bytes(void);

#ifdef __cplusplus
}
#endif
