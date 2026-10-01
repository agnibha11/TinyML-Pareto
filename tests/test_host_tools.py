"""Host-side tests: dut.py against the real firmware core (compiled for the PC), measure.py dry run, contract formats.

    python -m pytest tests/ -q

The firmware tests compile firmware/bench/bench_core.cpp with g++ (firmware/tests/host_sim). They are skipped if no
C++ compiler is installed.
"""
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ml"))

from tools.dut import DUT, DUTError  # noqa: E402
from tools.measure import HW_COLUMNS, parse_w_line  # noqa: E402

def sync_edges(path: Path) -> list[int]:
    return [int(ln.split()[1]) for ln in path.read_text().splitlines() if ln.startswith("SYNC")]


def test_ping_list(board):
    dut, _ = board
    assert dut.ping() == {"bench": "sim", "n_models": 1}
    models = dut.list()
    assert [(m.k, m.model_id, m.runtime) for m in models] == [(0, "stub-noop", "stub")]


def test_errors_are_raised_not_guessed(board):
    dut, _ = board
    for cmd, code in [("RUN 10", "NOSEL"), ("LAT", "NOSEL"), ("SEL 7", "RANGE"), ("FOO", "UNKNOWN"),
                      ("RUN abc", "ARG"), ("RUN", "ARG"), ("PING 1", "ARG"), ("INPUT 5", "RANGE"),
                      ("IDLE 0", "ARG"), ("X" * 63, "UNKNOWN")]:
        with pytest.raises(DUTError, match=rf"ERR {code}"):
            dut.command(cmd)
    # an over-long line is rejected by the board, not truncated into a different command
    dut._ser.write(b"RUN " + b"9" * 80 + b"\n")
    assert dut._readline(time.monotonic() + 2).startswith("ERR ARG line longer")
    assert dut.ping()["bench"] == "sim"  # and the link is still in sync afterwards


def test_timeout_resyncs_link(board):
    """A host timeout must not leave a stale reply that answers the next command (CONTRACT.md 7.1)."""
    dut, _ = board
    with pytest.raises(DUTError, match="resynchronised"):
        dut.command("IDLE 1500", timeout_s=0.3)
    assert dut.idle(20)["us"] < 500_000          # not the stale 1.5 s reply
    assert dut.sel(0) == {"k": 0, "arena_used": 0}


def test_late_verify_payload_is_not_parsed_as_commands(board):
    dut, _ = board
    dut.sel(0)
    dut._ser.write(b"VERIFY\n")
    assert dut._readline(time.monotonic() + 2) == "RDY 4096"
    time.sleep(2.3)                               # board gives up after 2 s
    dut._ser.write(np.ones(1024, "<f4").tobytes())
    first = dut._readline(time.monotonic() + 3)
    assert first.startswith("ERR IO"), first
    time.sleep(0.3)
    assert dut._ser.in_waiting == 0               # no ERR lines from payload bytes
    assert dut.ping()["bench"] == "sim"


def test_unknown_command_wins_over_argument_errors(board):
    dut, _ = board
    with pytest.raises(DUTError, match="ERR UNKNOWN"):
        dut.command("FOO 1 2")


def test_windows_and_sync_pin(board):
    dut, log = board
    assert dut.sel(0) == {"k": 0, "arena_used": 0}
    assert dut.input(3) == {"j": 3}
    lat = dut.lat()
    assert lat["inf_min_cyc"] <= lat["inf_med_cyc"] <= lat["inf_max_cyc"]
    assert dut.lat_us == pytest.approx(lat["inf_med_cyc"] / 64)
    r = dut.run(200)
    assert r["n"] == 200 and r["us"] > 0 and 0 <= r["cls"] <= 9
    assert dut.front(50)["n"] == 50
    assert dut.e2e(50)["n"] == 50
    idle = dut.idle(120)
    assert 120_000 <= idle["us"] < 400_000
    assert dut.sleep(30)["us"] >= 30_000
    edges = sync_edges(log)
    assert edges == [1, 0] * 5, edges  # RUN, FRONT, E2E, IDLE, SLEEP: exactly one HIGH/LOW pair each


def test_verify_roundtrip(board):
    dut, _ = board
    dut.sel(0)
    w = np.zeros(1024, dtype=np.float32)
    w[620:640] = 3.0  # inside segment 6 (samples 612..713 of 10 segments x 102)
    cls, out = dut.verify(w)
    assert cls == 6
    assert out.shape == (10,) and out[6] == pytest.approx(20 * 9.0, rel=1e-5)
    assert dut.ping()["bench"] == "sim"


def test_stored_inputs_match_handoff(board):
    """The C array in firmware/bench/stored_inputs.cpp holds exactly the windows in handoff/test_vectors/."""
    dut, _ = board
    X = np.load(REPO / "handoff" / "test_vectors" / "stored_inputs.npy")
    assert X.shape == (5, 1024) and X.dtype == np.float32
    dut.sel(0)
    for j in range(5):
        dut.input(j)
        cls_board = dut.run(3)["cls"]
        cls_host, out = dut.verify(X[j])            # same window sent from the host
        seg = (X[j][:1020].astype(np.float64) ** 2).reshape(10, 102).sum(1)  # stub: energy per segment
        assert cls_board == cls_host == int(np.argmax(seg))
        np.testing.assert_allclose(out, seg, rtol=1e-4, atol=1e-6)


def test_n_for():
    assert DUT.n_for(10.0, 9.0) == 1_111_112
    assert DUT.n_for(10.0, 20_000_000) == 1


# ------------------------------------------------------------------------------------------------------------------
# measure.py and formats
# ------------------------------------------------------------------------------------------------------------------
def test_w_line_parser():
    w = parse_w_line("W rise_us=100 fall_us=10000100 n=9434 E_uJ=240012.5 Pmean_uW=24001.25 "
                     "Pstd_uW=150.2 Vbus_mV=4951.0 ovf=0")
    assert w.duration_us == 10_000_000 and w.ovf == 0
    with pytest.raises(ValueError):
        parse_w_line("W rise_us=1 fall_us=2")


def test_measure_dry_run_matches_contract(tmp_path):
    out = tmp_path / "hw.csv"
    subprocess.run([sys.executable, str(REPO / "tools" / "measure.py"), "--dry-run", "--out", str(out)],
                   check=True, capture_output=True)
    rows = list(csv.DictReader(open(out)))
    assert list(rows[0].keys()) == HW_COLUMNS
    contract = (REPO / "CONTRACT.md").read_text()
    m = re.search(r"### 6\.4 .*?\n\n`([^`]+)`", contract, re.S)
    assert m and [c.strip() for c in m.group(1).split(",")] == HW_COLUMNS
    assert {r["rejected"] for r in rows} <= {"0", "1"}
    ok = [r for r in rows if r["rejected"] == "0"]
    assert {r["mode"] for r in ok} == {"IDLE", "RUN", "FRONT", "E2E"}
    # every (model, input, mode) window: exactly one accepted row, or two rejected attempts (left for re-measurement)
    groups = {}
    for r in rows:
        groups.setdefault((r["model_id"], r["mode"]), []).append(r["rejected"])
    for (mid, mode), flags in groups.items():
        assert flags.count("0") <= 5 and len(flags) <= 10, (mid, mode, flags)
    assert len({r["model_id"] for r in ok}) == 3


def test_load_mat_uses_explicit_key(tmp_path):
    """99.mat holds X098 (a copy of 98.mat) next to X099; only X099 may be read."""
    import scipy.io
    import prepare_data as pdp
    f = tmp_path / "99.mat"
    scipy.io.savemat(f, {"X098_DE_time": np.full((10, 1), 98.0), "X099_DE_time": np.full((12, 1), 99.0),
                         "X099RPM": np.array([[1750]])})
    x, rpm, keys, _ = pdp.load_mat(f, 99)
    assert x.shape == (12,) and (x == 99.0).all() and rpm == 1750.0
    assert "X098_DE_time" in keys
    with pytest.raises(SystemExit, match="has no key X100_DE_time"):
        pdp.load_mat(f, 100)


def test_file_table_matches_classes_json():
    import prepare_data as pdp
    ft = pdp.file_table()
    assert len(ft) == 40 and ft.file_id.is_unique
    assert (ft[ft.fault_type == "N"].fs_original == 48_000).all()
    assert set(ft.class4) == {0, 1, 2, 3}
    assert ft.groupby("class10").load_hp.apply(list).map(lambda x: x == [0, 1, 2, 3]).all()
