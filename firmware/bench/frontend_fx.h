// frontend_fx.h - fixed-point front end for R1, R2 and R4 (handoff/features_spec.md v1.2), from int16 sensor samples.
//
// Same features, same units as the float front end (frontend.h), so a model trained on float features runs on these
// unchanged. The input is what a vibration accelerometer delivers: int16 samples at FX_LSB_PER_G LSB per g (+/-8 g
// full scale, 16 bit, as an IIS3DWB-class MEMS sensor). The float front end needs the same samples converted to
// float first (fx_to_float), which this front end avoids.
//
//   fx_r1      integer moments, exact (two passes: integer mean, then central moments of d = x - mean in int64)
//   fx_r2_q15  R1 + Hann + 1024-point arm_rfft_q15 + band powers (SMLALD), block-floating-point input scaling
//   fx_r2_q31  same with arm_rfft_q31
//   fx_r4_q15  R1 w/o mean + envelope spectrum: rfft_q15 -> band 171..426 -> inverse cfft_q15 (256) -> |.| ->
//              mean removal, Hann -> rfft_q15 (256) -> |.| bins 1..32, block-floating-point rescaling between stages
//   fx_r4_q31  same with the q31 transforms
//   fx_r2_hyb, fx_r4_hyb   hybrid for cores with an FPU: integer fx_r1, float FFT stages (CMSIS-DSP f32) fed straight
//              from int16 (conversion fused with the window). On the Cortex-M4F this is the fastest R2/R4.
//
// Only the last few scalar steps (ratios, square roots, log10) use the FPU; every per-sample and per-bin loop is
// integer. Accuracy and cost against the float front end: tools/fxfe/ and ml/fixed_point.py.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FX_N 1024
#define FX_LSB_PER_G 4096.0f   // +/-8 g full scale, 16 bit

int fx_init(void);                                   // call once; 0 on success
void fx_to_float(const int16_t *q, float *x);         // what a float front end must do first: x = q / FX_LSB_PER_G
void fx_from_float(const float *x, int16_t *q);       // test helper: round and saturate g -> int16

void fx_r1(const int16_t *q, float *out10);
void fx_r2_q15(const int16_t *q, float *out24);
void fx_r2_q31(const int16_t *q, float *out24);
void fx_r4_q15(const int16_t *q, float *out41);
void fx_r4_q31(const int16_t *q, float *out41);
void fx_r2_hyb(const int16_t *q, float *out24);
void fx_r4_hyb(const int16_t *q, float *out41);

uint32_t fx_static_ram_bytes(int path);               // work buffers + FFT instances: 0 q15, 1 q31, 2 hybrid

#ifdef __cplusplus
}
#endif
