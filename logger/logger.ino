// logger.ino - ESP32 energy logger for the Nano 33 BLE (CONTRACT.md 7.3, 7.4). Owner: Hridayesh (measurement).
//
// Wiring (7.3): INA219 on I2C SDA 21 / SCL 22 at 400 kHz; D2 sync from the Nano on GPIO 4 (INPUT_PULLDOWN plus an
// external 100 kOhm to GND); all grounds tied. The INA219 measures the Nano's 5 V USB line (VIN+ PC side, VIN- Nano).
//
// A timer task on core 0 reads the shunt and bus registers every SAMPLE_PERIOD_US into a ring buffer; the sync ISR
// time-stamps D2 edges into an edge queue; loop() merges both in time order through logger_core and prints one W line
// per window of >= 100 ms. Nothing else this sketch prints starts with "W ".
//
// Status: logic host-tested (logger/tests, pytest tests/test_logger_core.py); sketch not yet compiled for the ESP32 -
// check on first bring-up (arduino-cli compile --fqbn esp32:esp32:esp32 logger) and pin the core version in
// firmware/VERSIONS.md.
#include <Arduino.h>
#include <Wire.h>

#include "esp_timer.h"
#include "logger_core.h"

#ifndef LOGGER_GIT_HASH
#define LOGGER_GIT_HASH "dev"
#endif
#ifndef SHUNT_OHM_MEASURED
#define SHUNT_OHM_MEASURED 1.000   // set to the multimeter value of the fitted shunt (session_log.csv)
#endif

static const int PIN_SYNC = 4, PIN_SDA = 21, PIN_SCL = 22;
static const uint8_t INA219_ADDR = 0x40;
static const uint32_t SAMPLE_PERIOD_US = 1000;           // ~1 kHz; one 12-bit shunt+bus pair takes ~1.06 ms
static const uint64_t MIN_WINDOW_US = 100000;            // 7.3 glitch filter
// INA219 config: BRNG 16 V, PGA /1 (+-40 mV), BADC 12-bit, SADC 12-bit, shunt+bus continuous.
static const uint16_t INA219_CONFIG = (0u << 13) | (0u << 11) | (0x3u << 7) | (0x3u << 3) | 0x7u;

// --- ring buffer of samples (producer: sampler task; consumer: loop) -------------------------------------------
static const uint32_t RING = 4096;
static logger_sample_t s_ring[RING];
static volatile uint32_t s_head = 0, s_tail = 0, s_dropped = 0;
// --- edge queue (producer: ISR) ----------------------------------------------------------------------------------
static const uint32_t EQ = 32;
static volatile uint64_t s_edge_t[EQ];
static volatile uint8_t s_edge_lvl[EQ];
static volatile uint32_t s_ehead = 0, s_etail = 0;

static logger_window_t s_win;

static bool ina_write(uint8_t reg, uint16_t v) {
  Wire.beginTransmission(INA219_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(v >> 8));
  Wire.write((uint8_t)v);
  return Wire.endTransmission() == 0;
}

static bool ina_read(uint8_t reg, uint16_t *v) {
  Wire.beginTransmission(INA219_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)INA219_ADDR, 2) != 2) return false;
  *v = ((uint16_t)Wire.read() << 8) | Wire.read();
  return true;
}

static void IRAM_ATTR on_sync_edge() {
  const uint32_t h = s_ehead;
  if (h - s_etail < EQ) {
    s_edge_t[h % EQ] = esp_timer_get_time();
    s_edge_lvl[h % EQ] = digitalRead(PIN_SYNC);
    s_ehead = h + 1;
  }
}

static void sampler_task(void *) {
  uint64_t next = esp_timer_get_time();
  for (;;) {
    next += SAMPLE_PERIOD_US;
    uint16_t shunt = 0, bus = 0;
    const uint64_t t = esp_timer_get_time();
    if (ina_read(0x01, &shunt) && ina_read(0x02, &bus)) {
      const uint32_t h = s_head;
      if (h - s_tail < RING) {
        s_ring[h % RING] = logger_sample_t{t, (int16_t)shunt, bus};
        s_head = h + 1;
      } else {
        s_dropped = s_dropped + 1;
      }
    }
    while ((int64_t)(next - esp_timer_get_time()) > 0) { /* busy-wait to the next slot */ }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_SYNC, INPUT_PULLDOWN);
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  ina_write(0x00, INA219_CONFIG);
  logger_init(&s_win, SHUNT_OHM_MEASURED, MIN_WINDOW_US);
  attachInterrupt(digitalPinToInterrupt(PIN_SYNC), on_sync_edge, CHANGE);
  xTaskCreatePinnedToCore(sampler_task, "ina219", 4096, nullptr, 5, nullptr, 0);
  Serial.printf("# logger %s shunt_ohm=%.4f period_us=%lu\n", LOGGER_GIT_HASH, SHUNT_OHM_MEASURED,
                (unsigned long)SAMPLE_PERIOD_US);
}

void loop() {
  static char line[200];
  // Merge edges and samples in time order.
  for (;;) {
    const bool have_s = s_tail != s_head, have_e = s_etail != s_ehead;
    if (!have_s && !have_e) break;
    const uint64_t te = have_e ? s_edge_t[s_etail % EQ] : UINT64_MAX;
    const uint64_t ts = have_s ? s_ring[s_tail % RING].t_us : UINT64_MAX;
    if (have_e && te <= ts) {
      const uint8_t lvl = s_edge_lvl[s_etail % EQ];
      s_etail = s_etail + 1;
      if (lvl) {
        logger_rise(&s_win, te);
      } else if (logger_fall(&s_win, te, line, sizeof line) > 0) {
        Serial.println(line);                       // 7.4: printed within 500 ms of the falling edge
      }
    } else {
      logger_sample(&s_win, (const logger_sample_t *)&s_ring[s_tail % RING]);
      s_tail = s_tail + 1;
    }
  }
  const uint32_t d = s_dropped;
  if (d) {
    s_dropped = 0;
    logger_dropped(&s_win, d);
  }
}
