# features_spec.md — front-end specification (R0–R3)

Owner: Agnibha · Implemented by: `ml/features.py` (Python reference, ground truth) and `firmware/bench/frontend.*`
(C, Abhinav). Status: **v1, frozen at M0**. The C front end must match Python to **max |error| < 1e-3 on normalized
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

## R2 — R1 + 16 log band energies (26 features)

1. Multiply by the symmetric Hann window `w[n] = 0.5 − 0.5·cos(2πn / 1023)`, n = 0..1023. Use the generated
   constants in `handoff/hann_1024.h` / `hann_1024.npy`. Do not compute `cosf` on the device.
2. Real FFT of length 1024 → bins k = 0..512, bin width 12000/1024 = 11.71875 Hz. Unnormalized DFT:
   `X[k] = Σ x_w[n] · e^(−2πi·kn/1024)` (NumPy `np.fft.rfft`, CMSIS `arm_rfft_fast_f32`).
3. Power `P[k] = |X[k]|²`.
4. Band b = 0..15 covers bins `32b … 32b + 31` (375 Hz per band, 0–6 kHz). Bin 512 (Nyquist) is not used.
5. Feature `B[b] = log10( Σ_{k in band b} P[k] + 1e-12 )`.

Order: `[R1 (10), B[0] … B[15]]`.

## R3 — log magnitude spectrum (256 features)

Same Hann window and FFT as R2. Feature `k − 1 = log10( |X[k]| + 1e-6 )` for k = 1..256 (11.7 Hz – 3 kHz). DC is
excluded.

**CMSIS-DSP packing trap:** `arm_rfft_fast_f32` returns element 0 = Re X[0] (DC) and element 1 = Re X[512] (Nyquist),
then (Re, Im) pairs for k = 1..511. Unpack before computing magnitudes. `arm_cmplx_mag_f32` on elements 2.. gives |X[k]|
for k ≥ 1.

## Normalization of R1–R3

Per-feature z-score, `f_norm = (f − mean_f) / std_f`. mean_f and std_f (ddof = 0) come from the **training set of P2
fold 0**, the fold whose models are deployed. Exported as `handoff/norm_constants.h` (float arrays) and
`handoff/norm_constants.npz`, together with R0's μ and σ. If std_f = 0 for some feature, use 1.

## Test vectors for the parity test (M2)

- 100 fixed test windows, 10 per class, from the P2 fold-0 test set: `handoff/test_vectors/ids.npy`.
- Exported as `.npy` and as C arrays: raw windows, R1/R2/R3 before and after normalization, and host outputs
  for every model in exactly the form VERIFY returns (dequantized softmax probabilities for NNs, one-hot for trees;
  `handoff/test_vectors/outputs/<model_id>.npy`, CONTRACT.md 7.2).
- Pass condition: max |C − Python| < 1e-3 on every **normalized** feature of every window.
- Usual culprits when it fails: ddof, excess vs non-excess kurtosis, FFT packing, log of zero, float32 accumulation
  order.
