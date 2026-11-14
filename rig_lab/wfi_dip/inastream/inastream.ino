// Streams INA219 averages every 100 ms: "S t_ms I_uA Vbus_mV sync" (shunt 0.100 ohm, same config as logger)
#include <Arduino.h>
#include <Wire.h>
static bool rd(uint8_t reg, uint16_t *v) {
  Wire.beginTransmission(0x40); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(0x40, 2) != 2) return false;
  *v = ((uint16_t)Wire.read() << 8) | Wire.read(); return true;
}
void setup() {
  Serial.begin(115200);
  pinMode(4, INPUT_PULLDOWN);
  Wire.begin(21, 22, 400000);
  Wire.beginTransmission(0x40); Wire.write(0x00);
  uint16_t cfg = (0x3u << 7) | (0x3u << 3) | 0x7u; Wire.write(cfg >> 8); Wire.write(cfg & 0xFF); Wire.endTransmission();
  Serial.println("# inastream");
}
void loop() {
  static uint32_t next = micros(), t_out = millis() + 100;
  static int64_t acc_s = 0, acc_b = 0; static uint32_t n = 0, hi = 0;
  next += 1000; uint16_t s, b;
  if (rd(0x01, &s) && rd(0x02, &b)) { acc_s += (int16_t)s; acc_b += (b >> 3); n++; hi += digitalRead(4); }
  if ((int32_t)(millis() - t_out) >= 0) {
    t_out += 100;
    if (n) Serial.printf("S %lu %.1f %.1f %lu\n", (unsigned long)millis(), acc_s * 10.0 / 0.100 / n, acc_b * 4.0 / n, (unsigned long)(hi * 100 / n));
    acc_s = acc_b = 0; n = hi = 0;
  }
  while ((int32_t)(next - micros()) > 0) {}
}
