# Supporting papers (checked facts)

## Zhang, Wijerathne, Li, Mitra (2022). Power-Performance Characterization of TinyML Systems
2022 IEEE 40th ICCD, Olympic Valley, pp. 644–651 · DOI 10.1109/ICCD56317.2022.00099 · arXiv 2608.21646 ·
key `zhang2022power`

- **MCUs:** 4 in total, including the **Arduino Nano 33 BLE Sense** ("MCU-S", Cortex-M4F, 64 MHz).
- **Energy method:** SmartPower2 average power × inference time. The SparkFun Edge could not be measured.
- **MicroSpeech results** (these are on the **STM32L476RG**, not the Nano):
  - pure-C bare metal: 488.3 ms / 54.2 mJ
  - CMSIS-NN bare metal: 46.2 ms / 5.1 mJ (≈ 10.6×)
  - TFLM + CMSIS-NN + Mbed OS: 48.9 ms / 9.5 mJ
- **Mbed OS overhead:** +98 % SRAM, +196 % flash, +65 % energy.
- **For us:** the "~10× from CMSIS-NN" figure is **against hand-written C, not TFLM reference kernels**, and on another
  M4 board. Our kernel ablation (TFLM-ref vs TFLM + CMSIS-NN on the Nano) is therefore new data, not a replication.

## Banbury et al. (2021). MLPerf Tiny Benchmark
NeurIPS Datasets & Benchmarks Track 1 · arXiv 2106.07597 (v4 lists 22 authors, the NeurIPS version 19; cite one
consistently) · key `banbury2021mlperf`

- **Timing:** "run inference for a minimum of 10 seconds and 10 iterations … report the median IPS of the five runs".
- **Energy:** measured over the same timing window as µJ/inference, median of 5 runs.
- **Rig:** the energy monitor (EMON) supplies and measures the DUT, and only core power is measured. A GPIO falling edge
  syncs the timer.
- **Scope:** pre- and post-processing are **excluded**.
- **For us:** our ≥ 10 s windows follow this. Our FRONT and E2E modes are deliberately outside MLPerf's scope. Our
  median over 5 stored inputs is our own choice, not MLPerf's.

## Gao, Jiang, Dong, Fu, Chen, Zhang (2025). An edge-deployable TinyML approach enhanced by transfer learning for efficient bearing fault diagnosis
*Science China Technological Sciences* 68, 2220401 · DOI 10.1007/s11431-025-3072-9 · key `gao2025edge`

- **Set-up:** ESP32-S3 DevKit-C1 (N8R16), a **proprietary** 4-class bearing dataset (plus SEU).
- **Results:** 88.28 %, 45 ms, 17.7 mJ.
- **Measurement:** the SI lists a Keithley 6510 for "power consumption test". The method text is paywalled and was not
  verified.

## El Boughardini, Jebari, Rekiek, Reklaoui (2026). Effective Classification of Bearing Vibration Signals Using Supervised Machine Learning for Predictive Maintenance: A Lightweight 1D CNN for Embedded Deployment
*Journal Européen des Systèmes Automatisés* 59(4), 1141–1160 · DOI 10.18280/jesa.590423 · key `elboughardini2026effective`

- **Hardware and model:** Teensy 4.1 (M7, 600 MHz); int8 QAT 1D-CNN; TFLM + CMSIS-NN.
- **Results:** 98.6 ± 0.3 % macro-F1; 4.7 ms (DWT); 90 kB flash / 42 kB RAM.
- **Energy:** 456 µJ = Joulescope JS220 average power (97.0 mW) × latency. It is not idle-subtracted (idle is 60.4 mW).
- **Data:**
  - Six public datasets named (the text says seven), harmonized to 3 classes (H/IR/OR).
  - Splits at the bearing/run level with 5-fold group-stratified CV.
  - Leave-one-dataset-out is described, but no numbers were found.
- **For us:** the closest leakage-aware deployment with measured energy. It still covers one family, has no frontier and
  runs on a Cortex-M7.
