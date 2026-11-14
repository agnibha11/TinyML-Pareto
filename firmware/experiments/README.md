# firmware/experiments — rig experiment sketches (not the benchmark)

Sketches used to investigate the energy rig (docs/energy_rig_bringup.md). They replace the normal firmware while
they run: **reflash `firmware/bench` on the Nano and `logger/` on the ESP32 afterwards.**

| Sketch | Board | What it does |
|---|---|---|
| `nano_dipx/` | Nano 33 BLE | bench-like idle conditions (LEDs, sensor rail, I2C pull-ups off, D2 LOW, HFXO on) plus `INFO` (clock/USB state) and `WIN <ms> <flags>`: D2 HIGH for `<ms>` while sleeping. Flags: 1 USB off (board resets after), 2 HFXO off (with 1), 4 RTOS sleep instead of WFI, 8 LFCLK from LFSYNT |
| `inastream/` | ESP32 NodeMCU-32S | prints `S <t_ms> <I_uA> <Vbus_mV> <sync%>` every 100 ms from the INA219 (same config as `logger/`), independent of sync windows |

`inastream` scales current with a fixed 0.100 Ω; multiply its currents by 1/1.139 for the calibrated value
(docs/energy_rig_bringup.md).

```bash
arduino-cli compile --fqbn arduino:mbed_nano:nano33ble firmware/experiments/nano_dipx
arduino-cli upload  --fqbn arduino:mbed_nano:nano33ble -p /dev/ttyACM0 firmware/experiments/nano_dipx
arduino-cli compile --fqbn esp32:esp32:nodemcu-32s firmware/experiments/inastream
arduino-cli upload  --fqbn esp32:esp32:nodemcu-32s -p /dev/ttyUSB0 firmware/experiments/inastream
python tools/run_dip.py --label info --info
python tools/run_dip.py --label usb_off --ms 130000 --flags 1     # -> results/rig/wfi_dip/ (local, git-ignored)
```
