// vm_model.h - one deployable model in the virtual-MCU image (mirrors firmware/bench/bench_core.h bench_model_t,
// with the front end selected by representation instead of a per-model function pointer).
#pragma once
#include <stdint.h>

typedef struct {
  const char *model_id;   // CONTRACT.md section 4
  const char *runtime;    // "tflm-cmsis" | "tflm-ref" | "emlearn"
  int rep;                // 0..4 = R0..R4
  int (*setup)(uint32_t *arena_used_bytes);   // never measured
  int (*infer)(const float *features, int *cls);  // RUN phase: quantise input, Invoke/predict, argmax
  int (*outputs)(float *out, int max_n);      // dequantised outputs (softmax) or one-hot for trees
} vm_model_t;

extern const vm_model_t g_vm_models[];
extern const int g_vm_n_models;
extern const int g_vm_emit_parity;

// TFLM runner shared with the board: firmware/bench/tflm_glue.cpp (bench_tflm_setup / _infer / _outputs).
#include "tflm_glue.h"
