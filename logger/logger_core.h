// logger_core.h - window integrator of the ESP32 INA219 energy logger (CONTRACT.md 7.3, 7.4). No Arduino code
// here, so the same file is compiled into the sketch (logger.ino) and into the PC tests (logger/tests/).
//
// Feed it, in time order: rising/falling edges of the D2 sync line and raw INA219 samples. At every falling edge
// that closes a window of at least min_window_us it formats exactly one CONTRACT.md 7.4 line:
//   W rise_us=<> fall_us=<> n=<> E_uJ=<> Pmean_uW=<> Pstd_uW=<> Vbus_mV=<> ovf=<>
// Definitions (7.4): samples i = 1..n inside [rise, fall]; P_i = V_bus,i * V_shunt,i / R_shunt;
// E = sum P_i * dt_i with dt_i measured from the previous sample (from rise for the first one);
// Pmean = E / (fall - rise); Pstd = population std of P_i; Vbus = mean bus voltage; ovf = samples with the INA219
// math-overflow flag + samples dropped by the ring buffer while the window was open.
#pragma once
#include <stddef.h>
#include <stdint.h>

// INA219 register scaling (TI datasheet): shunt voltage LSB 10 uV (signed); bus voltage = (reg >> 3) * 4 mV;
// bus register bit 0 = OVF (math overflow), bit 1 = CNVR.
#define INA219_SHUNT_LSB_UV 10.0
#define INA219_BUS_LSB_MV 4.0

typedef struct {
  uint64_t t_us;       // esp_timer_get_time() when the sample was read
  int16_t shunt_raw;   // shunt-voltage register (0x01)
  uint16_t bus_raw;    // bus-voltage register (0x02), flags included
} logger_sample_t;

typedef struct {
  // configuration
  double shunt_ohm;           // measured shunt resistance (session_log.csv: shunt_ohm_measured)
  uint64_t min_window_us;     // glitch filter: shorter HIGH pulses produce no line (7.3: 100 ms)
  // state
  int open;
  uint64_t rise_us, last_t_us;
  uint32_t n, ovf;
  double e_uj;                // sum P_i dt_i
  double p_mean_acc, p_m2;    // Welford mean / M2 of P_i (uW)
  double vbus_acc_mv;
} logger_window_t;

void logger_init(logger_window_t *w, double shunt_ohm, uint64_t min_window_us);
void logger_rise(logger_window_t *w, uint64_t t_us);
void logger_sample(logger_window_t *w, const logger_sample_t *s);     // ignored when no window is open
void logger_dropped(logger_window_t *w, uint32_t n_dropped);         // ring-buffer overruns
// Returns the number of characters written to line (0 = no line: no open window, or a glitch shorter than
// min_window_us). The line has no trailing newline.
int logger_fall(logger_window_t *w, uint64_t t_us, char *line, size_t cap);

// Power of one sample in microwatts (exposed for tests).
double logger_power_uw(const logger_window_t *w, const logger_sample_t *s);
