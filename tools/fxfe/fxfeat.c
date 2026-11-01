// fxfeat.c - runs inside QEMU (mps2-an386, Cortex-M4F): features of every window, computed by the Cortex-M4 machine
// code of both front ends, so the accuracy study uses exactly what the chip produces.
//
// Reads int16 windows (FX_LSB_PER_G LSB per g, 1024 samples each) from fx_in.bin over semihosting and writes, per
// window, 280 float32 values to fx_out.bin:
//   float front end on the int16 input (after fx_to_float):  R1 (10), R2 (24), R4 (41)
//   fixed-point front end:  R1 (10), R2 q15 (24), R2 q31 (24), R4 q15 (41), R4 q31 (41)
//   hybrid (integer R1, float FFT):  R2 (24), R4 (41)
// Built and run by tools/fxfe/run_fxfe.py.
#include <stdio.h>

#include "frontend.h"
#include "frontend_fx.h"

extern void initialise_monitor_handles(void);

#define N_OUT (10 + 24 + 41 + 10 + 24 + 24 + 41 + 41 + 24 + 41)

static int16_t s_q[FX_N];
static float s_x[FX_N];
static float s_f[N_OUT];

int main(void) {
  initialise_monitor_handles();
  if (fe_init() != 0 || fx_init() != 0) {
    printf("ERR init\n");
    return 1;
  }
  FILE *in = fopen("fx_in.bin", "rb");
  FILE *out = fopen("fx_out.bin", "wb");
  if (!in || !out) {
    printf("ERR files\n");
    return 1;
  }
  int n = 0;
  while (fread(s_q, sizeof(int16_t), FX_N, in) == FX_N) {
    float *f = s_f;
    fx_to_float(s_q, s_x);
    fe_r1(s_x, f);      f += 10;
    fe_r2(s_x, f);      f += 24;
    fe_r4(s_x, f);      f += 41;
    fx_r1(s_q, f);      f += 10;
    fx_r2_q15(s_q, f);  f += 24;
    fx_r2_q31(s_q, f);  f += 24;
    fx_r4_q15(s_q, f);  f += 41;
    fx_r4_q31(s_q, f);  f += 41;
    fx_r2_hyb(s_q, f);  f += 24;
    fx_r4_hyb(s_q, f);  f += 41;
    if (fwrite(s_f, sizeof(float), N_OUT, out) != N_OUT) {
      printf("ERR write %d\n", n);
      return 1;
    }
    ++n;
  }
  fclose(out);
  fclose(in);
  printf("FX windows=%d values=%d\n", n, N_OUT);
  return 0;
}
