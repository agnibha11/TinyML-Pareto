"""Checks on the committed results of the two Sprint-2 studies (fixed-point front ends, detect-then-diagnose cascade).

They guard the claims the paper and the slides make, and catch a regenerated result that silently changes them.
Each test skips when its result file is absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

RES = Path(__file__).resolve().parents[1] / "results"


def need(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"{p} not present")
    return p


def test_fx_bench_matches_pipeline_float_costs():
    """The fixed-point benchmark measures the float front end exactly as the pipeline's virtual-MCU run did."""
    s = pd.read_csv(need(RES / "fixedpoint" / "fx_cost_summary.csv")).set_index(["core", "region"]).cyc_med
    c = pd.read_csv(need(RES / "prehw_costs.csv")).set_index("model_id").fe_cyc_med
    for rep, model in (("R1", "dt-r1-d4-fp32"), ("R2", "dt-r2-d4-fp32"), ("R4", "dt-r4-d4-fp32")):
        assert abs(s[("m4f", f"F32_{rep}")] / c[model] - 1) < 0.01


def test_fx_core_dependent_design_rule():
    s = pd.read_csv(need(RES / "fixedpoint" / "fx_cost_summary.csv")).set_index(["core", "region"]).cyc_med
    assert s[("m4f", "FX_R1")] < s[("m4f", "F32_R1")]              # integer R1 wins even with an FPU
    assert s[("m4f", "HYB_R4")] < s[("m4f", "F32_R4")] < s[("m4f", "FX_R4_Q15")]
    for core in ("m4nofp", "m0p"):                                 # without an FPU, q15 wins everywhere
        for rep in ("R2", "R4"):
            assert s[(core, f"FX_{rep}_Q15")] * 5 < s[(core, f"F32_{rep}")]


def test_fx_exact_front_ends_reproduce_float():
    fid = pd.read_csv(need(RES / "fixedpoint" / "fx_fidelity.csv")).set_index(["rep", "front_end"])
    for key in (("r1", "int"), ("r2", "q31"), ("r2", "hyb"), ("r4", "hyb")):
        assert fid.loc[key, "p99_abs_z"] < 0.01
    s = json.loads(need(RES / "fixedpoint" / "fx_summary.json").read_text())
    for k, v in s["deploy"].items():
        assert abs(v["mean_dF1"]) < 0.002, k
    assert s["mlp_min_agreement"]["q31"] >= 0.99
    assert s["mlp_min_agreement"]["hyb"] >= 0.99


def test_cascade_keeps_accuracy_and_saves_energy():
    s = json.loads(need(RES / "cascade" / "summary.json").read_text())
    e = s["P3_either"]
    assert s["gates"]["P3_either"]["tpr_mean"] > 0.98
    assert e["dF1_mean"] > -0.005
    assert e["saving_median_pi0.01"] > 3
    assert e["best_E_pi0.01_uJ"] < e["best_E_single_uJ"] / 3
    assert e["best_E_pi1_reuse_uJ"] <= e["best_E_single_uJ"]      # never worse than the model alone, with reuse


def test_cascade_scored_every_stored_run():
    runs = pd.read_csv(need(RES / "cascade" / "cascade_runs.csv"))
    host = list((RES / "host").glob("*__P[23]__*.json"))
    if not host:
        pytest.skip("host results not present")
    assert runs[runs.gate == "either"].shape[0] == len(host)
