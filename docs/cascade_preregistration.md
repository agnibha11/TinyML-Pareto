# Pre-registration: measured detect-then-diagnose cascade

Status: **v0.1, draft (21 Nov 2026).** It becomes binding when it is committed before the first cascade energy
window (pilot session 80 included). Anything changed after that is listed under "Deviations" at the end, with the
date and the reason; nothing here changes silently.

Owner: Hridayesh. Host design study: `ml/cascade_design.py`. Earlier host estimate: `ml/cascade.py`
(`results/<ds>/cascade/`).

## 1. What is being tested

A cascade runs a cheap, always-on stage 1 (fixed-point front end + a detector, the **gate**) on every window and
wakes a stage-2 diagnosis model only when the gate alarms. Its energy per window at fault prevalence π is

E(π) = E₁ + [π·TPR + (1 − π)·FPR]·E₂.

So far every term of this formula is an estimate: E₁ comes from virtual-MCU cycles, E₂ from the pre-hardware model,
and the gate has never run on a board. This study measures every term on the Nano 33 BLE rig and tests the formula
against replayed real recordings.

## 2. Research questions and acceptance rules

Each RQ has one primary outcome. "Within x %" means the mean over the three repeats, with its 95 % CI (§6) reported
alongside. A failed hypothesis is still reported as a result.

| RQ | Question | Primary outcome | Hypothesis / acceptance |
|---|---|---|---|
| RQ1 | Does the board's gate make the same decisions as the host? | alarm agreement, `GVERIFY` over every test vector of every gate | 100 % agreement. A mismatch counts as explained only if the host score lies within 1e-5 relative of the threshold; it is reported with its margin. |
| RQ2 | What does stage 1 cost, measured against the estimate? | E₁ per gate (µJ/window), `GATE` mode | Reported with CI and as the ratio measured / chip-level estimate. Expected ratio 2.5–3.1, the ratio the board's float front ends already show (§4.3). |
| RQ3 | Is the cascade additive? | Δ = E(`CASCADE on`) − E(`GATE`) − E2E(model) | \|Δ\| ≤ 2 % of E2E(model) for every stage-2 model, or the overhead is quantified. |
| RQ4 | Does E(π) predict real streams? | measured energy per window on each stream vs the prediction from measured E₁, E₂ and the stream's realized alarms | Within 3 % on every stream. |
| RQ5 | Which gate should a deployment use? | measured gate front per dataset: E₁ vs FPR at TPR ≥ 0.98 | The cheapest CWRU gate (integer R1) is **not** on the Paderborn front. |
| RQ6 | What does feature reuse save? | E(`CASCADE on`) − E(`CASCADE reuse`) | Saving ≈ the measured cost of the shared front-end part: the R1 part of an R4 model with an R1 gate (small, ≤ 10 % of E2E); the whole R4 front end with an R4 gate (Paderborn). |
| RQ7 | Can a gate calibrate itself on the device, with no fault data? | board: G5 alarms after `CALIB n` (GVERIFY and streams), `CALIB` energy; host: FPR at the unseen load | Board: after `CALIB 16` and `CALIB 32`, the alarms equal the host's exactly (RQ1 rule). Host, CWRU P2 fold 0 (§4.4): FPR 1.0 → ≤ 0.05 with TPR ≥ 0.99. Host, all P2 folds: FPR 0.26 → ≤ 0.08 at N = 32. |
| RQ8 | What does this mean for a deployment? | energy per hour and battery life at 100 ms, 1 s and 10 s monitoring intervals, with thread sleep between windows; detection latency | Reported, board-level and incremental energy kept apart (the board idles at ~18.8 mW, mostly USB). |

## 3. Data, folds and models

- Datasets: CWRU (P2 unseen load, P3 unseen bearing) and Paderborn (P3, P4 real damage).
- **The board runs one split per dataset:** the split of the deployed instances (CWRU P2 fold 0, Paderborn P3
  fold 0, seed 0; `ml/dataset.py` `DEPLOYED`), the same instances as the energy bundles.
  - The board's gates are fitted on that split's training and validation windows.
  - Its streams come from that split's test windows.
  - For CWRU this is a real deployment at an operating point the models never saw: the models were trained on
    loads 1–3 and run at load 0.
- **Host claims across folds** (RQ5, RQ7 host part) use all folds, as in the design study.
- **Windows:** 1024 samples at 12 kHz (85.3 ms; test windows do not overlap), stored as int16, which is what a sensor
  delivers. Gates read int16. Stage-2 models read the float front end of the same int16 window.
- Gate thresholds are tuned on the validation split only, exactly as in `ml/cascade.py`.
- **Export:** `ml/cascade_export.py` writes gate parameters, GVERIFY expectations, the window pool and the streams
  to `handoff/cascade/<ds>/`. It checks that:
  - its gates reproduce `ml/cascade.py` exactly;
  - a float32 tree walk gives the float64 decisions;
  - no GVERIFY vector lies within 1e-5 of a threshold (CWRU: none).

## 4. What goes on the board (from the host design study)

### 4.1 Design study (done on the host, CWRU, 21 Nov)

`ml/cascade_design.py` covers:
- front ends integer R1 (`fx_r1`), R1 + R2 log bands (`hyb_r2`), R1 + R4 envelope bins (`hyb_r4`);
- detectors: depth-1–3 trees, Mahalanobis on healthy windows only, "either" (tree OR Mahalanobis);
- confirmation 1-of-1, 2-of-2, 2-of-3 over consecutive windows of a recording;
- a self-calibrating Mahalanobis gate (N ∈ {16, 32, 64, 128}).

Results:
- **Integer R1 gates dominate on CWRU.** The R2/R4-based gates cost 53–101 µJ per window (hardware-calibrated),
  the same order as the stage-2 models themselves (RF/DT/MLP E2E 58–230 µJ measured).
- **Tree depth makes no difference.** Depths 1–3 give the same alarms.
- **2-of-2 confirmation cuts false alarms about 10×.** In P3 the FPR falls from 0.059 to 0.006, the TPR from
  0.996 to 0.984, and alarms come one window later.

The gate choice below uses test-split host scores. The energy RQs (RQ2–4, 6, 8) do not depend on detection scores,
so this selection does not bias them. For the detection claims, CWRU has no held-out data left, and the paper says
so. Paderborn keeps P4 out of the selection and uses it as the held-out check (§4.2).

### 4.2 Gates (≤ 6 per dataset)

| Gate | Front end | Detector | Confirmation | Dataset |
|---|---|---|---|---|
| G1 `sup` | fx_r1 | depth-2 tree (balanced) | 1-of-1 | both |
| G2 `healthy` | fx_r1 | Mahalanobis, healthy-only, 99th percentile of validation healthy | 1-of-1 | both |
| G3 `either` | fx_r1 | G1 OR G2 | 1-of-1 | both |
| G4 `either2` | fx_r1 | G1 OR G2 | 2-of-2 | both |
| G5 `selfcal` | fx_r1 | G2 with the threshold rescaled from N on-device healthy windows (§4.4) | 1-of-1 | both |
| G6 `selfcal_r2` | fx_r2_hyb (R1 + 14 log bands) | G5 on 23 features, N = 128 (chosen after the study: Deviations, 4) | 1-of-1 | Paderborn |

G1–G4 are the paper's current gates, so the host and board numbers can be compared one to one. G1 is
`ml/cascade.py`'s `sup`: a tree on all 10 fx_r1 features. On CWRU P2 fold 0 it splits on the window mean.

**Host rates on the board's split, CWRU P2 fold 0** (test, load 0; `handoff/cascade/cwru/summary.json`):

| Gate | TPR | FPR |
|---|---|---|
| G1 sup | 1.0 | 0.42 |
| G2 healthy | 1.0 | 1.0 |
| G3 either | 1.0 | 1.0 |
| G4 either2 | 0.99 | 0.98 |
| G5 selfcal, N = 16 | 0.999 | 0.023 |
| G5 selfcal, N = 32 | 0.999 | 0.037 |

At this unseen load, the factory healthy-only gate alarms on every healthy window. Self-calibration from 1.4 s of
healthy signal repairs it. Fold 0 is the worst of the four P2 folds: across all folds, the factory FPR is 0.26.

**G6 selection rule (fixed before the Paderborn design study runs):**
1. Run `ml/cascade_design.py` on Paderborn P3: the same front ends, detectors and confirmations as on CWRU.
2. Keep the gates with mean TPR ≥ 0.98 over folds.
3. Among those, choose the gate with the lowest predicted E(π = 1 %) for the Paderborn headline stage-2 model,
   using hardware-calibrated E₁.
4. Break ties by the lower E₁.
5. If no gate reaches TPR ≥ 0.98, choose the one with the highest TPR − FPR, and report it as a weak gate.

P4 is not used for the choice. Its TPR/FPR for G6 is the held-out check.

### 4.3 Stage-1 costs used for the predictions

`E1_hwcal = cycles / 64 MHz × 9.984 mW × r`. Here r is the measured / estimated ratio of the board's float front end
with the same representation (R1 2.53, R2 2.93, R4 3.11; from the repeat 1–3 FRONT windows of `dt-r{1,2,4}-d4`).
Integer R1 gates: 8.9–9.6 µJ predicted per window. RQ2 replaces these with measurements.

### 4.4 Self-calibrating gate (G5)

- **Factory part.** ρ = (G2 threshold) / (median Mahalanobis distance of the validation healthy windows). It is the
  only factory number G5 uses.
- **On the device, `CALIB n`:**
  - takes the first n healthy pool windows, which stand in for the first n windows after installation;
  - computes their mean and covariance in double precision (Welford, 9 + 45 values) and adds the ridge 1e-3·I;
  - inverts the result (Cholesky);
  - sets the threshold to ρ × the median distance of the n windows.
- No fault data and no validation data are used at the deployment.
- **Measured:** n = 16 and n = 32, i.e. 1.4 s and 2.7 s of signal. Longer is not possible, because the CWRU healthy
  test recording at load 0 has 59 non-overlapping windows.
- **Host prediction for the board's definition** (CWRU P2 fold 0, evaluated on the healthy windows after the
  calibration span and every fault test window):

  | n | FPR | FPR of the factory gate, same windows | TPR |
  |---|---|---|---|
  | 16 | 0.023 | 1.0 | 0.999 |
  | 32 | 0.037 | 1.0 | 0.999 |

  These rates rest on 43 and 27 healthy windows, so they are coarse.
- **Across folds** (design study, overlapping calibration windows; all P2 folds):

  | Setting | FPR | TPR |
  |---|---|---|
  | factory | 0.26 | — |
  | N = 32 | 0.075 | 1.0 |
  | N = 128 | 0.044 | 1.0 |

  P3 does not improve; its FPR is already about 0.055.

### 4.5 Stage-2 models

CWRU, the most accurate model of each family on P2 and P3 (host macro-F1, fold 0 deployed instances); measured
board E2E from repeats 1–3 (int8 from repeats 5–7):

| Protocol | Family | Model | E2E (µJ) |
|---|---|---|---|
| P2 | RF | rf-r2-t25d10-fp32 | 58 |
| P2 | DT | dt-r4-d12-fp32 | 89 |
| P2 | MLP | mlp-r3-h128-int8 | 230 |
| P2 | CNN | cnn-r0-w050d4-int8qat | 1 940 |
| P3 | RF | rf-r4-t25d6-fp32 | 90 |
| P3 | DT | dt-r4-d4-fp32 | 89 |
| P3 | MLP | mlp-r4-h64-int8 | 95 |
| P3 | CNN | cnn-r0-w200d4-fp32 | 64 800 |

Together these span three decades of E₂.
- **Flash.** The eight models take ~434 KB, the TFLM base ~320 KB, and the window pool (§4.6) 190 KB, against a
  983 KB limit. If the cascade bundle does not link, `cnn-r0-w200d4-fp32` (332 KB) is replaced by the most accurate
  P3 CNN that fits, before any data is taken.
- **Paderborn.** The same rule (best of each family on P3 and P4) picks Paderborn's stage-2 set. Result of the
  Paderborn design study (21 Nov), measured board E2E:

  | Protocol | Family | Model | E2E (µJ) |
  |---|---|---|---|
  | P3 | DT | dt-r2-d6-fp32 | 57 |
  | P3 | CNN | cnn-r0-w025d4-fp32 | 2 160 |
  | P3 | RF | rf-r2-t25d6-fp32 | 58 |
  | P3 | MLP | mlp-r4-h32-fp32 | 93 |
  | P4 | RF | rf-r4-t25d6-fp32 | 87 |
  | P4 | CNN | cnn-r0-w025d2-fp32 | 1 140 |
  | P4 | DT | dt-r1-d4-fp32 | 14 |
  | P4 | MLP | mlp-r3-h128-fp32 | 304 |

  Paderborn's stream model is `mlp-r4-h32-fp32`, the most accurate on P3.

### 4.6 Streams

- **Window pool.** Test windows of the deployed split, stored once as int16, 2 KB per window. For CWRU that is
  95 windows (190 KB):
  - every non-overlapping test window of the first healthy test recording, at most 64 (CWRU: 59, file 97,
    load 0);
  - 4 consecutive test windows from the middle of each faulty test recording, at most 9 recordings (CWRU: 9
    classes × 4 = 36).
- **A stream** is a fixed list of 100 pool indices. Healthy positions cycle through the healthy pool in time order.
  Fault positions come in runs of 2 consecutive windows of one recording, cycling through the faulty recordings,
  because a 2-of-2 gate can never escalate a single fault window:

  | Stream | Content |
  |---|---|
  | S0 | 100 healthy (π = 0) |
  | S2 | one run of 2 faults in 100 (π = 2 %) |
  | S10 | five runs of 2 (π = 10 %) |
  | S100 | 100 faults (π = 100 %) |
  | ST | 50 healthy, then 50 faults (the healthy → fault transition, for latency) |

  E(π) is linear in π, so RQ4 compares each stream with its own prediction. The exact prevalence values are not
  important.
- **Run positions** come from a fixed seed (`20261121`); runs never touch.
- **G4 state.** The previous-window state is reset at the start of every stream, so position 0 cannot confirm.
- **Export.** For each stream, `streams.csv` holds the pool indices, the fault flags and the expected alarm of every
  gate (G5 after `CALIB 16` and `CALIB 32`).
- **Expected stage-2 classes** are not exported. The energy RQs need only the escalation count. Stage-2 agreement is
  already covered by the model VERIFY of every bundle.
- **Stage 2 on the streams.** The most accurate model on the deployed protocol (CWRU P2: `rf-r2-t25d10-fp32`).
  Every other stage-2 model is covered by S-2 and the RQ3 additivity.
- **G5 on S0.** S0 contains G5's calibration windows, so its FPR there is optimistic. The RQ7 FPR comes from the
  host evaluation on windows after the calibration span (§4.4). The board's G5 streams test alarm agreement and
  energy.

## 5. Board campaign

### 5.1 Commands

The firmware commands come in CONTRACT v1.1: `TEMP`, `GSEL`, `GVERIFY`, `GATE`, `CASCADE`, `STREAM`, `CALIB`,
`DUTY`. Every timed command follows §7: D2 HIGH only around the measured iterations, and no serial I/O inside the
window.

### 5.2 Blocks per session

| Block | What | Conditions |
|---|---|---|
| S-V | `GVERIFY` of every test vector for each gate; stream alarm sequences against the host. Not an energy block; it runs once per firmware build, plus a short check at the start of every session. | — |
| S-1 | `GATE`: each gate × the 5 stored inputs (healthy and faulty, to test data dependence) | 6 |
| S-2 | `CASCADE off / on / reuse`, for each stage-2 model | 24 |
| S-3 | `STREAM` S0/S2/S10/S100/ST. CWRU: G1, G4 and G5 (after `CALIB 32`). Paderborn: G3, G4, G6. | 15 |
| S-4 | `CALIB 16`, `CALIB 32` (calibration cost), then `GVERIFY` of the pool with G5 (alarm agreement) | 2 |
| S-5 | `SLEEP`, then `DUTY` at 100 ms, 1 s, 10 s | 4 |
| S-6 | `FRONT`: fx_r1 vs float R1, fx R2/R4 hybrids vs float | 6 |

Every condition follows the energy rules: ≥ 10 s windows, N from `LAT`, an IDLE window before each block, 5 windows
per stored input, and the same rejection and pairing rules as the energy campaign (CONTRACT §7.4).

### 5.3 Sessions and order

| Session | What |
|---|---|
| 80 | Pilot: S-V + one window per condition, to size N and check timeouts. Not analysed. |
| 81, 82, 83 | Cascade repeats 1–3. Dataset order alternates: CWRU first in 81 and 83, Paderborn first in 82. |
| 181, 182, 183 | Continuations of a stopped session (+100), as in the energy campaign. |

- Results go to `results/<ds>/cascade_hw/`: measurements, session log, build record and gate VERIFY. They are not
  written into `hardware/hw_measurements.csv`, so the 80-model energy data stays untouched.
- **Condition order.** Randomized within each session, with the seed recorded in the session log.
- **Drift anchor.** `GATE` of G1 on stored input 0 runs at the start, middle and end of every session.
- **Every session also:**
  - starts with `rig_check`;
  - records die temperature (`TEMP`) next to each window, and room temperature in the session log;
  - runs on the same firmware hash. A firmware change restarts the cascade repeats.

## 6. Analysis plan (`ml/cascade.py --hw`)

- Per condition and repeat: the mean of the accepted windows. Then the mean over the three repeats, with a 95 %
  t-interval over repeats (n = 3). A bootstrap over repeats and the stored host runs gives the bands of the E(π)
  curves.
- E₂ = measured E2E of the model (repeats 1–3; int8 repeats 5–7).
- RQ3: Δ with its CI per model.
- RQ4: per stream, (measured − predicted) / predicted. The prediction uses the board's realized alarm count
  (`STREAM` reply), so a detection error cannot hide an energy error.
- Every estimated term is reported next to its measured value, as a ratio. This calibrates the paper's power
  model.
- **Exclusions:** only windows rejected by the CONTRACT §7.4 rules. There is no outlier removal beyond these. A
  condition with fewer than 3 accepted windows in a repeat is reported as missing, not imputed.

## 7. Known systematic

The fp32 CNN anchors moved −2.3 % (Paderborn) and +1.4 % (CWRU) in time between the unpatched and the int8v2
library, and are stable to 0.1 % within each. This is most likely a code-layout effect. Stage-2 E2E values for fp32
CNNs therefore carry a ±2 % systematic, which the RQ3 and RQ4 tolerances must be read against.

## Deviations

Deviations 1–3 were recorded on 21 Nov 2026, before the Paderborn design study ran. Deviation 4 was recorded after
it ran. All four come before any cascade energy window.

1. **§4.2 rule step 3, "Paderborn headline stage-2 model".** This was not defined. It is now the paper's Paderborn
   P3 best model, `mlp-r4-h32-fp32` (`results/pu/analysis/summary.json`, P3 `best_model`), i.e. the most accurate
   model on the protocol the board runs.
2. **Paderborn recordings in the design study.** The Paderborn manifest has no `file_id`.
   - **Confirmation:** k-of-n runs over consecutive test windows of one recording (bearing, condition, recording).
   - **Self-calibration:** it calibrates on the first N windows, in time order, of one healthy bearing at one
     operating condition (its five recordings in a row). It is evaluated on that bearing and condition's test
     windows after the calibration span, plus every fault test window. One Paderborn recording has only about 46
     windows, too few for N = 32 with anything left to evaluate.
3. **Firmware (CONTRACT v1.1).**
   - `FXFE` offers fx_r1 and the two hybrid front ends only. The q15/q31 paths do not link with the board's
     CMSIS-DSP 5.7.0, and S-6 needs only fx_r1 and the hybrids.
   - `CASCADE` runs stage 2 on the float stored input that `E2E` measures, so RQ3's Δ contains only the cascade's
     own overhead. `STREAM` and `DUTY` convert the int16 window to float first, as a deployment would.
4. **G6 (21 Nov 2026, after the Paderborn design study; a post-hoc choice).**
   - **What the rule gave.** Applied as written, the §4.2 rule picks `fxr1 tree1 1of1`. On Paderborn P3, every gate
     that reaches mean TPR ≥ 0.98 has FPR 1.0 (8 of 45). So the chosen gate escalates every window, and the cascade
     would cost more than the headline model alone: 102 µJ against 93 µJ per window at π = 1 %. The rule searched
     front ends, detectors and confirmations, but not the design study's fourth axis, self-calibration (§4.1).
   - **What replaces it.** G6 is the self-calibrating Mahalanobis gate on `hyb_r2`:
     - features: the 9 fx_r1 gate features plus the 14 log band powers (23 features, `fx_r2_hyb` on the board);
     - calibrated on N = 128 healthy windows.
   - **How N was chosen.** The rule's own cost criterion, the lowest E(π = 1 %), among the N that fit the
     Paderborn bundle's flash:

     | N | P3 TPR | P3 FPR | E(π = 1 %) | P4 TPR | P4 FPR |
     |---|---|---|---|---|---|
     | 64 | 0.994 | 0.032 | ≈ 61 µJ | 1.0 | 0.051 |
     | 128 | 0.989 | 0.012 | ≈ 59 µJ | 1.0 | 0.016 |

     The P4 columns are held out.
   - **In the paper,** G6 is reported as chosen after the study. `fxr1 tree1` is reported as the rule's result, with
     its E(π = 1 %).
   - **On the board.**
     - The Paderborn pool holds the first 128 windows, in time order, of the first healthy (bearing, condition)
       of the P3 fold-0 test split.
     - `CALIB` with gate 6 selected calibrates G6. The factory part of G6 is only its ρ, from the training split's
       `hyb_r2` Mahalanobis.
     - Paderborn S-2 uses G6, so `CASCADE` m = 2 reuses the whole R2 front end for the R2 models (RQ6).

