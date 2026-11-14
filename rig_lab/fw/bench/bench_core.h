// bench_core.h - command-driven benchmark firmware (protocol: CONTRACT.md section 7).
#pragma once
#include <stdint.h>

#define BENCH_WIN        1024   // samples per window (float32)
#define BENCH_N_INPUTS   5      // stored windows selectable with INPUT <j>
#define BENCH_MAX_FEAT   1024   // largest front-end output (R0 = normalized window)
#define BENCH_MAX_OUT    16     // largest model output vector (10 classes today)
#define BENCH_LAT_REPS   101    // timed repetitions per stored window in LAT

// One deployable model. bundle.py generates a table of these; nothing here is model-specific.
typedef struct {
  const char *model_id;  // CONTRACT.md section 4, e.g. "cnn-r0-w050d4-int8"
  const char *runtime;   // "tflm-cmsis" | "tflm-ref" | "emlearn" | "stub"
  // One-time setup (AllocateTensors, cache tensor pointers). Returns 0 on success. Never timed.
  int (*setup)(uint32_t *arena_used_bytes);
  // Front end: raw window -> model input features (R0..R3 incl. z-score). The FRONT phase.
  void (*frontend)(const float *window, float *features);
  // Inference: input quantization + Invoke()/predict() + argmax. The RUN phase. Returns 0 on success.
  int (*infer)(const float *features, int *cls);
  // Dequantized outputs of the most recent infer() (for VERIFY). Returns the number written (<= max_n).
  int (*outputs)(float *out, int max_n);
} bench_model_t;

// Provided by the model table (models_stub.cpp today; generated bundle source later).
extern const bench_model_t g_models[];
extern const int g_n_models;

void bench_setup(void);  // call once from setup()
void bench_poll(void);   // call repeatedly from loop(); handles at most one complete command per call
