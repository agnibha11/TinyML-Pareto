// tflm_glue.cpp - see tflm_glue.h. Builds against TFLM as packaged by Chirale_TensorFlowLite 2.0.0 (the board) and
// against current tflite-micro (virtual MCU, PC simulator).
#if !defined(ARDUINO) || (defined(BUNDLE_ID) && defined(BUNDLE_TFLM) && BUNDLE_TFLM)

#ifdef ARDUINO
#include <Chirale_TensorFlowLite.h>
#endif
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <new>

#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#if __has_include("tensorflow/lite/micro/schema/schema_generated.h")
#include "tensorflow/lite/micro/schema/schema_generated.h"
#else
#include "tensorflow/lite/schema/schema_generated.h"
#endif
#include "tflm_glue.h"

#ifndef ARENA_BYTES
#define ARENA_BYTES (768 * 1024)
#endif
#ifndef BENCH_ARENA_ATTR
#define BENCH_ARENA_ATTR
#endif

alignas(16) static uint8_t s_arena[ARENA_BYTES] BENCH_ARENA_ATTR;
alignas(tflite::MicroInterpreter) static uint8_t s_interp_mem[sizeof(tflite::MicroInterpreter)];
static tflite::MicroInterpreter *s_interp = nullptr;
typedef tflite::MicroMutableOpResolver<5> Resolver;
alignas(Resolver) static uint8_t s_resolver_mem[sizeof(Resolver)];
static Resolver *s_resolver = nullptr;
static TfLiteTensor *s_in = nullptr, *s_out = nullptr;

#ifndef ARDUINO
// Current TFLM logs through DebugLog(format, va_list); send it to stdout, prefixed (semihosting / PC).
extern "C" void DebugLog(const char *format, va_list args) {
  printf("TFLM: ");
  vprintf(format, args);
}
extern "C" int DebugVsnprintf(char *buffer, size_t buf_size, const char *format, va_list vlist) {
  return vsnprintf(buffer, buf_size, format, vlist);
}
#endif

static Resolver &resolver() {
  if (!s_resolver) {
    s_resolver = new (s_resolver_mem) Resolver();
    s_resolver->AddConv2D();
    s_resolver->AddFullyConnected();
    s_resolver->AddMaxPool2D();
    s_resolver->AddMean();
    s_resolver->AddSoftmax();
  }
  return *s_resolver;
}

static int n_elems(const TfLiteTensor *t) { return (int)(t->bytes / (t->type == kTfLiteInt8 ? 1 : 4)); }

extern "C" int bench_tflm_setup(const unsigned char *model, uint32_t *arena_used) {
  if (s_interp) {
    s_interp->~MicroInterpreter();
    s_interp = nullptr;
  }
  const tflite::Model *m = tflite::GetModel(model);
  s_interp = new (s_interp_mem) tflite::MicroInterpreter(m, resolver(), s_arena, ARENA_BYTES);
  if (s_interp->AllocateTensors() != kTfLiteOk) return -1;
  s_in = s_interp->input(0);
  s_out = s_interp->output(0);
  *arena_used = (uint32_t)s_interp->arena_used_bytes();
  return 0;
}

extern "C" int bench_tflm_infer(const float *f, int *cls) {
  const int n = n_elems(s_in);
  if (s_in->type == kTfLiteInt8) {
    const float inv = 1.0f / s_in->params.scale;
    const int zp = s_in->params.zero_point;
    int8_t *q = s_in->data.int8;
    for (int i = 0; i < n; ++i) {
      const int v = (int)lroundf(f[i] * inv) + zp;
      q[i] = (int8_t)(v < -128 ? -128 : (v > 127 ? 127 : v));
    }
  } else {
    memcpy(s_in->data.f, f, (size_t)n * 4);
  }
  if (s_interp->Invoke() != kTfLiteOk) return -1;
  const int no = n_elems(s_out);
  int best = 0;
  if (s_out->type == kTfLiteInt8) {
    for (int i = 1; i < no; ++i) if (s_out->data.int8[i] > s_out->data.int8[best]) best = i;
  } else {
    for (int i = 1; i < no; ++i) if (s_out->data.f[i] > s_out->data.f[best]) best = i;
  }
  *cls = best;
  return 0;
}

extern "C" int bench_tflm_outputs(float *out, int max_n) {
  const int no = n_elems(s_out);
  const int n = no < max_n ? no : max_n;
  for (int i = 0; i < n; ++i)
    out[i] = s_out->type == kTfLiteInt8 ? (s_out->data.int8[i] - s_out->params.zero_point) * s_out->params.scale
                                        : s_out->data.f[i];
  return n;
}

#endif
