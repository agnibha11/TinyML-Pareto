// bench.ino - TinyML-Pareto benchmark sketch for the Arduino Nano 33 BLE.
//
// Build:  arduino-cli compile --fqbn arduino:mbed_nano:nano33ble firmware/bench
//         (tools/build_flash.py adds -DBENCH_GIT_HASH, -DBUNDLE_ID, -DARENA_BYTES)
// Talk:   python tools/dut.py /dev/ttyACM0 PING
//
// All logic lives in bench_core.cpp (protocol: CONTRACT.md section 7). The board-specific part is
// hal_nano33ble.cpp. The same core also compiles on a PC (firmware/tests/host_sim) for the host-side tests.
#include "bench_core.h"

void setup() { bench_setup(); }

void loop() { bench_poll(); }
