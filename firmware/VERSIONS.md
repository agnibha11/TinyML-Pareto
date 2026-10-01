# firmware/VERSIONS.md — pinned toolchain (reproducibility appendix)

Every number in the paper that is bytes or microseconds was measured with exactly these versions. Do not accept an
IDE or `arduino-cli` update prompt during the study. Changing any line below means re-measuring every model.

## Board and core

| Item | Value | Notes |
|---|---|---|
| Board | Arduino Nano 33 BLE (nRF52840, Cortex-M4F) | two boards: one measured, one untouched spare |
| FQBN | `arduino:mbed_nano:nano33ble` | |
| Core | `arduino:mbed_nano@4.6.0` | Mbed OS-based. Do **not** install or mix in the Zephyr core |
| CPU clock | 64 MHz (default) | never changed |
| Regulator | default (LDO; nRF52840 DC/DC left off by the Mbed config) | fixed condition, stated in the paper |
| Optimization | `-Os` (the variant's default) | not changed; stated in the paper |
| arduino-cli | `<fill in at W1 install: arduino-cli version>` | Abhinav, before M1 |
| arm-none-eabi-gcc | `<fill in at W1 install: version the core installed>` | `~/.arduino15/packages/arduino/tools/arm-none-eabi-gcc/*/bin/` |

## Libraries

| Library | Version | Used for |
|---|---|---|
| Chirale_TensorFlowLite | 2.0.0 | TFLM runtime + CMSIS-NN kernels (main build, runtime `tflm-cmsis`) |
| Arduino_CMSIS-DSP | 5.7.0 | `arm_rfft_fast_f32` etc. for the R2/R3 front end (default branch has only a build script, so pin 5.7.0) |
| emlearn (C headers) | 0.23.2 | tree/forest inference (runtime `emlearn`); **same version as Python** (`requirements.txt`) |
| TFLM reference-kernel tree | commit `<fill in by W4 (gate M4)>` of tensorflow/tflite-micro | ablation build, runtime `tflm-ref`, packaged as library `TFLM_REF` |

## Install (exact commands)

```bash
arduino-cli core update-index
arduino-cli core install arduino:mbed_nano@4.6.0
arduino-cli lib install "Chirale_TensorFlowLite@2.0.0"
arduino-cli lib install "Arduino_CMSIS-DSP@5.7.0"
python -m emlearn.arduino.install          # copies emlearn C headers into the Arduino libraries folder (from the venv)

arduino-cli core list                      # must show arduino:mbed_nano 4.6.0
arduino-cli lib list                       # must show the two libraries at the versions above
```

Smoke test for the toolchain:
```bash
arduino-cli compile --fqbn arduino:mbed_nano:nano33ble firmware/bench
arduino-cli upload  --fqbn arduino:mbed_nano:nano33ble -p /dev/ttyACM0 firmware/bench
python tools/dut.py /dev/ttyACM0 PING      # -> OK bench=dev n_models=1
```

## Linux host note

ModemManager may probe `/dev/ttyACM*` with AT commands when the Nano enumerates. Those bytes would reach the bench
firmware as garbage commands. Either stop the service (`sudo systemctl stop ModemManager`) or add a udev rule:
```
# /etc/udev/rules.d/99-nano33ble.rules   (Arduino VID 2341)
ATTRS{idVendor}=="2341", ENV{ID_MM_DEVICE_IGNORE}="1"
```
Then run `sudo udevadm control --reload && sudo udevadm trigger`. Also add yourself to the `dialout` group.

## Facts checked on the host side (1 Oct 2026)

- emlearn 0.23.2, `convert(est, method='inline', dtype='float')` generates
  `int32_t <name>_predict(const float *features, int32_t features_length)`. Bundles must reject any tree header whose
  predict function does not take `const float *` (the int16 default would silently mis-classify).
- A CNN written as Conv2D (k×1) over a (1024, 1, 1) input, converted as full int8 (PTQ or QAT, TF 2.18.1), uses these
  TFLite ops: `CONV_2D, FULLY_CONNECTED, MAX_POOL_2D, MEAN, SOFTMAX`. Register exactly the union list in
  `handoff/ops_union.txt` in `MicroMutableOpResolver`; never use `AllOpsResolver`.

## Build stamping

`tools/build_flash.py` passes `-DBENCH_GIT_HASH=\"<short hash>\"`, and for bundles `-DBUNDLE_ID=<b> -DARENA_BYTES=<n>`,
through `--build-property "compiler.cpp.extra_flags=..."`. `PING` then reports the hash, and `measure.py` stores it in
every row (`fw_hash`).

## Change log

| Date | Change | Who | Re-measured? |
|---|---|---|---|
| 2026-10-01 | Initial pins | | n/a |
