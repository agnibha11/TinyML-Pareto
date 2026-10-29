"""Virtual rig (tools/vrig/vrig_sim.cpp + the real logger core): an exact, noise-free window must integrate to the
analytic energy, within the INA219 bus-voltage LSB and the unsampled tail before the falling edge."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.measure import parse_w_line  # noqa: E402


@pytest.fixture(scope="module")
def vrig(tmp_path_factory):
    if not shutil.which("g++"):
        pytest.skip("no C++ compiler")
    exe = tmp_path_factory.mktemp("vrig") / "vrig_sim"
    subprocess.run(["g++", "-std=c++17", "-O2", f"-I{REPO / 'logger'}", str(REPO / "logger" / "logger_core.cpp"),
                    str(REPO / "tools" / "vrig" / "vrig_sim.cpp"), "-o", str(exe)], check=True)
    return exe


def test_exact_window_energy(vrig):
    # 10 s at 15 mA through 1 ohm from 5 V: the board sees 4.985 V -> 74.775 mW -> 747,750 uJ
    out = subprocess.run([str(vrig)], input="10000000 1 1.0 5.0 0.010 0.005 0 0 0 0 1064 0\n",
                         capture_output=True, text=True, check=True).stdout.strip()
    w = parse_w_line(out)
    assert w.ovf == 0 and 9390 <= w.n <= 9400
    assert w.E_uJ == pytest.approx(747_750.0, rel=5e-4)


def test_overflow_flag_beyond_40mV(vrig):
    out = subprocess.run([str(vrig)], input="1000000 1 1.0 5.0 0.045 0 0 0 0 0 1064 0\n",
                         capture_output=True, text=True, check=True).stdout.strip()
    assert parse_w_line(out).ovf > 0              # 45 mA x 1 ohm = 45 mV > the PGA /1 range: rejected window
