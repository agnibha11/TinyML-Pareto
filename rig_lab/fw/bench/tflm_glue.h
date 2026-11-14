// tflm_glue.h - TensorFlow Lite Micro runner shared by every NN model of a bundle: one static arena, one interpreter
// at a time, the op union of handoff/ops_union.txt registered explicitly (never AllOpsResolver).
// Used by the board bundles (BUNDLE_TFLM=1), the virtual MCU (tools/vmcu) and the PC simulator.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// SEL: build the interpreter for this .tflite in the static arena. Returns 0 on success (ERR ALLOC otherwise).
int bench_tflm_setup(const unsigned char *model, uint32_t *arena_used_bytes);
// RUN phase (CONTRACT.md 7.5): quantise the features into the input tensor, Invoke(), argmax.
int bench_tflm_infer(const float *features, int *cls);
// VERIFY: dequantised outputs of the last Invoke().
int bench_tflm_outputs(float *out, int max_n);

#ifdef __cplusplus
}
#endif
