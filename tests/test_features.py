"""ml/features.py against signals whose features are known in closed form (no data download needed)."""
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ml"))
import features as FE  # noqa: E402

N, FS = 1024, 12000.0


def tone(f_hz, amp=1.0, phase=0.3):
    t = np.arange(N) / FS
    return (amp * np.sin(2 * np.pi * f_hz * t + phase)).astype(np.float32)


def test_r1_sine_closed_form():
    x = tone(375.0 * 4 + 11.71875 * 7, amp=2.0)          # not bin-centred on purpose
    f = FE.r1(x)[0]
    assert f.shape == (10,)
    assert f[0] == pytest.approx(0.0, abs=2e-2)          # mean
    assert f[1] == pytest.approx(2.0 / np.sqrt(2), rel=1e-2)   # rms
    assert f[3] == pytest.approx(2.0, rel=1e-3)          # peak
    assert f[5] == pytest.approx(np.sqrt(2), rel=1e-2)   # crest
    assert f[6] == pytest.approx(1.5, rel=2e-2)          # kurtosis of a sine (non-excess)
    assert f[8] == pytest.approx(np.pi / (2 * np.sqrt(2)), rel=1e-2)   # shape factor


def test_r1_gaussian_kurtosis_is_non_excess():
    x = np.random.default_rng(0).standard_normal(N).astype(np.float32)
    f = FE.r1(x)[0]
    assert f[6] == pytest.approx(3.0, abs=0.4)
    assert abs(f[7]) < 0.3


def test_r2_shape_and_band_of_a_tone():
    f = FE.r2(tone(375.0 * 5 + 180.0))[0]                # inside band 5 (1875-2250 Hz)
    assert f.shape == (FE.N_FEAT["r2"],) == (24,)
    bands = f[10:]
    assert int(np.argmax(bands)) == 5
    assert np.allclose(f[:10], FE.r1(tone(375.0 * 5 + 180.0))[0])


def test_r3_peak_bin():
    k = 37
    f = FE.r3(tone(k * FS / N, phase=0.0))[0]
    assert f.shape == (256,)
    assert int(np.argmax(f)) == k - 1                    # feature index = bin - 1 (DC excluded)


def test_normalizer_uses_ddof0_and_guards_zero_std():
    F = np.array([[1.0, 5.0], [3.0, 5.0]], np.float32)
    n = FE.Normalizer.fit(F)
    assert n.std[0] == pytest.approx(1.0) and n.std[1] == 1.0
    assert np.allclose(n(F), [[-1.0, 0.0], [1.0, 0.0]])


def test_noise_snr():
    x = np.tile(tone(1000.0, amp=1.0), (64, 1))
    xn = FE.add_noise(x, 10.0, np.random.default_rng(1))
    snr = 10 * np.log10((x ** 2).mean() / ((xn - x) ** 2).mean())
    assert snr == pytest.approx(10.0, abs=0.3)


def test_r4_envelope_peak_at_modulation_rate():
    """A 3 kHz carrier, amplitude-modulated at envelope bin 10 (117.2 Hz, like a fault impact rate): R4's envelope
    spectrum must peak at bin 10, whatever the carrier; the first 9 features are R1 without the mean."""
    t = np.arange(N) / FS
    fm = 10 * 3000.0 / 256                                # envelope bin width = 3000/256 Hz
    x = ((1.0 + 0.8 * np.cos(2 * np.pi * fm * t)) * np.sin(2 * np.pi * 3000.0 * t + 0.4)).astype(np.float32)
    f = FE.r4(x)[0]
    assert f.shape == (FE.N_FEAT["r4"],) == (41,)
    assert np.allclose(f[:9], FE.r1(x)[0][1:])           # R1 without the mean
    env = f[9:]
    assert int(np.argmax(env)) == 10 - 1                  # feature env10 (k = 10) is index 9 of the 32
    assert env[9] > env[[2, 5, 15, 25]].max() + 0.5       # at least ~3x the off-rate bins
