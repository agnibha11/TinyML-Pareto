#!/usr/bin/env python3
"""Build the interactive model selector, the project website (docs/index.html), from measured board results.

The page is TrueJoule's selection rule as a tool: pick a dataset and the evaluation protocol to trust, give it a RAM and
flash budget, an inference rate, a battery and a target life, and it returns the most accurate model that fits, every
other model with the reason it does not, and the accuracy-energy plane with the energy budget drawn in.

Every energy and latency on the page is measured on the Arduino Nano 33 BLE with the INA219/ESP32 rig (board level at
the 5 V USB input, idle-subtracted, mean of three repeats; int8 models from the patched, bit-exact TFLM library). The
rule is the paper's (paper/latex/scripts/make_numbers.py, energy_table), imported rather than re-implemented.

Inputs (all written by scripts):
    results/<ds>/hardware/hw_measurements.csv     board energy windows (tools/measure.py)
    results/<ds>/hardware/fw_build.csv            model flash on the board (tools/build_flash.py)
    results/<ds>/hardware/board_verify*.csv       board-vs-host output check (tools/verify_board.py)
    results/<ds>/analysis/host_summary.csv        macro-F1 per protocol over folds and seeds (ml/stats.py, ml/pu_analysis.py)
    results/cwru/analysis/prehw_costs.csv         peak RAM and the chip-level estimate shown for comparison (ml/stats.py)
    results/cwru/analysis/headline_numbers.json   datasheet chip-sleep power (ml/stats.py)
    results/<ds>/cascade_hw/analysis/*            measured gate energy and savings (ml/cascade_hw.py)
    paper/latex/scripts/cascade_rates.json        gate detection rates on the deployed split (from docs/cascade_preregistration.md)
    results/<ds>/hardware/logfix/hw_measurements.csv  same models rebuilt with a float-only log10 (FE_FLOAT_LOG)
    results/<ds>/cascade_hw/cascade_measurements.csv   measured board sleep power (local only; optional)
Output:
    docs/index.html             one self-contained page (template tools/selector/template.html + embedded JSON)
    docs/selector/index.html    redirect for the old /selector/ link

Usage: python tools/make_selector.py      (after paper/latex/scripts/make_numbers.py, which it shares a rule with)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "paper" / "latex" / "scripts"))
from make_numbers import energy_table, parse  # noqa: E402  (same measurement rule as the paper)

RES = REPO / "results"
TEMPLATE = REPO / "tools" / "selector" / "template.html"
OUT = REPO / "docs" / "index.html"
OLD = REPO / "docs" / "selector" / "index.html"
REDIRECT = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Bearing Sensor Model Selector</title>
<meta http-equiv="refresh" content="0; url=../">
<link rel="canonical" href="../">
<script>location.replace("../" + location.hash)</script></head>
<body><p>The model selector moved to <a href="../">the site root</a>.</p></body></html>
"""
PROTOCOLS = {"cwru": ["P3", "P2", "P1"], "pu": ["P3", "P4", "P2", "P1"]}
LENS = {
    "cwru": {"P3": "Bearing-disjoint · one fault size (the physical bearing) held out · 4 classes",
             "P2": "Recording-disjoint · one motor load held out · 10 classes",
             "P1": "Random windows · overlapping windows on both sides, optimistic · 10 classes"},
    "pu": {"P3": "Bearing-disjoint · a quarter of the bearings of each class held out · 3 classes",
           "P4": "Artificial → real damage · trained on artificial, tested on real damage · 3 classes",
           "P2": "Recording-disjoint · one operating condition held out · 3 classes",
           "P1": "Random windows · recordings shared between train and test, optimistic · 3 classes"},
}
LABEL = {"P3": "Bearing", "P4": "Art→real", "P2": "Recording", "P1": "Random"}


def f1_table(ds: str) -> dict:
    hs = pd.read_csv(RES / ds / "analysis" / "host_summary.csv")
    if ds == "cwru":
        hs = hs[hs.snr_db.isna()].rename(columns={"f1_macro_mean": "f1", "f1_macro_std": "sd"})
    else:
        hs = hs.rename(columns={"f1_mean": "f1", "f1_std": "sd"})
    return {(r.model_id, r.protocol): (round(float(r.f1), 4), round(float(r.sd), 4), int(r.n_runs))
            for r in hs.itertuples(index=False)}


def sleep_mW(ds: str):
    p = RES / ds / "cascade_hw" / "cascade_measurements.csv"
    if not p.exists():
        return None
    c = pd.read_csv(p)
    s = c[(c["mode"] == "SLEEP") & (c.rejected == 0)]
    return round(float(s.P_mean_uW.median()) / 1e3, 2) if len(s) else None


def main() -> None:
    pre = pd.read_csv(RES / "cwru" / "analysis" / "prehw_costs.csv").set_index("model_id")
    head = json.loads((RES / "cwru" / "analysis" / "headline_numbers.json").read_text())
    rates = json.loads((REPO / "paper" / "latex" / "scripts" / "cascade_rates.json").read_text())
    data = {"built": date.today().isoformat(), "datasets": {}}
    total_windows = 0
    for ds in ("cwru", "pu"):
        wide, st = energy_table(RES / ds / "hardware" / "hw_measurements.csv", [1, 2, 3], [5, 6, 7])
        total_windows += st["n_windows"]
        fb = pd.read_csv(RES / ds / "hardware" / "fw_build.csv").set_index("model_id")
        ver = {}
        for name in ("board_verify.csv", "board_verify_int8v2.csv"):     # later file wins: patched library
            p = RES / ds / "hardware" / name
            if p.exists():
                for r in pd.read_csv(p).itertuples(index=False):
                    ver[r.model_id] = bool(r.passed)
        # float-only log10 build (same models, FE_FLOAT_LOG firmware), where measured
        lf = {}
        lfp = RES / ds / "hardware" / "logfix" / "hw_measurements.csv"
        if lfp.exists():
            lw, _ = energy_table(lfp, [1, 2, 3], [1, 2, 3])
            lfv = {}
            vp = RES / ds / "hardware" / "logfix" / "board_verify.csv"
            if vp.exists():
                for r in pd.read_csv(vp).itertuples(index=False):      # last attempt per model counts
                    lfv[r.model_id] = bool(r.passed)
            for r in lw.itertuples(index=False):
                if lfv.get(r.model_id, True):
                    lf[r.model_id] = {"e2e": round(float(r.E_E2E), 3), "fe": round(float(r.E_FRONT), 3),
                                      "lat": round(float(r.t_us_E2E), 1)}
        f1 = f1_table(ds)
        prots = PROTOCOLS[ds]
        models = []
        for r in wide.itertuples(index=False):
            if not all((r.model_id, p) in f1 for p in prots):
                continue
            meta = parse(r.model_id)
            models.append({
                "id": r.model_id, "fam": meta["family"].lower(), "rep": meta["rep"].lower(), "prec": meta["prec"],
                "ram": int(pre.loc[r.model_id, "ram_peak_B"]), "flash": int(fb.loc[r.model_id, "flash_model_B_board"]),
                "e2e": round(float(r.E_E2E), 3), "fe": round(float(r.E_FRONT), 3), "inf": round(float(r.E_RUN), 3),
                "lat": round(float(r.t_us_E2E), 1), "est": round(float(pre.loc[r.model_id, "E_e2e_uJ"]), 3),
                "cv": round(float(r.cv_E2E), 4), "ok": ver.get(r.model_id, False),
                "f1": {p: f1[(r.model_id, p)][:2] for p in prots},
                "runs": sum(f1[(r.model_id, p)][2] for p in prots),
            })
            if r.model_id in lf:
                models[-1]["lf"] = lf[r.model_id]
        # measured gate: CWRU G5 (self-calibrated, n = 16), Paderborn G6 (self-calibrated, n = 128)
        g = pd.read_csv(RES / ds / "cascade_hw" / "analysis" / "rq2_stage1.csv").set_index("gate")
        summ = json.loads((RES / ds / "cascade_hw" / "analysis" / "summary.json").read_text())
        save = summ.get("rq6_saving_uJ", {})
        if ds == "cwru":
            gid, (tpr, fpr) = 5, rates["cwru_board_split"]["G5n16"]
            reuse = {"r1": None, "r2": None, "r4": None}
            r1part = sum(save.values()) / len(save)
            reuse = {k: round(r1part, 2) for k in reuse}
            split = "CWRU P2 fold 0 (unseen load)"
        else:
            gid = 6
            tpr, fpr = rates["pu_G6"]["n128"]["p3"]
            r2 = [v for k, v in save.items() if "-r2-" in k]
            r14 = [v for k, v in save.items() if "-r2-" not in k]
            reuse = {"r1": round(sum(r14) / len(r14), 2), "r4": round(sum(r14) / len(r14), 2),
                     "r2": round(sum(r2) / len(r2), 2)}
            split = "Paderborn P3 fold 0 (unseen bearings)"
        data["datasets"][ds] = {
            "name": "CWRU" if ds == "cwru" else "Paderborn",
            "protocols": prots, "label": {p: LABEL[p] for p in prots}, "lens": {p: LENS[ds][p] for p in prots},
            "models": models, "windows": st["n_windows"], "idle_mW": round(st["idle_med"], 2), "sleep_mW": sleep_mW(ds),
            "cv_med": round(st["cv_med"], 4),
            "gate": {"name": g.loc[gid, "name"], "E1": round(float(g.loc[gid, "mean"]), 3), "tpr": tpr, "fpr": fpr,
                     "reuse": reuse, "split": split},
        }
    data["chip_sleep_uW"] = head["energy_model"]["P_sleep_chip_uW"]
    data["windows"] = total_windows
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    OLD.parent.mkdir(parents=True, exist_ok=True)
    OLD.write_text(REDIRECT)
    n = sum(len(d["models"]) for d in data["datasets"].values())
    print(f"{n} model entries -> {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
