# Bartoli et al. (2025), two papers

## (a) End-to-End Efficiency in Keyword Spotting: A System-Level Approach for Embedded Microcontrollers
Bartoli, Bondini, Veronesi, Giudici, Antonello, Zappa · 2025 IEEE SENSORS, Vancouver, pp. 1–4 ·
DOI 10.1109/SENSORS59705.2025.11330257 · arXiv 2509.07051 · BibTeX key `bartoli2025endtoend`

- **Hardware:** STM32 N6 (Cortex-M55 + NPU, 800 MHz), H7 (M7, 480 MHz) and U5 (M33, 160 MHz). All models are int8, and
  MFCC runs on each chip's DSP.
- **Data and models:**
  - Google Speech Commands v0.02, 10 keywords.
  - Models: DS-CNN, LiCoNet-S, TENet6, and their own TKWS-2 and TKWS-3.
  - MFCC grid: {32, 63} windows × {15, 30} mel banks.
- **Method:** "Latency and energy were measured for the pre-processing and inference phases", with the procedure taken
  from paper (b). Post-processing is called negligible.
- **Results:**
  - TKWS-3 reaches 92.4 % F1 with 14.4 k parameters.
  - N6 has the best energy-delay product (EDP).
  - Without an NPU, 1D-conv models are more energy-efficient than 2D ones.
  - 63 windows beat 32.
  - The MFCC-vs-NN energy split is only in a raster heatmap and was not readable.
- **Not in the paper:** trees, vibration data, leakage or splits, Pareto fronts, battery lifetime.

## (b) Benchmarking Energy and Latency in TinyML: A Novel Method for Resource-Constrained AI
Bartoli, Veronesi, Giudici, Siorpaes, Trojaniello, Zappa · IJCNN 2025, Rome, pp. 1–8 ·
DOI 10.1109/IJCNN64981.2025.11228997 · arXiv 2505.15622 · BibTeX key `bartoli2025benchmarking`

- **Hardware:** STM32N6570-DK board (Cortex-M55 + NPU).
- **Models:** the four MLPerf Tiny networks (not retrained).
- **Measurement:**
  - A 50 mΩ ±1 % shunt on the **core rail**, read with a Tektronix MSO64B oscilloscope.
  - Energy E = ∫ (ΔV/R)·V_core dt.
  - **Two GPIOs encode four phases:** pre-inference 10, inference 11, post-inference 01, idle 00.
  - 1000 repetitions per model.
  - The **DUT runs on its own supply**.
- **Critique of MLPerf Tiny:**
  - its single trigger does not isolate inference;
  - energy, latency and accuracy are measured on separate set-ups;
  - it has no combined metric;
  - the energy monitor must power the DUT (1.8–3.3 V), which does not fit sub-1 V cores.
- **Results:**
  - Pre-inference (here: loading the input tensor from external flash) is **59–70 % of energy**. NPU inference is
    11.5–30.6 %.
  - Lowering V_core/clocks cuts total energy by 25–29 % for +2–5 % latency, which is about −25.5 % EDP on average.
- **Not in the paper:** trees, vibration data, splits, Pareto fronts, battery lifetime.

## Use in our paper
- Cite (a) for "end-to-end energy (features + inference) matters and is measured for audio", and (b) for the phase
  method.
- Our rig differs in two ways:
  - We use one sync pin and **separate measured windows per phase** (FRONT, RUN, E2E) rather than multi-bit phase codes.
  - We measure the whole board through USB VBUS rather than the core rail. We report total E and idle-subtracted ΔE, and
    state that the USB PHY is inside our measured rail.
