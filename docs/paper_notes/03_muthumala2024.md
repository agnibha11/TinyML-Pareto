# Muthumala, Zhang, Martinez-Rau, Bader (2024). Comparison of Tiny Machine Learning Techniques for Embedded Acoustic Emission Analysis

2024 IEEE 10th World Forum on Internet of Things (WF-IoT), Ottawa, pp. 444–449 ·
DOI 10.1109/WF-IoT62078.2024.10811219 · arXiv 2411.17733 · BibTeX key `muthumala2024comparison`

**What was read.** The full arXiv v1 text.

## Set-up
- **Board:** Arduino Nano 33 BLE Sense (nRF52840, Cortex-M4). The same MCU as ours. Revision and clock are not stated.
- **Toolchain:** TFLM with int8 quantization.
  - Only the needed ops are registered.
  - FFT uses "the Arduino FFT library".
  - Features were written in C and checked against the Python (TSFEL) reference.
- **Data:** acoustic emission from a concrete block (Siracusano et al. 2021).
  - 3 balanced classes (tensile / shear / mixed), 15 000 events, down-sampled to 1000 points.
  - Split 70/15/15; the method (random or stratified) is not stated.
- **Models:** three MLPs.
  - Raw input: 1000→64→32→3.
  - 6 features: 6→64→96→3.
  - 8 features: 8→64→128→3.

## Energy is ESTIMATED
- Method: "Energy consumption is estimated based on datasheet values of the processor's power consumption." (Sec. II-E)
- Only execution time was measured.
- Energy / time is ≈ 10.98 mW for all three models, so the energies are exactly proportional to time.

## Results (Table II, on device)

| Model | Inference (µs) | Features (µs) | Flash (KB) | RAM (KB) | Energy (mJ) |
|---|---|---|---|---|---|
| Raw signal | 6 656 | — | 222.99 | 126.06 | 0.073 |
| 6 features | 767 | 471 937 | 176.93 | 64.93 | 5.19 |
| 8 features | 930 | 344 106 | 172.11 | 67.34 | 3.79 |

- Flash includes the libraries.
- Accuracy (Table I) is from float Keras (0.996 / 0.992 / 0.991), not measured on the device.
- "71x and 52x of the raw-signal model": 71× is the 6-feature model and 52× the 8-feature model.
- "the total processing time is dominated by the feature extraction". Dropping FFT features saves about 120 ms.

## Use in our paper
- This is the closest same-board precedent. Its finding that feature extraction dominates motivates our separate FRONT
  and E2E measurements.
- Our differences:
  - **measured** energy (INA219 on VBUS);
  - trees as well as NNs;
  - a CMSIS-DSP FFT;
  - on-device accuracy agreement;
  - bearing data under leakage-free splits.
