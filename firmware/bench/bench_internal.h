// bench_internal.h - what bench_core.cpp shares with bench_cascade.cpp. Not part of the model interface.
#pragma once
#include <stdint.h>

#include "bench_core.h"

void bench_reply(const char *fmt, ...);        // one line + "\r\n"
const bench_model_t *bench_selected(void);     // the SEL model, or 0 after replying ERR NOSEL
int bench_selected_index(void);                // -1 = none
int bench_input_index(void);                   // stored window of INPUT <j>
void bench_drain_input(void);                  // after a short binary read (VERIFY rules)

// CONTRACT.md 7.6. Cascade bundles (-DBENCH_CASCADE) implement these; every other build replies ERR NOTIMPL.
void bench_cascade_setup(void);
void bench_cascade_command(const char *cmd, int nargs, const uint32_t *args);  // args already parsed as u32
