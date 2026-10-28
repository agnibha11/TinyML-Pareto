"""ESP32 logger core (logger/logger_core.cpp) against CONTRACT.md 7.3/7.4 and tools/measure.py's W-line parser."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.measure import parse_w_line  # noqa: E402


@pytest.fixture(scope="module")
def core_sim(tmp_path_factory):
    if not shutil.which("g++"):
        pytest.skip("no C++ compiler")
    exe = tmp_path_factory.mktemp("logger") / "core_sim"
    subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Werror", f"-I{REPO / 'logger'}",
                    str(REPO / "logger" / "logger_core.cpp"), str(REPO / "logger" / "tests" / "core_sim.cpp"),
                    "-o", str(exe)], check=True)
    return exe


def run(exe, scenario):
    return subprocess.run([str(exe), scenario], capture_output=True, text=True, check=True).stdout.strip()


def test_constant_power_window(core_sim):
    w = parse_w_line(run(core_sim, "const"))
    assert w.n == 10000 and w.ovf == 0
    assert w.fall_us - w.rise_us == 10_000_000
    assert w.E_uJ == pytest.approx(100_000 * 10.0, rel=1e-9)        # 100 mW x 10 s
    assert w.Pmean_uW == pytest.approx(w.E_uJ / 10.0, rel=1e-9)     # E = Pmean x duration exactly
    assert w.Pstd_uW == pytest.approx(0.0, abs=1e-6)
    assert w.Vbus_mV == pytest.approx(5000.0)


def test_step_power_mean_and_std(core_sim):
    w = parse_w_line(run(core_sim, "step"))
    assert w.E_uJ == pytest.approx(49_000 * 5 + 147_000 * 5, rel=1e-9)
    assert w.Pmean_uW == pytest.approx(98_000, rel=1e-9)
    assert w.Pstd_uW == pytest.approx(49_000, rel=1e-6)              # half the samples at each level


def test_glitch_shorter_than_100ms_prints_nothing(core_sim):
    assert run(core_sim, "glitch") == "# no line"


@pytest.mark.parametrize("scenario,expected", [("ovf", 1), ("drop", 3)])
def test_overflow_and_drops_are_counted(core_sim, scenario, expected):
    assert parse_w_line(run(core_sim, scenario)).ovf == expected
