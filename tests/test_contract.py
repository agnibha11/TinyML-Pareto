"""CONTRACT.md must agree with the code and the committed files. One test per M0 checklist item.

If one of these fails, the contract and the code have drifted apart. Fix whichever one is wrong in the same PR.
"""
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ml"))

from tools import contract as C  # noqa: E402
from tools.dut import DUTError  # noqa: E402
from tools.measure import parse_w_line  # noqa: E402

TEXT = (REPO / "CONTRACT.md").read_text()


def section(heading_prefix: str) -> str:
    """Text of one '## n.' or '### n.m' section of CONTRACT.md, up to the next heading of the same or higher level."""
    level = heading_prefix.split()[0]
    m = re.search(rf"^{re.escape(heading_prefix)}.*?$(.*?)(?=^#{{1,{len(level)}}} |\Z)", TEXT, re.S | re.M)
    assert m, f"section {heading_prefix!r} not found in CONTRACT.md"
    return m.group(1)


def first_code_list(sec: str) -> list[str]:
    m = re.search(r"^`([^`\n]+)`\s*$", sec, re.M)
    assert m, "no backtick column list in section"
    return [c.strip() for c in m.group(1).split(",")]


# 1. Serial protocol ------------------------------------------------------------------------------------------------
def contract_commands() -> list[str]:
    return re.findall(r"^\| `([A-Z0-9]+)(?: <[A-Za-z]+>)?` \|", section("### 7.2"), re.M)


def test_command_list_matches_code():
    assert tuple(contract_commands()) == C.SERIAL_COMMANDS
    core = (REPO / "firmware" / "bench" / "bench_core.cpp").read_text()
    for cmd in C.SERIAL_COMMANDS:
        assert f'"{cmd}"' in core, f"{cmd} in CONTRACT.md but not dispatched in bench_core.cpp"


def test_error_codes_used_by_firmware_are_in_contract():
    core = (REPO / "firmware" / "bench" / "bench_core.cpp").read_text()
    used = set(re.findall(r'"ERR ([A-Z]+)', core))
    assert used and used <= set(C.ERR_CODES), used - set(C.ERR_CODES)
    listed = set(re.findall(r"`([A-Z]+)` \(", section("### 7.1")))
    assert listed == set(C.ERR_CODES)


def test_every_contract_command_runs_on_the_firmware_core(board):
    dut, _ = board
    dut.sel(0)
    args = {"SEL": "0", "INPUT": "1", "IDLE": "20", "SLEEP": "20", "RUN": "5", "FRONT": "5", "E2E": "5"}
    for cmd in contract_commands():
        if cmd == "VERIFY":
            cls, out = dut.verify(np.zeros(1024, np.float32))
            assert 0 <= cls < len(out)
            continue
        kv, _ = dut.command(f"{cmd} {args[cmd]}" if cmd in args else cmd, timeout_s=10, allow_extra=cmd == "LIST")
        assert isinstance(kv, dict)


def test_board_is_silent_unless_asked(board):
    """§7.1: no banner, no unsolicited output."""
    import time
    dut, _ = board
    time.sleep(0.3)
    assert dut._ser.in_waiting == 0
    dut.sel(0)
    dut.run(10)
    time.sleep(0.3)
    assert dut._ser.in_waiting == 0


# 4. Classes ---------------------------------------------------------------------------------------------------------
def test_class_table_matches_classes_json():
    rows = re.findall(r"^\| (\d) \| (\w+) \| (\d) \| (\w+) \| ([\d.]+) \| ([\d, ]+) \|$", section("## 2."), re.M)
    assert len(rows) == 10
    spec = json.loads(C.CLASSES_JSON.read_text())
    c4 = {c["name"]: c["id"] for c in spec["class4"]}
    for (cid, name, k4, ft, size, files), c in zip(rows, spec["class10"]):
        assert int(cid) == c["id"] and name == c["name"] and ft == c["fault_type"]
        assert int(k4) == c4[ft] and float(size) == c["fault_size_in"]
        assert [int(x) for x in files.split(",")] == c["cwru_files"]
    assert C.class_names()[:3] == ["N", "IR007", "B007"]


# 5. Registry schema -------------------------------------------------------------------------------------------------
def test_registry_schema():
    assert first_code_list(section("### 6.1")) == C.REGISTRY_COLUMNS
    assert C.validate_registry() == []


def test_registry_validator_catches_mistakes(tmp_path):
    p = tmp_path / "registry.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(C.REGISTRY_COLUMNS)
        good = dict.fromkeys(C.REGISTRY_COLUMNS, "")
        good.update(model_id="dt-r2-d8-fp32", family="dt", input_rep="r2", config="d8", precision="fp32",
                    runtime="emlearn", feasible="1")
        w.writerow([good[c] for c in C.REGISTRY_COLUMNS])
        bad = dict(good, model_id="dt-r2-d8-int8", config="d8", precision="int8", runtime="tflm")
        w.writerow([bad[c] for c in C.REGISTRY_COLUMNS])
    problems = C.validate_registry(p)
    assert len(problems) == 1 and "line 3" in problems[0]


# 6. Model-ID format -------------------------------------------------------------------------------------------------
def test_model_id_examples_from_contract_parse():
    examples = re.findall(r"`([a-z0-9]+-r[0-3]-[a-z0-9]+-[a-z0-9]+)`", section("## 4."))
    assert len(examples) >= 5
    for mid in examples:
        C.parse_model_id(mid)


@pytest.mark.parametrize("bad", ["DT-r2-d8-fp32", "dt-r2-d8", "dt-r0-d8-fp32", "rf-r1-t10d6-int8",
                                 "cnn-r2-w050d4-int8", "mlp-r2-32x32-int8", "cnn-r0-w50d4-int8",
                                 "cnn-r0-w050d4-fp16", "mlp-r0-h32-fp32", "mlp-r2-h32-int8ref"])
def test_bad_model_ids_rejected(bad):
    with pytest.raises(ValueError):
        C.parse_model_id(bad)


def test_c_name():
    assert C.c_name("cnn-r0-w050d4-int8qat") == "cnn_r0_w050d4_int8qat"
    assert "g_model_<c_name>" in section("## 4.") and "<c_name>_predict" in section("### 7.5")


# 7. Results CSV schema ----------------------------------------------------------------------------------------------
def test_results_schemas_match_contract():
    assert first_code_list(section("### 6.3")) == C.FW_METRICS_COLUMNS
    assert first_code_list(section("### 6.4")) == C.HW_COLUMNS
    assert first_code_list(section("### 6.5")) == C.SESSION_COLUMNS
    host = re.search(r"`results/host_summary.csv`: `([^`]+)`", section("### 6.2")).group(1)
    assert [c.strip() for c in host.split(",")] == C.HOST_SUMMARY_COLUMNS
    modes = re.findall(r"`([A-Z0-9]+)`", re.search(r"`mode` ∈ ([^.]+)\.", section("### 6.4")).group(1))
    assert tuple(modes) == C.HW_MODES


# 8. Logger window line ----------------------------------------------------------------------------------------------
def test_logger_example_line_parses():
    sec = section("### 7.4")
    fmt = re.search(r"^(W rise_us=.*)$", sec, re.M).group(1)
    keys = re.findall(r"(\w+)=", fmt)
    assert keys == ["rise_us", "fall_us", "n", "E_uJ", "Pmean_uW", "Pstd_uW", "Vbus_mV", "ovf"]
    example = re.search(r"Example \(values made up\): `([^`]+)`", sec).group(1)
    w = parse_w_line(example)
    assert w.duration_us == 10_003_207 and w.ovf == 0


# 9. Handoff directory -----------------------------------------------------------------------------------------------
def test_committed_handoff_files_are_in_layout():
    layout = section("## 5.")
    for f in (REPO / "handoff").rglob("*"):
        if f.is_file():
            assert f.name in layout or f.stem in layout, f"{f.relative_to(REPO)} is not described in CONTRACT.md §5"


# 2/3. Sync pin + phases (behaviour) ---------------------------------------------------------------------------------
def test_sync_pin_only_around_measured_loops(board):
    dut, log = board
    dut.sel(0)
    dut.ping()
    dut.list()
    dut.lat()                      # timed with DWT, not a D2 window
    with pytest.raises(DUTError):
        dut.command("RUN 0")       # rejected before D2 moves
    dut.run(5)
    edges = [ln.split()[1] for ln in log.read_text().splitlines() if ln.startswith("SYNC")]
    assert edges == ["1", "0"]


def test_contract_version_consistent():
    assert f"v{C.CONTRACT_VERSION}" in TEXT.splitlines()[2]
