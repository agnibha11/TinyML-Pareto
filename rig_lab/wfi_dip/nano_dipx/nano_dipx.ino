// nano_dipx.ino - experiment sketch for the ~42 s idle-current dip (firmware/experiments/wfi_dip/README.md).
// NOT the bench firmware: flash firmware/bench back afterwards.
//
// Same idle conditions as firmware/bench (LEDs, sensor rail and I2C pull-ups off, D2 LOW at rest, HFXO on), plus:
//   INFO            -> clock and USB state
//   WIN <ms> <flags> -> D2 HIGH for <ms>, sleeping, then D2 LOW. Flags (bit mask):
//        1  USB off during the window: pull-up released + USBD disabled; the board resets at the end
//        2  HFXO off during the window (HFCLK falls back to HFINT; only meaningful with flag 1)
//        4  sleep with rtos::ThisThread::sleep_for() instead of a WFI loop
//        8  LFCLK switched to LFSYNT (derived from HFXO) for the window
//   Replies "OK us=<n>" after the window (no reply when flag 1 is set: the board resets instead).
#include <Arduino.h>
#include <mbed.h>
#include <chrono>

static const int kSync = 2;

static void hfxo_start() {
  NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
  NRF_CLOCK->TASKS_HFCLKSTART = 1;
  while (NRF_CLOCK->EVENTS_HFCLKSTARTED == 0) {}
  NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
}

static void lfclk_set(uint32_t src) {  // 0 RC, 1 Xtal, 2 Synth
  NRF_CLOCK->TASKS_LFCLKSTOP = 1;
  while (NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_STATE_Msk) {}
  NRF_CLOCK->LFCLKSRC = src;
  NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
  NRF_CLOCK->TASKS_LFCLKSTART = 1;
  while (NRF_CLOCK->EVENTS_LFCLKSTARTED == 0) {}
  NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
}

void setup() {
#ifdef LED_PWR
  pinMode(LED_PWR, OUTPUT); digitalWrite(LED_PWR, LOW);
#endif
#ifdef PIN_ENABLE_SENSORS_3V3
  pinMode(PIN_ENABLE_SENSORS_3V3, OUTPUT); digitalWrite(PIN_ENABLE_SENSORS_3V3, LOW);
#endif
#ifdef PIN_ENABLE_I2C_PULLUP
  pinMode(PIN_ENABLE_I2C_PULLUP, OUTPUT); digitalWrite(PIN_ENABLE_I2C_PULLUP, LOW);
#endif
  pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, LOW);
  pinMode(kSync, OUTPUT); digitalWrite(kSync, LOW);
  hfxo_start();
  Serial.begin(115200);
}

static void info() {
  const uint32_t hs = NRF_CLOCK->HFCLKSTAT, ls = NRF_CLOCK->LFCLKSTAT;
  Serial.print("OK dipx hfclk_src="); Serial.print((hs & CLOCK_HFCLKSTAT_SRC_Msk) ? "xtal" : "rc");
  Serial.print(" lfclk_src="); Serial.print(ls & CLOCK_LFCLKSTAT_SRC_Msk);          // 0 RC, 1 Xtal, 2 Synth
  Serial.print(" lfclk_running="); Serial.print((ls & CLOCK_LFCLKSTAT_STATE_Msk) ? 1 : 0);
  Serial.print(" lfclksrc_reg="); Serial.print(NRF_CLOCK->LFCLKSRC & 3);
  Serial.print(" usbd_enable="); Serial.print(NRF_USBD->ENABLE);
  Serial.print(" dcdcen="); Serial.print(NRF_POWER->DCDCEN);
  Serial.print(" dcdcen0="); Serial.println(NRF_POWER->DCDCEN0);
}

static void win(uint32_t ms, uint32_t flags) {
  delay(50);                                   // let the USB stack finish anything pending
  const uint32_t lf_before = NRF_CLOCK->LFCLKSRC & 3;
  if (flags & 1) {
    NRF_USBD->USBPULLUP = 0;                   // host sees a disconnect: no more SOF/polling
    delay(5);
    NVIC_DisableIRQ(USBD_IRQn);
    NRF_USBD->ENABLE = 0;
    if (flags & 2) NRF_CLOCK->TASKS_HFCLKSTOP = 1;
  }
  if (flags & 8) lfclk_set(2);
  const uint32_t t0 = micros();
  digitalWrite(kSync, HIGH);
  if (flags & 4) {
    rtos::ThisThread::sleep_for(std::chrono::milliseconds(ms));
  } else {
    const uint32_t span = ms * 1000u;
    while ((uint32_t)(micros() - t0) < span) __WFI();
  }
  digitalWrite(kSync, LOW);
  const uint32_t t1 = micros();
  if (flags & 8) lfclk_set(lf_before);
  if (flags & 1) NVIC_SystemReset();           // cleanest way to bring USB back
  Serial.print("OK us="); Serial.println(t1 - t0);
}

void loop() {
  static char line[48]; static size_t n = 0;
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (n < sizeof line - 1) line[n++] = c; continue; }
    line[n] = 0; n = 0;
    unsigned long ms = 0, fl = 0;
    if (!strcmp(line, "INFO")) info();
    else if (sscanf(line, "WIN %lu %lu", &ms, &fl) >= 1 && ms >= 1 && ms <= 600000) win(ms, fl);
    else Serial.println("ERR");
  }
}
