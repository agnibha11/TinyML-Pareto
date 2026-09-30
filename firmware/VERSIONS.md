# Firmware Versions and Toolchain

To ensure reproducibility of all latency and memory metrics, the following versions are strictly pinned for this study:

* **Arduino Core:** `arduino:mbed_nano` version **4.6.0** (Do not use the Zephyr core)
* **TensorFlow Lite Micro:** `Chirale_TensorFlowLite` version **2.0.0**
* **CMSIS-DSP:** `Arduino_CMSIS-DSP` version **5.7.0**
* **emlearn:** Version **0.23.x** (Must match the host Python environment)
* **Compiler Flags:** `-Os` (Size-optimized, default for this Nano variant)
* **Regulator Mode:** Default (LDO) - Mbed DC/DC is left off
* **CPU Clock:** 64 MHz

*Note: Any updates to these libraries invalidate prior latency measurements.*