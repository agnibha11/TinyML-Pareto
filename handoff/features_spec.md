# features_spec.md — front-end specification (R0–R4)

Owner: Agnibha · Implemented by: `ml/features.py` (Python reference, ground truth) and `firmware/bench/frontend.*`
(C, Abhinav; reference C implementation in `tools/vmcu/fw/frontend.c`). Status: **v1.2** (v1 frozen at M0; v1.1
changes R2, v1.2 adds R4; see the change log at the end). The C front end must match Python to **max |error| < 1e-3 on normalized
features** over the 100 test vectors (gate M2). Changing any formula after M2 means re-running parity and re-measuring
every tree and MLP.

Input to every representation: one window `x[0..N-1]`, N = 1024, float32, 12 kHz, raw accelerometer units. This is
the output of `ml/prepare_data.py`: normal files decimated from 48 kHz, and every recording low-passed at 5 kHz.

## R0 — raw input for CNNs

`x_norm[i] = (x[i] − μ) / σ`, where μ and σ are **two global scalars** computed over all training samples of the fold
(every sample of every training window). Do **not** normalize per window: amplitude carries fault-size information.

For int8 models, the firmware then quantizes with the model's own input tensor parameters:
`q = clamp(round(x_norm / scale) + zero_point, −128, 127)`. `scale` and `zero_point` are read from the `.tflite` and
copied into `meta.json`, and the firmware checks that the two agree.

## R1 — 10 time-domain features (population statistics, divide by N, ddof = 0)

| # | Name | Definition |
|---|---|---|
| 1 | mean | μ = Σ x[i] / N |
| 2 | rms | sqrt( Σ x[i]² / N ) |
| 3 | std | s = sqrt( Σ (x[i] − μ)² / N ) |
| 4 | peak | max |x[i]| |
| 5 | p2p | max x[i] − min x[i] |
| 6 | crest | peak / rms |
| 7 | kurtosis | Σ (x[i] − μ)⁴ / (N · s⁴) — **not** excess kurtosis (no −3) |
| 8 | skewness | Σ (x[i] − μ)³ / (N · s³) |
| 9 | shape | rms / ( Σ |x[i]| / N ) |
| 10 | impulse | peak / ( Σ |x[i]| / N ) |

Order in the feature vector = the order of this table (index 0 = mean … index 9 = impulse).
Compute μ first, then the central moments (two-pass). A single-pass raw-moment formula loses precision in float32.

## R2 — R1 + 14 log band energies (24 features)

1. Multiply by the symmetric Hann window `w[n] = 0.5 − 0.5·cos(2πn / 1023)`, n = 0..1023. Use the generated
   constants in `handoff/hann_1024.h` / `hann_1024.npy`. Do not compute `cosf` on the device.
2. Real FFT of length 1024 → bins k = 0..512, bin width 12000/1024 = 11.71875 Hz. Unnormalized DFT:
   `X[k] = Σ x_w[n] · e^(−2πi·kn/1024)` (NumPy `np.fft.rfft`, CMSIS `arm_rfft_fast_f32`).
3. Power `P[k] = |X[k]|²`.
4. Band b = 0..13 covers bins `32b … 32b + 31` (375 Hz per band, 0–5250 Hz). Bins 448–512 are not used.
5. Feature `B[b] = log10( Σ_{k in band b} P[k] + 1e-12 )`.

Order: `[R1 (10), B[0] … B[15]]`.

## R3 — log magnitude spectrum (256 features)

Same Hann window and FFT as R2. Feature `k − 1 = log10( |X[k]| + 1e-6 )` for k = 1..256 (11.7 Hz – 3 kHz). DC is
excluded.

**CMSIS-DSP packing trap:** `arm_rfft_fast_f32` returns element 0 = Re X[0] (DC) and element 1 = Re X[512] (Nyquist),
then (Re, Im) pairs for k = 1..511. Unpack before computing magnitudes. `arm_cmplx_mag_f32` on elements 2.. gives |X[k]|
for k ≥ 1.

## R4 — R1 + 32 log envelope-spectrum bins (42 features, v1.2)

Classical bearing envelope analysis, reduced to a fixed-size, speed-independent feature vector. A local fault excites
structural resonances once per impact; the impact rate (BPFO ≈ 3.58×, BPFI ≈ 5.42×, ball 2×BSF ≈ 4.71× shaft speed,
i.e. about 70–165 Hz here, plus harmonics) appears in the spectrum of the **envelope** of the resonance band. These
rates depend on where the fault is, not on how large it is.

1. `X = rFFT_1024(x)`, **no window**, unnormalised (as R2's DFT definition, without the Hann).
2. Band: `Z[m] = X[171 + m]`, m = 0..255, complex (bins 171..426 = 2003.9–4992.2 Hz). Taking the bins out of place
   shifts the band to baseband; the envelope does not depend on that shift.
3. Envelope: `e[m] = | IDFT_256(Z)[m] |` with the 1/256 inverse scaling (NumPy `np.fft.ifft`; CMSIS
   `arm_cfft_f32(&arm_cfft_sR_f32_len256, buf, 1, 1)` then `arm_cmplx_mag_f32`). 256 samples at 3 kHz = the same 85 ms.
4. `u[m] = (e[m] − mean(e)) · h[m]`, with the symmetric 256-point Hann `h[m] = 0.5 − 0.5·cos(2πm / 255)`, evaluated
   in double precision and rounded to float32 (the C front end does this once in `fe_init`).
5. `E[k] = | rFFT_256(u)[k] |`, k = 1..32 (bin width 3000/256 = 11.71875 Hz: 11.7–375 Hz).
6. Feature `9 + k = log10( E[k] / mean_{j=1..32} E[j] + 1e-6 )`, k = 1..32. Dividing by the mean keeps only the
   *shape* of the envelope spectrum, not its level (level tracks fault size, which P3 holds out).

Order: `[R1 (10), env01 … env32]`. Same CMSIS-DSP packing trap as R3 for step 5 (element 1 is Re E[128]).

## Normalization of R1–R4

Per-feature z-score, `f_norm = (f − mean_f) / std_f`. mean_f and std_f (ddof = 0) come from the **training set of P2
fold 0**, the fold whose models are deployed. Exported as `handoff/norm_constants.h` (float arrays) and
`handoff/norm_constants.npz`, together with R0's μ and σ. If std_f = 0 for some feature, use 1.

## Test vectors for the parity test (M2)

- 100 fixed test windows, 10 per class, from the P2 fold-0 test set: `handoff/test_vectors/ids.npy`.
- Exported as `.npy` and as C arrays: raw windows, R1/R2/R3/R4 before and after normalization, and host outputs
  for every model in exactly the form VERIFY returns (dequantized softmax probabilities for NNs, one-hot for trees;
  `handoff/test_vectors/outputs/<model_id>.npy`, CONTRACT.md 7.2).
- Pass condition: max |C − Python| < 1e-3 on every **normalized** feature of every window.
- Usual culprits when it fails: ddof, excess vs non-excess kurtosis, FFT packing, log of zero, float32 accumulation
  order.

## Change log

- **v1.1 (29 Oct 2026).** R2 keeps bands 0–13 (0–5250 Hz) instead of 0–15. Bands 14–15 sit in the stop-band of the
  common 5 kHz low-pass that `prepare_data.py` applies to every recording: their log10 power is about −10 (signal bands:
  +1 to +3), i.e. FIR stop-band residue below the float32 FFT noise floor. The first parity run of the C front end
  (CMSIS-DSP `arm_rfft_fast_f32`, Cortex-M4F in QEMU, `tools/vmcu/run_vmcu.py`) matched R1 and R3 to < 1e-4 but
  missed these two features by up to 0.62 (normalised units). They carry no physical information after the low-pass,
  so they are removed rather than computed in double precision. R2 becomes 24 features; `g_r2_mean[24]`, `g_r2_std[24]`.
- **v1.2 (29 Oct 2026).** Adds R4 (above). Motivation: in the first host sweep every family scored at most 0.63
  macro-F1 on unseen bearings (P3), and the spectral R2/R3 features overfit the fault size held out. On the same random
  forest (25 trees, depth 10, 3 seeds) R4 raises P3 macro-F1 from 0.60 (R2) to 0.75, at a small cost under P1/P2.
  R0–R3 are unchanged; `g_r4_mean[42]`, `g_r4_std[42]` are new.
