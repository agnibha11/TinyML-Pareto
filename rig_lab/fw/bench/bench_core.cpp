// bench_core.cpp - serial command loop, sync-pin windows and DWT latency (CONTRACT.md section 7).
//
// Rules this file enforces:
//   * The board prints only in reply to a command; every command ends with exactly one "OK ..." or "ERR ..." line.
//   * While D2 is HIGH there is no serial I/O, no allocation, no model setup, only the measured loop.
//   * No malloc/new/String: every buffer is static.
#include "bench_core.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "hal.h"
#include "stored_inputs.h"

#ifndef BENCH_GIT_HASH
#define BENCH_GIT_HASH "dev"
#endif

#define LINE_MAX_CHARS 63u
#define VERIFY_BYTES   (BENCH_WIN * 4u)
#define MAX_WINDOW_MS  600000u
#define VERIFY_TIMEOUT_MS 2000u   // board waits this long for the 4096-byte payload after RDY
#define DRAIN_MIN_US      1000000u  // after a VERIFY timeout: discard input for at least 1 s ...
#define DRAIN_SILENCE_US  50000u    // ... and until 50 ms of silence
#define VERIFY_LINE_MAX   (16 + BENCH_MAX_OUT * 20)

static char     s_line[LINE_MAX_CHARS + 1];
static size_t   s_len = 0;
static int      s_overflow = 0;
static int      s_sel = -1;     // selected model index, -1 = none
static int      s_input = 0;    // stored window used by RUN / FRONT / E2E
static float    s_window[BENCH_WIN];       // VERIFY input
static float    s_feat[BENCH_MAX_FEAT];    // front-end output
static float    s_out[BENCH_MAX_OUT];
static uint32_t s_reps[BENCH_LAT_REPS];

// ---------------------------------------------------------------------------------------------------------------
// Output helpers
// ---------------------------------------------------------------------------------------------------------------
static void reply(const char *fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  bhal_write(buf);
  bhal_write("\r\n");
}

// Float formatting without printf("%f") (newlib-nano may be built without float printf).
// CONTRACT.md 7.2: fixed 6 decimals ("-0.012345"); "nan", "inf", "-inf" for non-finite values;
// finite values beyond +/-2e9 are clamped to +/-2000000000.000000.
static int fmt_float(char *dst, size_t cap, float v) {
  if (v != v) return snprintf(dst, cap, "nan");
  if (v > 3.4e38f) return snprintf(dst, cap, "inf");
  if (v < -3.4e38f) return snprintf(dst, cap, "-inf");
  const char *sign = "";
  if (v < 0) { sign = "-"; v = -v; }
  if (v > 2.0e9f) v = 2.0e9f;
  uint32_t ip = (uint32_t)v;
  uint32_t fp = (uint32_t)((v - (float)ip) * 1000000.0f + 0.5f);
  if (fp >= 1000000u) { ip += 1u; fp -= 1000000u; }
  return snprintf(dst, cap, "%s%lu.%06lu", sign, (unsigned long)ip, (unsigned long)fp);
}

// ---------------------------------------------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------------------------------------------
static int parse_u32(const char *s, uint32_t *out) {
  if (!s || !*s) return 0;
  uint64_t v = 0;
  for (; *s; ++s) {
    if (*s < '0' || *s > '9') return 0;
    v = v * 10u + (uint64_t)(*s - '0');
    if (v > 0xFFFFFFFFull) return 0;
  }
  *out = (uint32_t)v;
  return 1;
}

// Discard incoming bytes for at least DRAIN_MIN_US and until the line has been silent for DRAIN_SILENCE_US,
// so a VERIFY payload that arrives late is swallowed instead of being parsed as commands.
static void drain_input(void) {
  const uint32_t t0 = bhal_micros();
  uint32_t last = t0;
  while ((uint32_t)(bhal_micros() - t0) < DRAIN_MIN_US || (uint32_t)(bhal_micros() - last) < DRAIN_SILENCE_US) {
    if (bhal_read_byte() >= 0) last = bhal_micros();
  }
  s_len = 0;
  s_overflow = 0;
}

static void sort_u32(uint32_t *a, int n) {  // insertion sort; n <= 101
  for (int i = 1; i < n; ++i) {
    uint32_t x = a[i];
    int j = i - 1;
    while (j >= 0 && a[j] > x) { a[j + 1] = a[j]; --j; }
    a[j + 1] = x;
  }
}

static uint32_t median_u32(uint32_t *a, int n) { sort_u32(a, n); return a[n / 2]; }

static const bench_model_t *selected(void) {
  if (s_sel < 0) { reply("ERR NOSEL select a model with SEL <k>"); return 0; }
  return &g_models[s_sel];
}

// ---------------------------------------------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------------------------------------------
static void cmd_ping(void) { reply("OK bench=%s n_models=%d", BENCH_GIT_HASH, g_n_models); }

static void cmd_list(void) {
  for (int k = 0; k < g_n_models; ++k) reply("M %d %s %s", k, g_models[k].model_id, g_models[k].runtime);
  reply("OK n=%d", g_n_models);
}

static void cmd_sel(uint32_t k) {
  if (k >= (uint32_t)g_n_models) { reply("ERR RANGE model index %lu >= %d", (unsigned long)k, g_n_models); return; }
  uint32_t arena = 0;
  s_sel = -1;
  if (g_models[k].setup(&arena) != 0) { reply("ERR ALLOC setup failed for %s", g_models[k].model_id); return; }
  s_sel = (int)k;
  reply("OK k=%lu arena_used=%lu", (unsigned long)k, (unsigned long)arena);
}

static void cmd_input(uint32_t j) {
  if (j >= BENCH_N_INPUTS) { reply("ERR RANGE input index %lu >= %d", (unsigned long)j, BENCH_N_INPUTS); return; }
  s_input = (int)j;
  reply("OK j=%lu", (unsigned long)j);
}

static void cmd_idle(uint32_t ms, int deep_sleep) {
  if (ms < 1u || ms > MAX_WINDOW_MS) { reply("ERR ARG ms must be 1..%lu", (unsigned long)MAX_WINDOW_MS); return; }
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  if (deep_sleep) {
    bhal_sleep_ms(ms);
  } else {
    const uint32_t span = ms * 1000u;
    while ((uint32_t)(bhal_micros() - t0) < span) bhal_wait_for_interrupt();
  }
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();
  reply("OK us=%lu", (unsigned long)(t1 - t0));
}

enum { MODE_RUN, MODE_FRONT, MODE_E2E };

static void cmd_window(uint32_t n, int mode) {
  if (n < 1u) { reply("ERR ARG N must be >= 1"); return; }
  const bench_model_t *m = selected();
  if (!m) return;
  const float *x = g_stored_inputs[s_input];
  int cls = -1;

  // Setup and warm-up, outside the window.
  m->frontend(x, s_feat);
  if (mode != MODE_FRONT && m->infer(s_feat, &cls) != 0) { reply("ERR INVOKE warm-up failed"); return; }

  int err = 0;
  const uint32_t t0 = bhal_micros();
  bhal_sync(1);
  if (mode == MODE_RUN) {
    for (uint32_t i = 0; i < n; ++i) err |= m->infer(s_feat, &cls);
  } else if (mode == MODE_FRONT) {
    for (uint32_t i = 0; i < n; ++i) m->frontend(x, s_feat);
  } else {
    for (uint32_t i = 0; i < n; ++i) { m->frontend(x, s_feat); err |= m->infer(s_feat, &cls); }
  }
  bhal_sync(0);
  const uint32_t t1 = bhal_micros();

  if (err) { reply("ERR INVOKE inference failed inside the window"); return; }
  if (mode == MODE_FRONT) reply("OK n=%lu us=%lu", (unsigned long)n, (unsigned long)(t1 - t0));
  else reply("OK n=%lu us=%lu cls=%d", (unsigned long)n, (unsigned long)(t1 - t0), cls);
}

static void cmd_lat(void) {
  const bench_model_t *m = selected();
  if (!m) return;
  uint32_t med_inf[BENCH_N_INPUTS], med_fe[BENCH_N_INPUTS];
  int cls = -1;
  for (int j = 0; j < BENCH_N_INPUTS; ++j) {
    const float *x = g_stored_inputs[j];
    m->frontend(x, s_feat);  // warm-up + features for the inference timing
    if (m->infer(s_feat, &cls) != 0) { reply("ERR INVOKE warm-up failed on input %d", j); return; }
    int err = 0;
    for (int r = 0; r < BENCH_LAT_REPS; ++r) {
      const uint32_t c0 = bhal_cycles();
      err |= m->infer(s_feat, &cls);
      s_reps[r] = bhal_cycles() - c0;
    }
    if (err) { reply("ERR INVOKE inference failed during LAT on input %d", j); return; }
    med_inf[j] = median_u32(s_reps, BENCH_LAT_REPS);
    for (int r = 0; r < BENCH_LAT_REPS; ++r) {
      const uint32_t c0 = bhal_cycles();
      m->frontend(x, s_feat);
      s_reps[r] = bhal_cycles() - c0;
    }
    med_fe[j] = median_u32(s_reps, BENCH_LAT_REPS);
  }
  sort_u32(med_inf, BENCH_N_INPUTS);
  sort_u32(med_fe, BENCH_N_INPUTS);
  reply("OK inf_med_cyc=%lu inf_min_cyc=%lu inf_max_cyc=%lu fe_med_cyc=%lu fe_min_cyc=%lu fe_max_cyc=%lu",
        (unsigned long)med_inf[BENCH_N_INPUTS / 2], (unsigned long)med_inf[0],
        (unsigned long)med_inf[BENCH_N_INPUTS - 1], (unsigned long)med_fe[BENCH_N_INPUTS / 2],
        (unsigned long)med_fe[0], (unsigned long)med_fe[BENCH_N_INPUTS - 1]);
}

static void cmd_verify(void) {
  const bench_model_t *m = selected();
  if (!m) return;
  reply("RDY %u", (unsigned)VERIFY_BYTES);
  const size_t got = bhal_read_bytes((uint8_t *)s_window, VERIFY_BYTES, VERIFY_TIMEOUT_MS);
  if (got != VERIFY_BYTES) {
    drain_input();  // late payload bytes must not be parsed as commands
    reply("ERR IO expected %u bytes, got %lu", (unsigned)VERIFY_BYTES, (unsigned long)got);
    return;
  }
  int cls = -1;
  m->frontend(s_window, s_feat);
  if (m->infer(s_feat, &cls) != 0) { reply("ERR INVOKE inference failed"); return; }
  const int n = m->outputs(s_out, BENCH_MAX_OUT);
  char buf[VERIFY_LINE_MAX];
  int w = snprintf(buf, sizeof buf, "OK cls=%d out=", cls);
  size_t pos = (size_t)w;
  for (int i = 0; i < n; ++i) {
    char num[32];
    const int len = fmt_float(num, sizeof num, s_out[i]);
    if (len < 0 || pos + (size_t)len + 2 >= sizeof buf) { reply("ERR IO output line too long (%d values)", n); return; }
    if (i) buf[pos++] = ',';
    memcpy(buf + pos, num, (size_t)len);
    pos += (size_t)len;
  }
  buf[pos] = '\0';
  bhal_write(buf);
  bhal_write("\r\n");
}

// ---------------------------------------------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------------------------------------------
static void dispatch(char *line) {
  char *argv[2] = {0, 0};
  int argc = 0;
  int too_many = 0;
  for (char *tok = strtok(line, " "); tok; tok = strtok(0, " ")) {
    if (argc == 2) { too_many = 1; break; }
    argv[argc++] = tok;
  }
  if (argc == 0) { reply("ERR UNKNOWN empty line"); return; }
  const char *cmd = argv[0];
  static const char *const known[] = {"PING", "LIST", "SEL", "INPUT", "IDLE", "SLEEP",
                                      "RUN", "FRONT", "E2E", "LAT", "VERIFY"};
  int is_known = 0;
  for (unsigned i = 0; i < sizeof known / sizeof known[0]; ++i) is_known |= !strcmp(cmd, known[i]);
  if (!is_known) { reply("ERR UNKNOWN %s", cmd); return; }  // unknown wins over argument errors
  uint32_t a = 0;
  const int has_arg = argc >= 2;
  if (too_many) { reply("ERR ARG too many arguments"); return; }
  if (has_arg && !parse_u32(argv[1], &a)) { reply("ERR ARG not an unsigned integer: %s", argv[1]); return; }

  // Commands without an argument.
  if (!strcmp(cmd, "PING") || !strcmp(cmd, "LIST") || !strcmp(cmd, "LAT") || !strcmp(cmd, "VERIFY")) {
    if (has_arg) { reply("ERR ARG %s takes no argument", cmd); return; }
    if (!strcmp(cmd, "PING")) cmd_ping();
    else if (!strcmp(cmd, "LIST")) cmd_list();
    else if (!strcmp(cmd, "LAT")) cmd_lat();
    else cmd_verify();
    return;
  }
  // Commands with exactly one argument.
  static const char *const one_arg[] = {"SEL", "INPUT", "IDLE", "SLEEP", "RUN", "FRONT", "E2E"};
  for (unsigned i = 0; i < sizeof one_arg / sizeof one_arg[0]; ++i) {
    if (strcmp(cmd, one_arg[i]) != 0) continue;
    if (!has_arg) { reply("ERR ARG %s needs one argument", cmd); return; }
    switch (i) {
      case 0: cmd_sel(a); break;
      case 1: cmd_input(a); break;
      case 2: cmd_idle(a, 0); break;
      case 3: cmd_idle(a, 1); break;
      case 4: cmd_window(a, MODE_RUN); break;
      case 5: cmd_window(a, MODE_FRONT); break;
      default: cmd_window(a, MODE_E2E); break;
    }
    return;
  }
  reply("ERR UNKNOWN %s", cmd);
}

void bench_setup(void) {
  bhal_init();
  s_len = 0;
  s_overflow = 0;
  s_sel = -1;
  s_input = 0;
}

void bench_poll(void) {
  int c;
  while ((c = bhal_read_byte()) >= 0) {
    if (c == '\r') continue;
    if (c == '\n') {
      s_line[s_len] = '\0';
      const int overflow = s_overflow;
      s_len = 0;
      s_overflow = 0;
      if (overflow) reply("ERR ARG line longer than %u characters", LINE_MAX_CHARS);
      else dispatch(s_line);
      return;  // one command per poll; the next one is handled on the next loop() call
    }
    if (s_len < LINE_MAX_CHARS) s_line[s_len++] = (char)c;
    else s_overflow = 1;
  }
}
