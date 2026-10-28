// core_sim.cpp - drives logger_core with synthetic INA219 traces; prints the W lines (and "#" notes) on stdout.
// Used by tests/test_logger_core.py, which checks every line with tools/measure.py's parser and against the
// analytic energy of each scenario.
//   ./core_sim <scenario>    scenario = const | step | glitch | ovf | drop
#include <stdio.h>
#include <string.h>

#include "logger_core.h"

static logger_sample_t mk(uint64_t t, double vshunt_uv, double vbus_mv, int ovf) {
  logger_sample_t s;
  s.t_us = t;
  s.shunt_raw = (int16_t)(vshunt_uv / INA219_SHUNT_LSB_UV + (vshunt_uv >= 0 ? 0.5 : -0.5));
  s.bus_raw = (uint16_t)(((uint16_t)(vbus_mv / INA219_BUS_LSB_MV + 0.5)) << 3) | (ovf ? 1u : 0u);
  return s;
}

static void feed(logger_window_t *w, logger_sample_t s) { logger_sample(w, &s); }

static void emit(logger_window_t *w, uint64_t t_fall) {
  char line[200];
  if (logger_fall(w, t_fall, line, sizeof line) > 0) printf("%s\n", line);
  else printf("# no line\n");
}

int main(int argc, char **argv) {
  const char *sc = argc > 1 ? argv[1] : "const";
  logger_window_t w;
  logger_init(&w, 1.0, 100000);
  const uint64_t t0 = 1000000, period = 1000;
  if (!strcmp(sc, "const") || !strcmp(sc, "ovf") || !strcmp(sc, "drop")) {
    // 20 mA through 1 Ohm (20 mV) at 5.000 V for 10 s, one sample per ms -> P = 100 mW
    logger_rise(&w, t0);
    for (uint64_t t = t0 + period; t <= t0 + 10000000; t += period)
      feed(&w, mk(t, 20000.0, 5000.0, !strcmp(sc, "ovf") && t == t0 + 5000 * period));
    if (!strcmp(sc, "drop")) logger_dropped(&w, 3);
    emit(&w, t0 + 10000000);
  } else if (!strcmp(sc, "step")) {
    // 10 mA for the first 5 s, 30 mA for the next 5 s, at 4.900 V -> P = 49 mW then 147 mW
    logger_rise(&w, t0);
    for (uint64_t t = t0 + period; t <= t0 + 10000000; t += period)
      feed(&w, mk(t, t <= t0 + 5000000 ? 10000.0 : 30000.0, 4900.0, 0));
    emit(&w, t0 + 10000000);
  } else if (!strcmp(sc, "glitch")) {
    // a 50 ms HIGH pulse (e.g. D2 floating during a Nano reset) must not produce a line
    logger_rise(&w, t0);
    for (uint64_t t = t0 + period; t <= t0 + 50000; t += period) feed(&w, mk(t, 20000.0, 5000.0, 0));
    emit(&w, t0 + 50000);
  }
  return 0;
}
