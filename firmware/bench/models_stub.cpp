// models_stub.cpp - bring-up model table, used until bundle.py generates real bundles (BUNDLE_ID defined).
//
// "stub-noop" does a small, deterministic amount of work so that every command (SEL, RUN, FRONT, E2E, LAT,
// VERIFY) can be exercised end to end before any real model exists. It is never measured for the paper.
#ifndef BUNDLE_ID

#include "bench_core.h"

static float s_stub_out[10];

static int stub_setup(uint32_t *arena_used) {
  *arena_used = 0;
  return 0;
}

// R0-style front end with mu = 0, sigma = 1 (a copy).
static void stub_frontend(const float *x, float *feat) {
  for (int i = 0; i < BENCH_WIN; ++i) feat[i] = x[i];
}

// Energy in 10 equal segments of the window; class = loudest segment.
static int stub_infer(const float *feat, int *cls) {
  const int seg = BENCH_WIN / 10;
  int best = 0;
  for (int s = 0; s < 10; ++s) {
    float e = 0.0f;
    for (int i = s * seg; i < (s + 1) * seg; ++i) e += feat[i] * feat[i];
    s_stub_out[s] = e;
    if (e > s_stub_out[best]) best = s;
  }
  *cls = best;
  return 0;
}

static int stub_outputs(float *out, int max_n) {
  const int n = max_n < 10 ? max_n : 10;
  for (int i = 0; i < n; ++i) out[i] = s_stub_out[i];
  return n;
}

const bench_model_t g_models[] = {
    {"stub-noop", "stub", stub_setup, stub_frontend, stub_infer, stub_outputs},
};
const int g_n_models = (int)(sizeof g_models / sizeof g_models[0]);

#ifdef BENCH_CASCADE
// CASCADE m = 2 on the simulator: the stub's front end is a copy, so "reuse" copies too and overwrites the first 10
// features with the gate's fx_r1 output.
static void stub_reuse(const float *x, const float *r1, float *feat) {
  for (int i = 0; i < BENCH_WIN; ++i) feat[i] = x[i];
  for (int i = 0; i < 10; ++i) feat[i] = r1[i];
}
const bench_reuse_fe_t g_model_reuse[] = {stub_reuse};
#ifdef CASC_HAS_G6_STUB   // Paderborn cascade simulator: gate 6 hands its R2 features over
static void stub_reuse_r2(const float *h, float *feat) {
  for (int i = 0; i < 24; ++i) feat[i] = h[i];
}
const bench_reuse_r2_t g_model_reuse_r2[] = {stub_reuse_r2};
#endif
#endif

#endif  // BUNDLE_ID
