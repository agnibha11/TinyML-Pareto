# Sprint 2 (1 Nov 2026): cutting the front end's energy, without hardware

The midsem result was that the feature front end, not the classifier, sets the energy of trees, forests and MLPs
(at least 95.7 % of a tree's work). Sprint 2 attacks that cost in two ways. Both are verified on the virtual
Cortex-M4, need no retraining, and become new configurations to measure when the board arrives. Every number below
comes from a script-written file in `results/`; the paper (`paper/main.pdf`) has the details.

## 1. Fixed-point and hybrid front ends (`firmware/bench/frontend_fx.c`)

Sensors deliver int16 samples. The new front end computes R1, R2 and R4 from them directly, in the float front end's
units, so every trained model runs on it unchanged.

- **R1:** exact integer moments (dual-16-bit SIMD pass, 64-bit SMLAL/UMLAL accumulators).
- **R2 and R4:** q15 and q31 versions on CMSIS-DSP's fixed-point FFTs with block-floating-point rescaling, plus a
  *hybrid* (integer R1 + float FFT fed straight from int16).

Each front end is built for three core classes and run on the virtual MCU (`tools/fxfe/run_fxfe.py`). The features
of all 9,346 windows are computed by the Cortex-M4F machine code itself (QEMU, semihosting), so the accuracy study
(`ml/fixed_point.py`) scores exactly what the chip would produce.

Estimated cycles per window, including the z-score (R1: float / integer; R2 and R4: float / q15 / hybrid):

| Core | R1 | R2 | R4 |
|---|---|---|---|
| Cortex-M4F (Nano 33 BLE) | 34.4k / 22.4k | 124.0k / 149.4k / 115.0k | 182.6k / 236.4k / 177.0k |
| Cortex-M4, no FPU | 793.5k / 32.8k | 2,417.8k / 189.2k / 1,727.4k | 3,410.2k / 313.1k / 2,738.8k |
| Cortex-M0+ | 1,615.4k / 240.8k | 5,404.6k / 651.0k / 4,264.9k | 7,394.1k / 1,130.8k / 6,326.5k |

- **On the Nano's Cortex-M4F the float FFT is hard to beat.** q15 is 1.2–1.3× slower and q31 2×. The integer R1 is
  35 % cheaper, though, or 50 % against a float front end that must first convert int16 samples. The best design
  there is the hybrid.
- **Without an FPU the picture inverts:** q15 is 11–24× faster than float on an M4 without FPU and 6.5–8.3× on an M0+.
- **Accuracy holds.** Dropped into the float-trained trees and forests (1,200 paired runs), the
  integer R1, q31 and hybrid front ends change the mean macro-F1 by at most 0.0004.
  Deployed MLPs agree with float on ≥ 99.6 % of windows. q15 is coarser: MLP
  agreement is 97.9 %, below the 99 % gate.

## 2. Detect-then-diagnose cascade (`ml/cascade.py`)

A machine is healthy almost all the time, yet a single-stage model pays its full cost on every window. In the
cascade, an always-on gate built only from the integer R1 features (3.8 µJ per window)
decides whether to wake the diagnosis model.

- **Gates:** supervised (depth-2 tree) and healthy-only (Mahalanobis distance to healthy windows; needs no fault
  data, which is all a newly installed sensor has). The headline gate fires when either does.
- **Scored without retraining:** every stored run kept its per-window predictions, so the cascade is scored for all
  88 models and all P2/P3 runs.

Results on unseen bearings (P3), with the headline gate:

- **Gate rates:** it flags 99.6 % of fault windows and
  5.9 % of healthy windows.
- **Accuracy:** the mean macro-F1 change is -0.0009, and 91 % of models lose less than 0.005.
- **Best model:** rf-r4-t25d6-fp32 drops from 28.7 µJ to 5.8 µJ per window at 1 % fault
  prevalence (5.4 µJ when stage 2 reuses R1). That is a 5.0× saving
  for F1 0.742 → 0.740.
- **All 88 models:** the median saving is 5.4×, up to 15× for the expensive CNNs.
- **Confirmed alarms:** escalating only when two windows in a row alarm cuts false alarms to
  0.6 % (median saving 7.6×, up to
  65×) for -0.0159 F1.
- **Limit:** the healthy machine must be learned at every operating point. Under P2, the healthy windows of a load
  the gate never saw raise alarms, which cuts the saving to 2.6×.

The Model Selector (<https://agnibha11.github.io/TinyML-Pareto/selector/>) has a **detect-then-diagnose gate** switch
with a fault-prevalence slider.

## Reproduce

```bash
python tools/fxfe/run_fxfe.py --deps ~/vmcu-deps --plugin ~/vmcu-deps/libvmcount.so   # features + cycles (3 cores)
python ml/fixed_point.py            # fidelity, drop-in/retrain accuracy, MLP agreement, fig_fx_cores
python ml/cascade.py                # gates, cascade over all stored runs, fig_cascade_*
python tools/make_selector.py       # selector with the gate switch
python paper/make_numbers.py && (cd paper && latexmk -pdf main.tex)
python -m pytest tests/             # includes tests/test_sprint2.py
```

## Next, with the board

1. Flash the hybrid front end as an extra configuration. Its VERIFY output must equal the float front end's.
2. Measure the gate's stage-1 energy and the escalation energy separately (`FRONT` + `E2E`).
3. Record healthy data at every load at installation, so the healthy-only gate does not raise false alarms.

## Sprint 2 review: slides and presentation guide

- **Slides** (12, split by workstream: Agnibha 1–3 and 8, Abhinav 4–7, Hridayesh 9–12):
  <https://claude.ai/artifact/KkS1QYqHHwJhx7iyBWiPN9>. Speaker notes hold a short script for each slide; the deck
  downloads as PowerPoint or PDF.
- **Presentation guide** (who presents what, per-slide scripts, likely questions, and every number on the slides
  with the file it comes from): <https://claude.ai/artifact/DtcNSZTUBzAF4BLqHnkZJ6>.

Both links are private to their owner until shared.
