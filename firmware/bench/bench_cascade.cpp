// bench_cascade.cpp - cascade commands (CONTRACT.md 7.6): TEMP, GSEL, GVERIFY, GATE, CASCADE, STREAM, CALIB, DUTY,
// FXFE. Built for real only in a cascade bundle (-DBENCH_CASCADE=1, data from tools/bundle.py --cascade); every other
// build answers these commands with ERR NOTIMPL.
//
// The window rules of bench_core.cpp hold here too: setup and one warm-up of the loop body outside the window, then
// D2 HIGH, the loop, D2 LOW, then the reply. No serial I/O and no allocation inside the window.
#include <stdio.h>
#include <string.h>

#include "bench_internal.h"
#include "hal.h"

#ifndef BENCH_CASCADE

void bench_cascade_setup(void) {}

void bench_cascade_command(const char *cmd, int nargs, const uint32_t *args) {
  (void)nargs;
  (void)args;
  bench_reply("ERR NOTIMPL %s needs a cascade bundle", cmd);
}

#else  // BENCH_CASCADE

#include "cascade.h"
#include "frontend_fx.h"
#include "stored_inputs.h"

#ifndef CASC_DATA_H
#define CASC_DATA_H "models/cascade_data.h"
#endif
#include CASC_DATA_H  // gate parameters, g_casc_pool[CASC_POOL_N][1024], g_casc_streams[CASC_N_STREAMS][100]

#define MAX_PERIOD_MS 60000u

#ifdef CASC_HAS_G6   // Paderborn: gate_params.h defines G6; the build must give cascade.c room for 23 features
#if !CASC_G6_ENABLED || CASC_CAL_MAX < CASC_G6_N
#error "G6 needs -DCASC_NG_MAX=23 -DCASC_CAL_MAX=128 (tools/build_flash.py, bundles.json defines)"
#endif
#define N_GATES 6
#define G6_RHO g_casc_g6_rho
#else
#define N_GATES 5
#define G6_RHO 0.0f
#endif

static const casc_params_t k_params = {
    CASC_TREE_NODES,       g_casc_tree_feature, g_casc_tree_left,  g_casc_tree_right, g_casc_tree_threshold,
    g_casc_tree_alarm,     g_casc_mahal_mu,     g_casc_mahal_ci,   g_casc_mahal_thr,  g_casc_selfcal_rho,
    CASC_RIDGE,            G6_RHO};
static const char *const k_gate_names[7] = {"none", "sup", "healthy", "either", "either2", "selfcal", "selfcal_r2"};

static int        s_gate = 0;               // GSEL; 0 = none
static casc_cal_t s_cal;                    // gate 5 state (valid after a successful CALIB)
#ifdef CASC_HAS_G6
static casc_cal_t s_cal6;                   // gate 6 state (CALIB with gate 6 selected)
static float      s_f24[24], s_g23[CASC_G6_FEATS];   // the G6 window's fx_r2_hyb output and gate features
#endif
static int16_t    s_q[BENCH_WIN];           // int16 copy of the stored input, or the GVERIFY payload
static float      s_x[BENCH_WIN];           // float window for stage 2
static float      s_fx[CASC_NFX];           // the gate's fx_r1 features
static float      s_feat[BENCH_MAX_FEAT];   // stage-2 features
static float      s_fxfe[41];               // FXFE output (R4: 41 features)

static unsigned long fbits(float v) {
  uint32_t u;
  memcpy(&u, &v, sizeof u);
  return (unsigned long)u;
}

void bench_cascade_setup(void) {
  s_gate = 0;
  memset(&s_cal, 0, sizeof s_cal);
#ifdef CASC_HAS_G6
  memset(&s_cal6, 0, sizeof s_cal6);
#endif
  fx_init_hyb();  // FFT instances of the hybrid R2/R4 front ends (FXFE 1, 2)
}

// The selected gate, after checking it can run. 0 after an ERR reply.
static int gate_ready(void) {
  if (s_gate == 0) { bench_reply("ERR NOSEL select a gate with GSEL <g>"); return 0; }
  if (s_gate == CASC_SELFCAL && !s_cal.valid) { bench_reply("ERR NOCAL gate 5 needs a successful CALIB"); return 0; }
#ifdef CASC_HAS_G6
  if (s_gate == CASC_SELFCAL_R2 && !s_cal6.valid) {
    bench_reply("ERR NOCAL gate 6 needs a successful CALIB with gate 6 selected");
    return 0;
  }
#endif
  return s_gate;
}

// One gate decision. s_fx holds the window's fx_r1 features afterwards (and s_f24 the full R2 features for G6).
static inline int gate(const int16_t *w, casc_state_t *st) {
#ifdef CASC_HAS_G6
  if (s_gate == CASC_SELFCAL_R2) {
    casc_g6_features(w, s_f24, s_g23);
    memcpy(s_fx, s_f24, sizeof s_fx);
    return casc_g6_decide(&s_cal6, s_g23);
  }
#endif
  fx_r1(w, s_fx);
  return casc_decide_fx(&k_params, &s_cal, s_gate, s_fx, st);
}

static void cmd_temp(void) { bench_reply("OK temp_centi_c=%ld", (long)bhal_temp_cC()); }

static void cmd_gsel(uint32_t g) {
  if (g < 1u || g > (uint32_t)N_GATES) { bench_reply("ERR RANGE gate %lu not in 1..%d", (unsigned long)g, N_GATES); return; }
  s_gate = (int)g;
  bench_reply("OK g=%lu name=%s", (unsigned long)g, k_gate_names[g]);
}

// GVERIFY: RDY 2048, 1024 int16 LE, then every base gate on that window (casc_eval). Independent of GSEL and SEL.
static void cmd_gverify(void) {
  const size_t nb = sizeof s_q;
  bench_reply("RDY %u", (unsigned)nb);
  const size_t got = bhal_read_bytes((uint8_t *)s_q, nb, 2000u);
  if (got != nb) {
    bench_drain_input();
    bench_reply("ERR IO expected %u bytes, got %lu", (unsigned)nb, (unsigned long)got);
    return;
  }
  casc_eval_t e;
  casc_eval(&k_params, &s_cal, s_q, &e);
  static char buf[400];   // longer than bench_reply's 256: written directly
  int pos = snprintf(buf, sizeof buf, "OK sup=%d leaf=%d healthy=%d d=%08lx sc=%d dsc=%08lx fx=", e.sup, e.leaf,
                     e.healthy, fbits(e.d), e.sc, fbits(e.dsc));
  for (int i = 0; i < CASC_NFX; ++i)
    pos += snprintf(buf + pos, sizeof buf - (size_t)pos, i ? ",%08lx" : "%08lx", fbits(e.fx[i]));
#ifdef CASC_HAS_G6
  // G6: sr = -1 until calibrated; dsr its distance; hb = the 14 log band powers of fx_r2_hyb
  casc_g6_features(s_q, s_f24, s_g23);
  const int s6 = casc_g6_decide(&s_cal6, s_g23);
  const float d6 = s_cal6.valid ? casc_mahal_n(s_g23, s_cal6.mu, s_cal6.ci, CASC_G6_FEATS) : 0.0f;
  pos += snprintf(buf + pos, sizeof buf - (size_t)pos, " sr=%d dsr=%08lx hb=", s6, fbits(d6));
  for (int i = 0; i < 14; ++i)
    pos += snprintf(buf + pos, sizeof buf - (size_t)pos, i ? ",%08lx" : "%08lx", fbits(s_f24[10 + i]));
#endif
  bhal_write(buf);
  bhal_write("\r\n");
}

static void load_stored(void) { fx_from_float(g_stored_inputs[bench_input_index()], s_q); }

static void cmd_gate(uint32_t n) {
  if (!gate_ready()) return;
  load_stored();
  casc_state_t st = {0};
  int alarm = gate(s_q, &st);  // warm-up
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  st.prev_either = 0;
  for (uint32_t i = 0; i < n; ++i) alarm = gate(s_q, &st);
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  bench_reply("OK n=%lu us=%lu alarm=%d", (unsigned long)n, (unsigned long)(t1 - t0), alarm);
}

// CASCADE: stage 2 runs on the float stored input itself (the window E2E measures), so that
// E(CASCADE 1) - E(GATE) - E2E(model) is the cascade's own overhead (RQ3). m = 2 replaces the model's R1 part with
// the gate's fx_r1 features (RQ6); with gate 6 an R2 model takes the gate's whole R2 front end (g_model_reuse_r2).
static void cmd_cascade(uint32_t n, uint32_t m) {
  if (m > 2u) { bench_reply("ERR ARG m must be 0, 1 or 2"); return; }
  if (!gate_ready()) return;
  const bench_model_t *mod = bench_selected();
  if (!mod) return;
  const bench_reuse_fe_t reuse = g_model_reuse[bench_selected_index()];
  if (m == 2u && !reuse) { bench_reply("ERR NOTIMPL %s has no R1 part to reuse", mod->model_id); return; }
#ifdef CASC_HAS_G6
  const bench_reuse_r2_t reuse_r2 = s_gate == CASC_SELFCAL_R2 ? g_model_reuse_r2[bench_selected_index()] : 0;
#define REUSE(x) (reuse_r2 ? reuse_r2(s_f24, s_feat) : reuse((x), s_fx, s_feat))
#else
#define REUSE(x) reuse((x), s_fx, s_feat)
#endif
  load_stored();
  const float *x = g_stored_inputs[bench_input_index()];
  casc_state_t st = {0};
  int cls = -1, err = 0;
  // warm-up: gate and stage 2 once
  gate(s_q, &st);
  if (m == 2u) REUSE(x);
  else mod->frontend(x, s_feat);
  if (mod->infer(s_feat, &cls) != 0) { bench_reply("ERR INVOKE warm-up failed"); return; }
  uint32_t alarms = 0, esc = 0;
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  st.prev_either = 0;
  for (uint32_t i = 0; i < n; ++i) {
    const int a = gate(s_q, &st);
    alarms += (uint32_t)a;
    if (a || m != 0u) {
      if (m == 2u) REUSE(x);
      else mod->frontend(x, s_feat);
      err |= mod->infer(s_feat, &cls);
      ++esc;
    }
  }
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  if (err) { bench_reply("ERR INVOKE inference failed inside the window"); return; }
  bench_reply("OK n=%lu us=%lu alarms=%lu esc=%lu cls=%d", (unsigned long)n, (unsigned long)(t1 - t0),
              (unsigned long)alarms, (unsigned long)esc, cls);
}

// One stream window: gate on the int16 pool window; on an alarm, int16 -> float, the model's front end, inference.
static inline int stream_window(const bench_model_t *mod, const int16_t *w, casc_state_t *st, int *cls, int *err,
                                uint32_t *esc) {
  const int a = gate(w, st);
  if (a) {
    fx_to_float(w, s_x);
    mod->frontend(s_x, s_feat);
    *err |= mod->infer(s_feat, cls);
    ++*esc;
  }
  return a;
}

static void cmd_stream(uint32_t n, uint32_t s) {
  if (s >= (uint32_t)CASC_N_STREAMS) { bench_reply("ERR RANGE stream %lu >= %d", (unsigned long)s, CASC_N_STREAMS); return; }
  if (!gate_ready()) return;
  const bench_model_t *mod = bench_selected();
  if (!mod) return;
  const uint8_t *idx = g_casc_streams[s];
  casc_state_t st = {0};
  int cls = -1, err = 0;
  uint32_t esc = 0, alarms = 0, seq = 2166136261u;  // FNV-1a offset basis
  // warm-up: gate and stage 2 once on the first window of the stream
  gate(g_casc_pool[idx[0]], &st);
  fx_to_float(g_casc_pool[idx[0]], s_x);
  mod->frontend(s_x, s_feat);
  if (mod->infer(s_feat, &cls) != 0) { bench_reply("ERR INVOKE warm-up failed"); return; }
  esc = 0;
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  for (uint32_t pass = 0; pass < n; ++pass) {
    st.prev_either = 0;
    for (int i = 0; i < CASC_STREAM_LEN; ++i) {
      const int a = stream_window(mod, g_casc_pool[idx[i]], &st, &cls, &err, &esc);
      alarms += (uint32_t)a;
      if (pass == 0u) seq = (seq ^ (uint32_t)a) * 16777619u;
    }
  }
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  if (err) { bench_reply("ERR INVOKE inference failed inside the window"); return; }
  bench_reply("OK n=%lu us=%lu windows=%lu alarms=%lu esc=%lu seq=%08lx", (unsigned long)n,
              (unsigned long)(t1 - t0), (unsigned long)(n * (uint32_t)CASC_STREAM_LEN), (unsigned long)alarms,
              (unsigned long)esc, (unsigned long)seq);
}

// CALIB calibrates gate 6 when gate 6 is selected, gate 5 otherwise.
static void cmd_calib(uint32_t n, uint32_t r) {
  const uint32_t nmax = CASC_POOL_HEALTHY < CASC_CAL_MAX ? CASC_POOL_HEALTHY : CASC_CAL_MAX;
  if (n < 2u || n > nmax) { bench_reply("ERR ARG n must be 2..%lu", (unsigned long)nmax); return; }
  if (r < 1u) { bench_reply("ERR ARG R must be >= 1"); return; }
  static casc_cal_t cal;
  typedef int (*calib_t)(const casc_params_t *, const int16_t (*)[1024], int, casc_cal_t *);
  calib_t fn = casc_calibrate;
  casc_cal_t *dst = &s_cal;
#ifdef CASC_HAS_G6
  if (s_gate == CASC_SELFCAL_R2) { fn = casc_calibrate_g6; dst = &s_cal6; }
#endif
  int rc = fn(&k_params, g_casc_pool, (int)n, &cal);  // warm-up
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  for (uint32_t i = 0; i < r; ++i) rc |= fn(&k_params, g_casc_pool, (int)n, &cal);
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  if (rc != 0) {
    dst->valid = 0;
    bench_reply("ERR NOCAL calibration failed (%d): covariance not positive definite", rc);
    return;
  }
  *dst = cal;
  bench_reply("OK n=%lu r=%lu us=%lu thr=%08lx", (unsigned long)n, (unsigned long)r, (unsigned long)(t1 - t0),
              fbits(dst->thr));
}

// DUTY: N periods of T ms on a fixed schedule (period p ends at t0 + (p + 1) T). Each period runs the next stream-0
// window through the gate (and stage 2 on an alarm), then sleeps like SLEEP and waits for interrupts to the end.
static void cmd_duty(uint32_t t_ms, uint32_t n) {
  if (t_ms < 10u || t_ms > MAX_PERIOD_MS) { bench_reply("ERR ARG T must be 10..%lu", (unsigned long)MAX_PERIOD_MS); return; }
  if (!gate_ready()) return;
  const bench_model_t *mod = bench_selected();
  if (!mod) return;
  if ((uint64_t)t_ms * n > 600000ull) { bench_reply("ERR ARG T x N must be <= 600000 ms"); return; }
  const uint8_t *idx = g_casc_streams[0];
  casc_state_t st = {0};
  int cls = -1, err = 0;
  uint32_t esc = 0, alarms = 0, overrun = 0;
  gate(g_casc_pool[idx[0]], &st);  // warm-up
  fx_to_float(g_casc_pool[idx[0]], s_x);
  mod->frontend(s_x, s_feat);
  if (mod->infer(s_feat, &cls) != 0) { bench_reply("ERR INVOKE warm-up failed"); return; }
  const uint32_t period_us = t_ms * 1000u;
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  st.prev_either = 0;
  for (uint32_t p = 0; p < n; ++p) {
    alarms += (uint32_t)stream_window(mod, g_casc_pool[idx[p % CASC_STREAM_LEN]], &st, &cls, &err, &esc);
    const uint32_t end = (p + 1u) * period_us;  // relative to t0
    uint32_t now = bhal_micros() - t0;
    if (now >= end) { ++overrun; continue; }
    if (end - now > 2000u) bhal_sleep_ms((end - now) / 1000u - 1u);
    while ((now = bhal_micros() - t0) < end) bhal_wait_for_interrupt();
  }
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  if (err) { bench_reply("ERR INVOKE inference failed inside the window"); return; }
  bench_reply("OK n=%lu us=%lu alarms=%lu esc=%lu overrun=%lu", (unsigned long)n, (unsigned long)(t1 - t0),
              (unsigned long)alarms, (unsigned long)esc, (unsigned long)overrun);
}

static void cmd_fxfe(uint32_t n, uint32_t p) {
  typedef void (*fe_t)(const int16_t *, float *);
  // 0 fx_r1, 1 fx_r2_hyb, 2 fx_r4_hyb (the q15/q31 paths need CMSIS-DSP functions the board library lacks)
  static const fe_t fes[3] = {fx_r1, fx_r2_hyb, fx_r4_hyb};
  if (p > 2u) { bench_reply("ERR ARG p must be 0..2"); return; }
  load_stored();
  const fe_t fe = fes[p];
  fe(s_q, s_fxfe);  // warm-up
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  for (uint32_t i = 0; i < n; ++i) fe(s_q, s_fxfe);
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  bench_reply("OK n=%lu us=%lu", (unsigned long)n, (unsigned long)(t1 - t0));
}

void bench_cascade_command(const char *cmd, int nargs, const uint32_t *a) {
  static const struct { const char *name; int nargs; } k_cmds[] = {
      {"TEMP", 0}, {"GSEL", 1}, {"GVERIFY", 0}, {"GATE", 1}, {"CASCADE", 2},
      {"STREAM", 2}, {"CALIB", 2}, {"DUTY", 2}, {"FXFE", 2}};
  int k = -1;
  for (int i = 0; i < (int)(sizeof k_cmds / sizeof k_cmds[0]); ++i)
    if (!strcmp(cmd, k_cmds[i].name)) k = i;
  if (k < 0) { bench_reply("ERR UNKNOWN %s", cmd); return; }
  if (nargs != k_cmds[k].nargs) { bench_reply("ERR ARG %s takes %d argument(s)", cmd, k_cmds[k].nargs); return; }
  // N >= 1 for every timed command; DUTY's N is its second argument
  const int has_n = k == 3 || k == 4 || k == 5 || k == 8;
  if ((has_n && a[0] < 1u) || (k == 7 && a[1] < 1u)) { bench_reply("ERR ARG N must be >= 1"); return; }
  switch (k) {
    case 0: cmd_temp(); break;
    case 1: cmd_gsel(a[0]); break;
    case 2: cmd_gverify(); break;
    case 3: cmd_gate(a[0]); break;
    case 4: cmd_cascade(a[0], a[1]); break;
    case 5: cmd_stream(a[0], a[1]); break;
    case 6: cmd_calib(a[0], a[1]); break;
    case 7: cmd_duty(a[0], a[1]); break;
    default: cmd_fxfe(a[0], a[1]); break;
  }
}

#endif  // BENCH_CASCADE
