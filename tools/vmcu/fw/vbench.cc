// vbench.cc - virtual-MCU benchmark: the deployed models running as Cortex-M4F code inside QEMU (mps2-an386).
//
// For every model in the generated table (bundle_gen.cc) it prints, over semihosting:
//   M <k> <model_id> <runtime> <rep>            model header
//   A <k> <rc> <arena_used_bytes>                setup (AllocateTensors for TFLM; 0 for trees)
//   L FE <k> <j> / L INF <k> <j>                 label of the next measured region (QEMU plugin prints its counts)
//   C <k> <j> <class>                            class predicted for stored window j
//   S <k> <stack_bytes>                          peak stack of one front end + inference (painted-stack method)
//   V <k> <i> <class> <o0>,<o1>,...              VERIFY-equivalent outputs for test vector i (CONTRACT.md 7.2)
// and once per image, for the M2 feature-parity check:
//   P <rep> <i> <f0>,<f1>,...                    normalised front-end output for test vector i
//
// Phases follow CONTRACT.md 7.5: FRONT = features + z-score (or R0 normalisation); RUN = input quantisation +
// Invoke()/predict() + argmax. One un-measured warm-up call precedes every measured call.
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "frontend.h"
#include "norm_constants.h"
#include "vm_inputs.h"
#include "vm_model.h"

extern "C" void vm_mark_begin(void);
extern "C" void vm_mark_end(void);
extern "C" void initialise_monitor_handles(void);

static float s_feat[FE_N];
static float s_out[16];

static void frontend(int rep, const float *x, float *f) {
  switch (rep) {
    case 0: fe_r0(x, g_r0_mu, g_r0_sigma, f); break;
    case 1: fe_r1(x, f); fe_zscore(f, g_r1_mean, g_r1_std, 10); break;
    case 2: fe_r2(x, f); fe_zscore(f, g_r2_mean, g_r2_std, 24); break;
    case 3: fe_r3(x, f); fe_zscore(f, g_r3_mean, g_r3_std, 256); break;
    default: fe_r4(x, f); fe_zscore(f, g_r4_mean, g_r4_std, 42); break;
  }
}

static const int k_rep_len[5] = {1024, 10, 24, 256, 42};

// --- painted-stack high-water mark ------------------------------------------------------------------------------
// main() paints PAINT_BYTES below its own stack pointer (inline, through a volatile pointer, so the stores cannot
// be optimised away and nothing below SP is live), runs one front end + inference, then finds the deepest word that
// changed. The result is the stack depth of that call chain below main's frame.
#define PAINT_BYTES (48 * 1024)
#define PAINT_WORD 0xC0DEFEEDu

static inline uint32_t current_sp(void) {
  uint32_t sp;
  __asm volatile("mov %0, sp" : "=r"(sp));
  return sp;
}

__attribute__((noinline)) static void probe_run(const vm_model_t *m, const float *x, int *cls) {
  frontend(m->rep, x, s_feat);
  m->infer(s_feat, cls);
}

static void print_vec(const char *head, const float *v, int n) {
  printf("%s", head);
  for (int i = 0; i < n; ++i) printf(i ? ",%.6f" : "%.6f", (double)v[i]);
  printf("\n");
}

int main(void) {
  initialise_monitor_handles();
  if (fe_init() != 0) {
    printf("ERR fe_init\n");
    return 1;
  }
  printf("VB start n_models=%d fe_ram=%lu\n", g_vm_n_models, (unsigned long)fe_static_ram_bytes());

  // M2 parity: every representation used by this image, on the 100 test vectors.
  if (g_vm_emit_parity) {
    for (int rep = 1; rep <= 4; ++rep) {
      for (int i = 0; i < VM_N_TV; ++i) {
        char head[32];
        frontend(rep, g_vm_tv[i], s_feat);
        snprintf(head, sizeof head, "P %d %d ", rep, i);
        print_vec(head, s_feat, k_rep_len[rep]);
      }
    }
    // R0 on the first 3 vectors (1024 values each) is enough to check the normalisation constants.
    for (int i = 0; i < 3; ++i) {
      frontend(0, g_vm_tv[i], s_feat);
      float mx = 0.0f;
      for (int t = 0; t < FE_N; ++t) {
        float ref = (g_vm_tv[i][t] - g_r0_mu) / g_r0_sigma;
        float e = fabsf(ref - s_feat[t]);
        if (e > mx) mx = e;
      }
      printf("P0ERR %d %.9f\n", i, (double)mx);
    }
  }

  for (int k = 0; k < g_vm_n_models; ++k) {
    const vm_model_t *m = &g_vm_models[k];
    printf("M %d %s %s %d\n", k, m->model_id, m->runtime, m->rep);
    uint32_t used = 0;
    int rc = m->setup(&used);
    printf("A %d %d %lu\n", k, rc, (unsigned long)used);
    if (rc != 0) continue;
    int cls = -1;
    for (int j = 0; j < VM_N_STORED; ++j) {
      const float *x = g_vm_stored[j];
      frontend(m->rep, x, s_feat);
      printf("L FE %d %d\n", k, j);
      vm_mark_begin();
      frontend(m->rep, x, s_feat);
      vm_mark_end();
      m->infer(s_feat, &cls);
      printf("L INF %d %d\n", k, j);
      vm_mark_begin();
      m->infer(s_feat, &cls);
      vm_mark_end();
      printf("C %d %d %d\n", k, j, cls);
    }
    volatile uint32_t *top = (volatile uint32_t *)(current_sp() & ~3u);
    volatile uint32_t *bot = top - PAINT_BYTES / 4;
    for (volatile uint32_t *p = bot; p < top; ++p) *p = PAINT_WORD;
    probe_run(m, g_vm_stored[0], &cls);
    volatile uint32_t *q = bot;
    while (q < top && *q == PAINT_WORD) ++q;
    printf("S %d %lu\n", k, (unsigned long)((top - q) * 4));
    for (int i = 0; i < VM_N_TV; ++i) {
      frontend(m->rep, g_vm_tv[i], s_feat);
      m->infer(s_feat, &cls);
      int n = m->outputs(s_out, 16);
      char head[40];
      snprintf(head, sizeof head, "V %d %d %d ", k, i, cls);
      print_vec(head, s_out, n);
    }
  }
  printf("VB done\n");
  return 0;
}
