// hal_nano33ble.cpp - Arduino Nano 33 BLE (arduino:mbed_nano@4.6.0) implementation of hal.h.
#ifdef ARDUINO

#include <Arduino.h>
#include <mbed.h>
#include <string.h>

#include <chrono>

#include "hal.h"

static const int kSyncPin = 2;  // D2: LOW at rest, HIGH only around the measured loop (CONTRACT.md 7.3)

void bhal_init(void) {
  // The core's initVariant() turns these on at boot; the benchmark needs none of them.
#ifdef LED_PWR
  pinMode(LED_PWR, OUTPUT);
  digitalWrite(LED_PWR, LOW);
#endif
#ifdef PIN_ENABLE_SENSORS_3V3
  pinMode(PIN_ENABLE_SENSORS_3V3, OUTPUT);
  digitalWrite(PIN_ENABLE_SENSORS_3V3, LOW);
#endif
#ifdef PIN_ENABLE_I2C_PULLUP
  pinMode(PIN_ENABLE_I2C_PULLUP, OUTPUT);
  digitalWrite(PIN_ENABLE_I2C_PULLUP, LOW);
#endif
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  // RGB LED pins (LEDR/LEDG/LEDB) are active-low and left as inputs: off.
  // Never call BLE.begin() or touch the IMU. CPU stays at 64 MHz, regulator mode untouched.

  pinMode(kSyncPin, OUTPUT);
  digitalWrite(kSyncPin, LOW);

  // DWT cycle counter for latency.
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  Serial.begin(115200);  // USB CDC: the baud rate is nominal
  while (!Serial) {
  }  // wait for the host to open the port; nothing is printed until a command arrives
}

uint32_t bhal_micros(void) { return micros(); }

uint32_t bhal_cycles(void) { return DWT->CYCCNT; }

void bhal_sync(int high) { digitalWrite(kSyncPin, high ? HIGH : LOW); }

void bhal_wait_for_interrupt(void) { __WFI(); }

void bhal_sleep_ms(uint32_t ms) { rtos::ThisThread::sleep_for(std::chrono::milliseconds(ms)); }

int bhal_read_byte(void) { return Serial.available() > 0 ? Serial.read() : -1; }

size_t bhal_read_bytes(uint8_t *buf, size_t n, uint32_t timeout_ms) {
  Serial.setTimeout(timeout_ms);
  return Serial.readBytes(reinterpret_cast<char *>(buf), n);
}

void bhal_write(const char *s) { Serial.write(reinterpret_cast<const uint8_t *>(s), strlen(s)); }

#endif  // ARDUINO
