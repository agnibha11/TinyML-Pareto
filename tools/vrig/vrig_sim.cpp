// vrig_sim.cpp - virtual INA219 + ESP32 logger: synthetic board-current traces, quantised exactly as the INA219
// registers, integrated by the real logger core (logger/logger_core.cpp). One CONTRACT.md 7.4 W line per window.
//
// stdin: one window per line, whitespace-separated:
//   dur_us seed shunt_ohm vbus_v i_base_a i_active_a noise_a drift_frac offset_uv gain_err period_us jitter_us
//     dur_us      window length (D2 HIGH time), microseconds
//     i_base_a    board current with the CPU in the WFI idle loop (amps, through the shunt)
//     i_active_a  extra current while the CPU executes the measured loop (0 for IDLE windows)
//     noise_a     per-sample current noise (std, amps): USB, regulator ripple
//     drift_frac  linear drift of the base current across the window (fraction of i_base, start -> end)
//     offset_uv   INA219 shunt offset voltage for this session (uV), gain_err = INA219 gain error (fraction)
//     period_us   logger sampling period (532 us shunt + 532 us bus conversion at 12 bit = 1064 us), jitter_us
// stdout: the W line of each window, in order.
// Build: g++ -O2 -std=c++17 -Ilogger logger/logger_core.cpp tools/vrig/vrig_sim.cpp -o build/vrig_sim
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <random>

#include "logger_core.h"

int main() {
  double dur_us, shunt, vbus, ib, ia, noise, drift, off_uv, gain, period, jitter;
  unsigned long long seed;
  char line[256];
  while (scanf("%lf %llu %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf", &dur_us, &seed, &shunt, &vbus, &ib, &ia, &noise,
               &drift, &off_uv, &gain, &period, &jitter) == 12) {
    std::mt19937_64 rng(seed);
    std::normal_distribution<double> n01(0.0, 1.0);
    std::uniform_real_distribution<double> u01(0.0, 1.0);
    logger_window_t w;
    logger_init(&w, shunt, 100000);
    const uint64_t t0 = 1000000;
    logger_rise(&w, t0);
    double t = t0 + period * u01(rng);                    // the logger's sample clock is not aligned with D2
    while (t <= t0 + dur_us) {
      const double frac = (t - t0) / dur_us;
      const double i = ib * (1.0 + drift * frac) + ia + noise * n01(rng);
      const double vshunt_uv = i * shunt * 1e6 * (1.0 + gain) + off_uv;
      const double vb_mv = (vbus - i * shunt) * 1e3;      // bus voltage is read on the load side of the shunt
      logger_sample_t s;
      s.t_us = (uint64_t)t;
      double q = std::nearbyint(vshunt_uv / INA219_SHUNT_LSB_UV);
      if (q > 32767) q = 32767;
      if (q < -32768) q = -32768;
      s.shunt_raw = (int16_t)q;
      const int ovf = std::fabs(vshunt_uv) > 40000.0;       // PGA /1: +-40 mV full scale
      s.bus_raw = (uint16_t)(((uint16_t)std::nearbyint(vb_mv / INA219_BUS_LSB_MV)) << 3) | (ovf ? 1u : 0u);
      logger_sample(&w, &s);
      t += period + jitter * (2.0 * u01(rng) - 1.0);
    }
    if (logger_fall(&w, t0 + (uint64_t)dur_us, line, sizeof line) > 0) printf("%s\n", line);
    else printf("# no line\n");
  }
  return 0;
}
