#!/usr/bin/env python3
"""Python reference front end: R0-R3 exactly as handoff/features_spec.md (v1, frozen at M0).

This module is the ground truth that the C front end must match (gate M2, max |err| < 1e-3 on normalized features).
Formulas are computed in float64 and returned as float32, so the reference is not itself a float32 rounding victim.

    r1(X)  (n, 10)   mean, rms, std, peak, p2p, crest, kurtosis (non-excess), skewness, shape, impulse
    r2(X)  (n, 24)   r1 + 14 log10 band powers of the Hann-windowed 1024-point rFFT (bins 32b..32b+31, b = 0..13,
                     0-5250 Hz; spec v1.1 drops bands 14-15, which lie in the 5 kHz low-pass stop-band)
    r3(X)  (n, 256)  log10(|X[k]| + 1e-6), k = 1..256
    r4(X)  (n, 42)   r1 + 32 log envelope-spectrum bins (spec v1.2): the 2.0-5.0 kHz band of the un-windowed 1024-point
                     rFFT (bins 171..426) shifted to baseband, |256-point IDFT| = envelope at 3 kHz, minus its mean,
                     times a 256-point Hann, |256-point rFFT| bins 1..32 (11.7-375 Hz, the bearing fault-frequency
                     range), each divided by the mean of the 32, log10(. + 1e-6). Physics-informed (envelope
                     analysis) and independent of shaft speed.
    r0 normalisation  (x - mu) / sigma with two global scalars from the training samples

Also:
    add_noise(X, snr_db, rng)   white Gaussian noise at a per-window SNR (robustness axis)
    Normalizer                  per-feature z-score fitted on training rows (std 0 -> 1)
    cached(rep)                 features of every window in windows.npy, cached in data/processed/feat_<rep>.npy

Run as a script to (re)build the caches:  python ml/features.py
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PROCESSED = REPO / "data" / "processed"
N = 1024
N_FEAT = {"r1": 10, "r2": 24, "r3": 256, "r4": 42}
N_BANDS = 14                                   # spec v1.1 (was 16)
R1_NAMES = ["mean", "rms", "std", "peak", "p2p", "crest", "kurtosis", "skewness", "shape", "impulse"]
R2_NAMES = R1_NAMES + [f"band{b:02d}" for b in range(N_BANDS)]
R3_NAMES = [f"logmag{k:03d}" for k in range(1, 257)]
ENV_K0, ENV_NB, ENV_NK = 171, 256, 32          # R4: band bins 171..426 (2004-5004 Hz), 256-point envelope, 32 bins
R4_NAMES = R1_NAMES + [f"env{k:02d}" for k in range(1, ENV_NK + 1)]


def hann() -> np.ndarray:
    """The handoff constant (symmetric Hann, float32), so Python and C use the same 1024 numbers."""
    p = REPO / "handoff" / "hann_1024.npy"
    if p.exists():
        return np.load(p).astype(np.float64)
    k = np.arange(N, dtype=np.float64)
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * k / (N - 1))).astype(np.float32).astype(np.float64)


def r1(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64).reshape(-1, N)
    mu = X.mean(axis=1)
    rms = np.sqrt((X ** 2).mean(axis=1))
    d = X - mu[:, None]
    m2 = (d ** 2).mean(axis=1)
    s = np.sqrt(m2)
    peak = np.abs(X).max(axis=1)
    p2p = X.max(axis=1) - X.min(axis=1)
    mabs = np.abs(X).mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        crest = peak / rms
        kurt = (d ** 4).mean(axis=1) / s ** 4
        skew = (d ** 3).mean(axis=1) / s ** 3
        shape = rms / mabs
        impulse = peak / mabs
    F = np.stack([mu, rms, s, peak, p2p, crest, kurt, skew, shape, impulse], axis=1)
    return F.astype(np.float32)


def _spectrum(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64).reshape(-1, N)
    return np.fft.rfft(X * hann()[None, :], axis=1)          # (n, 513), unnormalised DFT


def r2(X: np.ndarray) -> np.ndarray:
    S = _spectrum(X)
    P = np.abs(S[:, :32 * N_BANDS]) ** 2                     # bins 0..447 (0-5250 Hz)
    bands = np.log10(P.reshape(-1, N_BANDS, 32).sum(axis=2) + 1e-12)
    return np.concatenate([r1(X), bands.astype(np.float32)], axis=1)


def r3(X: np.ndarray) -> np.ndarray:
    S = _spectrum(X)
    return np.log10(np.abs(S[:, 1:257]) + 1e-6).astype(np.float32)


def hann256() -> np.ndarray:
    """Symmetric 256-point Hann for R4, float32 values of the float64 formula (the C front end computes the same
    numbers once in fe_init with double-precision cos)."""
    k = np.arange(ENV_NB, dtype=np.float64)
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * k / (ENV_NB - 1))).astype(np.float32).astype(np.float64)


def envelope_spectrum(X: np.ndarray) -> np.ndarray:
    """(n, 32) log envelope spectrum of R4 (see module docstring and features_spec.md v1.2)."""
    X = np.asarray(X, dtype=np.float64).reshape(-1, N)
    band = np.fft.rfft(X, axis=1)[:, ENV_K0:ENV_K0 + ENV_NB]     # 2.0-5.0 kHz, shifted to baseband
    env = np.abs(np.fft.ifft(band, axis=1))                       # envelope, 256 samples at 3 kHz
    env = (env - env.mean(axis=1, keepdims=True)) * hann256()[None, :]
    E = np.abs(np.fft.rfft(env, axis=1))[:, 1:ENV_NK + 1]         # 11.7-375 Hz
    return np.log10(E / E.mean(axis=1, keepdims=True) + 1e-6)


def r4(X: np.ndarray) -> np.ndarray:
    return np.concatenate([r1(X), envelope_spectrum(X).astype(np.float32)], axis=1)


FUNCS = {"r1": r1, "r2": r2, "r3": r3, "r4": r4}


def compute(rep: str, X: np.ndarray, batch: int = 2048) -> np.ndarray:
    out = [FUNCS[rep](X[i:i + batch]) for i in range(0, len(X), batch)]
    return np.concatenate(out) if out else np.zeros((0, N_FEAT[rep]), np.float32)


def add_noise(X: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """White Gaussian noise, noise power = signal power / 10^(SNR/10), per window (signal power = mean x^2)."""
    X = np.asarray(X, dtype=np.float32)
    p_sig = (X.astype(np.float64) ** 2).mean(axis=1, keepdims=True)
    sd = np.sqrt(p_sig / 10 ** (snr_db / 10.0))
    return (X + rng.standard_normal(X.shape) * sd).astype(np.float32)


@dataclass
class Normalizer:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, F: np.ndarray) -> "Normalizer":
        F = np.asarray(F, dtype=np.float64)
        mu, sd = F.mean(axis=0), F.std(axis=0)            # ddof = 0
        sd[sd == 0] = 1.0
        return cls(mu.astype(np.float32), sd.astype(np.float32))

    def __call__(self, F: np.ndarray) -> np.ndarray:
        return ((np.asarray(F, np.float32) - self.mean) / self.std).astype(np.float32)


@dataclass
class R0Norm:
    mu: float
    sigma: float

    @classmethod
    def fit(cls, X: np.ndarray) -> "R0Norm":
        X = np.asarray(X, dtype=np.float64)
        return cls(float(np.float32(X.mean())), float(np.float32(X.std())))

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return ((np.asarray(X, np.float32) - np.float32(self.mu)) / np.float32(self.sigma)).astype(np.float32)


def windows() -> np.ndarray:
    return np.load(PROCESSED / "windows.npy", mmap_mode="r")


def cached(rep: str) -> np.ndarray:
    """Features for every window_id (row i = window i). Built once, then memory-mapped."""
    p = PROCESSED / f"feat_{rep}.npy"
    W = windows()
    if p.exists():
        F = np.load(p, mmap_mode="r")
        if F.shape == (len(W), N_FEAT[rep]):
            return F
    F = compute(rep, W)
    np.save(p, F)
    return np.load(p, mmap_mode="r")


if __name__ == "__main__":
    W = windows()
    for rep in ("r1", "r2", "r3", "r4"):
        F = cached(rep)
        print(f"{rep}: {F.shape} finite={np.isfinite(F).all()}")
