// logger_core.cpp - see logger_core.h.
#include "logger_core.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

void logger_init(logger_window_t *w, double shunt_ohm, uint64_t min_window_us) {
  memset(w, 0, sizeof *w);
  w->shunt_ohm = shunt_ohm;
  w->min_window_us = min_window_us;
}

void logger_rise(logger_window_t *w, uint64_t t_us) {
  const double r = w->shunt_ohm;
  const uint64_t mw = w->min_window_us;
  logger_init(w, r, mw);           // a new rise always starts a fresh window
  w->open = 1;
  w->rise_us = t_us;
  w->last_t_us = t_us;
}

double logger_power_uw(const logger_window_t *w, const logger_sample_t *s) {
  const double vshunt_v = s->shunt_raw * INA219_SHUNT_LSB_UV * 1e-6;
  const double vbus_v = (double)(s->bus_raw >> 3) * INA219_BUS_LSB_MV * 1e-3;
  return vbus_v * (vshunt_v / w->shunt_ohm) * 1e6;
}

void logger_sample(logger_window_t *w, const logger_sample_t *s) {
  if (!w->open || s->t_us < w->rise_us) return;
  const double p = logger_power_uw(w, s);
  const double dt_s = (double)(s->t_us - w->last_t_us) * 1e-6;
  w->e_uj += p * dt_s;
  w->last_t_us = s->t_us;
  w->n += 1;
  const double d = p - w->p_mean_acc;            // Welford
  w->p_mean_acc += d / w->n;
  w->p_m2 += d * (p - w->p_mean_acc);
  w->vbus_acc_mv += (double)(s->bus_raw >> 3) * INA219_BUS_LSB_MV;
  if (s->bus_raw & 0x1u) w->ovf += 1;            // INA219 math overflow
}

void logger_dropped(logger_window_t *w, uint32_t n_dropped) {
  if (w->open) w->ovf += n_dropped;
}

int logger_fall(logger_window_t *w, uint64_t t_us, char *line, size_t cap) {
  if (!w->open) return 0;
  w->open = 0;
  if (t_us <= w->rise_us || t_us - w->rise_us < w->min_window_us) return 0;   // glitch filter (7.3)
  const double dur_s = (double)(t_us - w->rise_us) * 1e-6;
  const double pmean = w->e_uj / dur_s;
  const double pstd = w->n ? sqrt(w->p_m2 / w->n) : 0.0;
  const double vbus = w->n ? w->vbus_acc_mv / w->n : 0.0;
  int k = snprintf(line, cap,
                   "W rise_us=%" PRIu64 " fall_us=%" PRIu64 " n=%" PRIu32 " E_uJ=%.3f Pmean_uW=%.3f Pstd_uW=%.3f "
                   "Vbus_mV=%.1f ovf=%" PRIu32,
                   w->rise_us, t_us, w->n, w->e_uj, pmean, pstd, vbus, w->ovf);
  return (k > 0 && (size_t)k < cap) ? k : 0;
}
