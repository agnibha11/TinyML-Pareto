# Related work: gap table (for "Background and Related Work")

Owner: Hridayesh · Status: **v1, checked against the papers (1 Oct 2026)**. Per-paper reading notes with quotes and
section numbers are in `docs/paper_notes/`. BibTeX is in `paper/refs.bib`. When a cell below says *second-hand* or
*unverified*, the notes explain why.

## The five core papers

Between them, these five cover our three contributions. Each one does part of what we do; none does all of it.

| # | Work | Task / data | Platform | Models | Energy | Split protocol | Frontier | What it leaves open (our angle) |
|---|---|---|---|---|---|---|---|---|
| 1 | **Hendriks, Dumond, Knox**, MSSP 169 (2022) 108732 | CWRU 12 kHz, **7 classes** (IR/OR/B at drive end and fan end + healthy), loads 1–3 HP | GPU (no MCU) | ACDIN, WDCNN (time/spectrum), ImageNet AlexNet/ResNet on spectrograms | none | Load-wise sets A/B/C vs **fault-size sets D/E/F** (0.007/0.014/0.021 in). Best model (ResNet): **95.0 % → 53.1 %** accuracy; mean of 6 set-ups 89.4 % → 44.1 % (≈ 45-point drop) | none | Accuracy only. Healthy data is still split by load, so it still leaks (Rosa 2024; Vieira 2026). No deployment cost, no model-family trade-off |
| 2 | **Vieira, Bauler, Rosa, Silva**, MSSP 258 (2026) 114640 | CWRU, Paderborn, UORED-VAFCLS, HUST; **multi-label**; metric **macro AUROC** | GPU | WDCNN (time/freq/envelope), RF and SVM on handcrafted features | none | Bearing-wise split (train and test bearings mutually exclusive), 100 runs. CWRU WDCNN: **≈ 100 % (leaky) → 63–64 %**. CWRU bearing-wise: **RF 85.1 ± 8.9 % vs WDCNN 74.5 ± 9.4 %** | none | RF beats the CNN on CWRU and HUST (not on PU or UORED): the ranking depends on the dataset. **No deployment, energy or memory.** We test whether the ranking flip survives on the MCU frontier |
| 3 | **Muthumala, Zhang, Martinez-Rau, Bader**, IEEE WF-IoT 2024, pp. 444–449 | Acoustic emission from concrete, 3 classes, random 70/15/15 | **Arduino Nano 33 BLE Sense (our MCU)**, TFLM int8 | 3 MLPs: raw 1000-sample input vs 6 or 8 handcrafted features | **Estimated**: "based on datasheet values of the processor's power consumption" (≈ 11 mW × measured time) | n/a (random) | none | Feature extraction is **52–71×** the raw model's time and energy (3.79 / 5.19 mJ vs 0.073 mJ). But energy is estimated, not measured, the models are NNs only, and accuracy is float Keras, not on-device. We **measure** front end and inference separately, for trees and NNs |
| 4 | **Jain, Kasper, Köber, Amft, Plinge, Seuß**, EEAI 2025 (arXiv 2602.17508) | MLPerf Tiny tasks + MNIST (images, machine sound) | Cortex-M0+/M4/M7 on one carrier board, 3.3 V | Pruned + int8 ResNet, LeNet-5, autoencoder, MobileNetV1 | **Measured** active energy (PPK, GPIO-synced, 5 repeats); idle from **datasheet** deep-sleep currents | n/a | Energy–accuracy fronts at 0.5 / 2.5 / 5 s cycles (visual) | Closest method precedent: the winner depends on the duty cycle (M7 at ≤ 0.5 s, M4 at ≥ 2.5 s). No trees, no feature-extraction energy, no vibration, no leakage, no hypervolume, no battery lifetime |
| 5 | **Bartoli et al.**, IEEE SENSORS 2025 (arXiv 2509.07051); method: **Bartoli et al.**, IJCNN 2025 (arXiv 2505.15622) | Keyword spotting (Google Speech Commands, 10 words); MLPerf Tiny models | STM32 N6 / H7 / U5 (a); STM32N6570-DK (b) | int8 NNs (DS-CNN, LiCoNet, TENet, their TKWS) | **Measured**: 50 mΩ shunt on the core rail + oscilloscope; 2 GPIOs encode 4 phases; 1000 repetitions; DUT on its own supply | n/a | EDP heatmaps | Measured **end-to-end** energy (MFCC + inference) exists for audio. In (b), pre-inference is 59–70 % of energy. Nothing for vibration, trees, leakage-safe splits or Pareto fronts |

## Supporting works (one line each in the paper)

| Work | What it shows | Gap relative to us |
|---|---|---|
| Banbury et al., MLPerf Tiny, NeurIPS D&B 2021 | µJ/inference over ≥ 10 s and ≥ 10 inferences, median of 5 runs. The energy monitor powers the DUT. **Pre/post-processing excluded** | Fixed reference models. Our window rules follow it; our end-to-end mode deliberately includes the front end |
| Zhang, Wijerathne, Li, Mitra, ICCD 2022 | 4 MCUs incl. the Nano 33 BLE Sense. MicroSpeech on **STM32L476RG**: pure-C bare metal 488 ms / 54.2 mJ vs CMSIS-NN 46.2 ms / 5.1 mJ. Mbed OS costs +98 % SRAM, +196 % flash, +65 % energy | NN only. The ~10× is CMSIS-NN vs hand-written C (not TFLM reference kernels) on a different M4 board. Our ablation measures TFLM-reference vs CMSIS-NN on the Nano |
| Tekin et al., *Internet of Things* 21 (2023) 100670 | DT / k-NN / RF / ANN energy on an ESP32 | Tabular intrusion detection; no front end, no leakage |
| Abburi et al., PHM 2023 | Bearing-shared splits overestimate (Naive Bayes 85.8 → 69.5 %, *second-hand* via Rosa 2024) | Accuracy only; healthy data split by load |
| Rosa, Braga, Silva, arXiv 2407.14625 (2024) | Multi-label, bearing-aware CWRU split; healthy data resampled 48 → 12 kHz | Server models, no deployment |
| Garavagno et al., BearingNAS, IEEE COINS 2026 | HW-NAS for 4–8 KiB RAM MCUs, 99.5 % on CWRU | Chronological split (not bearing-disjoint); latency from a cloud tool; no energy; no trees |
| Liao, arXiv 2304.09100 (2023) | CNN on STM32H743, 98.9 %, 19 ms | Leaky evaluation, NN only, no energy |
| Kılıçkaya, MSc thesis (2022) | 1D-CNN on STM32L4 (Cortex-M4), 99.88 % | Same-bearing holdout, no energy, not peer-reviewed |
| Gao et al., *Sci. China Tech. Sci.* 68 (2025) 2220401 | ESP32-S3, 88.28 %, 45 ms, 17.7 mJ; a Keithley 6510 is listed in the SI | **Proprietary** 4-class data; one model; measurement method not verifiable (paywalled) |
| El Boughardini et al., *JESA* 59(4) (2026) 1141–1160 | Teensy 4.1 (M7, 600 MHz) int8 1D-CNN, 98.6 % macro-F1, 4.7 ms, 90 kB flash / 42 kB RAM, **456 µJ** (Joulescope JS220 average power × DWT latency, not idle-subtracted); splits at the bearing/run level on pooled multi-dataset data (3 classes) | Closest leakage-aware deployment, but one family, Cortex-M7, no frontier |
| Rojas-Carrasco, Guinaldo, *Appl. Sci.* 16 (2026) 3679 | Pareto fronts of LR / MLP / CNN on three cheap boards | Images, no measured energy, no trees |
| Njor et al., *IEEE Access* 12 (2024); Alharthi, *Sensors* 26 (2026) | Surveys: toolchains unbenchmarked; energy/memory metrics non-standard | Gap statement for the introduction |

## The gap, in one paragraph (proposed text)

Prior microcontroller studies of bearing-fault diagnosis report one model family, evaluated with splits that put the
same physical bearing in training and test [Liao; Kılıçkaya; BearingNAS]. When energy is reported, it covers inference
only, or it is derived from datasheet power [Muthumala]. The leakage literature shows that honest splits cut CWRU
performance roughly in half for deep models (95 % → 53 % accuracy for the best CNN in [Hendriks]; ≈ 100 % → 63 % macro
AUROC in [Vieira]). It also shows that a random forest on handcrafted features can then beat a CNN [Vieira], but it never
measures deployment cost. Measured Pareto benchmarking on Cortex-M [Jain] and measured end-to-end energy [Bartoli] exist
only for image and audio neural networks. To our knowledge, no study compares trees, MLPs and CNNs (float and int8) on
one Cortex-M4 with measured front-end and inference energy, and none asks whether the accuracy–energy–memory frontier
changes when evaluation leakage is removed.

**Claims we will not make** (a precedent exists): "first to measure pre-processing energy" (Bartoli 2025), "first
Pareto study on Cortex-M" (Jain 2025), "first leakage-free CWRU evaluation" (Hendriks 2022), "first use of hypervolume"
(not checked exhaustively; the core papers do not use it).

## Corrections to our own earlier material

These came up while reading the papers. The deck and the companion doc have been fixed where they were affected.

| Earlier statement | What the paper actually says |
|---|---|
| Hendriks: "4 classes, ~95 % → ~53 %" | **7 classes**. 95.0 → 53.1 % is the best model (ResNet on spectrograms); the mean of all 6 set-ups is 89.4 → 44.1 % |
| Vieira: "accuracy ≈ 100 % → 74.5 % (CNN) / 85.1 % (RF)" | The metric is **macro AUROC**. 74.5 / 85.1 % are both on the bearing-wise protocol (a ranking, not a drop). The leaky-vs-clean drop is a separate WDCNN experiment: ≈ 100 % → 63–64 % |
| Zhang ICCD: "~10× CMSIS-NN vs reference kernels on this board" | The 10.6× is **CMSIS-NN vs hand-written pure C**, on an **STM32L476RG** (also Cortex-M4F), not the Nano |
| MLPerf Tiny: "median over 5 inputs" | Median over **5 runs** of ≥ 10 s and ≥ 10 inferences each. Our 5-input median is our own choice |
| Jain: "Nordic PPK II" | The paper only says "Power Profiler Kit (PPK)". Idle currents come from **datasheets**, not measurement |
