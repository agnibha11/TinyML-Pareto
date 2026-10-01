"""contract.py - CONTRACT.md in code: column lists, model-ID grammar and the class map, in one place.

Import these instead of retyping them:
    from tools.contract import REGISTRY_COLUMNS, HW_COLUMNS, parse_model_id

tests/test_contract.py checks that this module, CONTRACT.md and the repo files say the same thing. If you change a
schema, change CONTRACT.md, this file and the test in the same PR (and bump the contract version).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTRACT_VERSION = "1.0-rc2"

# §2
CLASSES_JSON = REPO / "handoff" / "classes.json"

# §4
FAMILIES = ("dt", "rf", "mlp", "cnn")
INPUT_REPS = ("r0", "r1", "r2", "r3")
PRECISIONS = ("fp32", "int8", "int8qat", "int8ref")
MODEL_ID_RE = re.compile(r"^(dt|rf|mlp|cnn)-(r[0-3])-([a-z0-9]+)-(fp32|int8|int8qat|int8ref)$")
_CONFIG_RE = {
    "dt": re.compile(r"^d\d+$"),
    "rf": re.compile(r"^t\d+d\d+$"),
    "mlp": re.compile(r"^h\d+(x\d+)?$"),
    "cnn": re.compile(r"^w\d{3}d\d+$"),
}

# §6
REGISTRY_COLUMNS = ["model_id", "family", "input_rep", "config", "precision", "runtime", "n_classes", "input_scale",
                    "input_zero_point", "output_scale", "output_zero_point", "params", "macs", "ops",
                    "protocol_trained", "fold", "seed", "artifact", "feasible"]
RUNTIMES = ("tflm-cmsis", "tflm-ref", "emlearn")
HOST_SUMMARY_COLUMNS = ["model_id", "protocol", "snr_db", "n_runs", "f1_macro_mean", "f1_macro_std",
                        "accuracy_mean", "accuracy_std"]
FW_METRICS_COLUMNS = ["model_id", "runtime", "bundle", "flash_model_B", "arena_used_B", "frontend_ram_B",
                      "ram_model_peak_B", "lat_inf_med_us", "lat_inf_min_us", "lat_inf_max_us", "lat_fe_med_us",
                      "git_hash"]
HW_COLUMNS = ["model_id", "session", "input_j", "mode", "N", "t_window_us_dut", "t_window_us_logger",
              "E_window_uJ", "P_mean_uW", "P_std_uW", "V_bus_mV", "n_samples", "ovf", "P_idle_uW",
              "fw_hash", "logger_hash", "timestamp", "rejected"]
HW_MODES = ("IDLE", "SLEEP", "RUN", "FRONT", "E2E")
SESSION_COLUMNS = ["session", "date", "room_temp_C", "usb_port", "cable", "shunt_ohm_measured",
                   "logger_hash", "fw_hash", "notes"]

# §7
SERIAL_COMMANDS = ("PING", "LIST", "SEL", "INPUT", "IDLE", "SLEEP", "RUN", "FRONT", "E2E", "LAT", "VERIFY")
ERR_CODES = ("UNKNOWN", "ARG", "NOSEL", "RANGE", "ALLOC", "INVOKE", "IO", "NOTIMPL")
SYNC_PIN = "D2"
LOGGER_SYNC_GPIO = 4
MIN_WINDOW_S = 10.0
MAX_DURATION_MISMATCH = 0.001


def parse_model_id(model_id: str) -> dict[str, str]:
    """Split a model ID into its four tokens; raise ValueError if it breaks §4."""
    m = MODEL_ID_RE.match(model_id)
    if not m:
        raise ValueError(f"model_id {model_id!r} does not match {MODEL_ID_RE.pattern}")
    family, input_rep, config, precision = m.groups()
    if not _CONFIG_RE[family].match(config):
        raise ValueError(f"model_id {model_id!r}: size token {config!r} is not valid for family {family!r}")
    if family in ("dt", "rf") and (input_rep == "r0" or precision != "fp32"):
        raise ValueError(f"model_id {model_id!r}: trees take features (r1-r3) and are fp32 in the main sweep")
    if family == "cnn" and input_rep != "r0":
        raise ValueError(f"model_id {model_id!r}: CNNs take the raw window (r0)")
    if family == "mlp" and input_rep == "r0":
        raise ValueError(f"model_id {model_id!r}: MLPs take features (r1-r3)")
    if precision == "int8ref" and family != "cnn":
        raise ValueError(f"model_id {model_id!r}: int8ref (reference-kernel ablation) exists only for CNNs")
    return {"family": family, "input_rep": input_rep, "config": config, "precision": precision}


def c_name(model_id: str) -> str:
    """C identifier for a model ID (CONTRACT.md 4): '-' -> '_' (e.g. dt_r2_d8_fp32)."""
    parse_model_id(model_id)
    return model_id.replace("-", "_")


def class_names() -> list[str]:
    """class10 names in label order, from the frozen handoff/classes.json."""
    spec = json.loads(CLASSES_JSON.read_text())
    return [c["name"] for c in sorted(spec["class10"], key=lambda c: c["id"])]


def validate_registry(path: Path = REPO / "models" / "registry.csv") -> list[str]:
    """Return a list of problems in models/registry.csv (empty list = valid)."""
    problems = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REGISTRY_COLUMNS:
            return [f"header {reader.fieldnames} != contract {REGISTRY_COLUMNS}"]
        seen = set()
        for i, row in enumerate(reader, start=2):
            mid = row["model_id"]
            if mid in seen:
                problems.append(f"line {i}: duplicate model_id {mid}")
            seen.add(mid)
            try:
                tok = parse_model_id(mid)
            except ValueError as e:
                problems.append(f"line {i}: {e}")
                continue
            for k, v in tok.items():
                if row[k] != v:
                    problems.append(f"line {i}: {k}={row[k]!r} but model_id says {v!r}")
            if row["runtime"] not in RUNTIMES:
                problems.append(f"line {i}: runtime {row['runtime']!r} not in {RUNTIMES}")
            if row["feasible"] not in ("0", "1"):
                problems.append(f"line {i}: feasible must be 0 or 1")
    return problems
