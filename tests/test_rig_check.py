"""tools/rig_check.py against its simulated rig: every wiring fault must be caught by the right check, and a
correctly wired rig must pass.  python -m pytest tests/test_rig_check.py -q"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import rig_check as rc  # noqa: E402

# fault -> (name prefix of the check that must FAIL, or None if only a WARN is expected)
EXPECTED = {
    "no_nano": "1 USB: Nano",
    "flaky_data": "2 Nano link",
    "swapped": "5 Sync + INA219",
    "r100": "5 Sync + INA219",
    "no_i2c": "5 Sync + INA219",
    "no_sync": "5 Sync + INA219",
    "floating_sync": "4 Sync idle",
    "loose": "6 Repeatability",
    "bypass": "7 Activity",
    "wrong_build": "3 Logger",
}


def run(fault):
    rep = rc.Report(color=False)
    sim = rc.SimRig(fault)
    dmm = 1.012 if fault == "wrong_build" else None
    rc.run_normal(rep, sim, sim, sim.ports(), 1.0, dmm, quick=True)
    return rep


def test_good_rig_passes():
    rep = run("ok")
    assert not rep.failed
    assert all(c.status == "PASS" for c in rep.checks)


@pytest.mark.parametrize("fault,check", sorted(EXPECTED.items()))
def test_fault_is_caught_by_the_right_check(fault, check):
    rep = run(fault)
    fails = [c.name for c in rep.checks if c.status == "FAIL"]
    assert fails and fails[0].startswith(check), (fault, fails)


def test_low_vbus_warns():
    rep = run("low_vbus")
    assert not rep.failed
    assert any(c.status == "WARN" and "Vbus" in c.detail for c in rep.checks)


@pytest.mark.parametrize("fault,gain_ok", [("ok", True), ("r100", False)])
def test_calibration(fault, gain_ok):
    rep = rc.Report(color=False)
    rc.run_cal(rep, rc.SimCal(fault, 998.7), 998.7, 1.0, 3, 1, ask=lambda m: None)
    gain = next(c for c in rep.checks if c.name == "cal gain")
    assert (gain.status == "PASS") == gain_ok


def test_w_line_parser_shared_with_measure():
    w = rc.parse_w_line("W rise_us=1 fall_us=2000001 n=1999 E_uJ=110000.0 Pmean_uW=55000.0 Pstd_uW=550.0 "
                        "Vbus_mV=4960.0 ovf=0")
    assert w.duration_us == 2_000_000 and w.Pmean_uW / w.Vbus_mV == pytest.approx(11.09, rel=1e-3)
