#!/usr/bin/env python3
"""Build the interactive model selector (docs/selector/index.html) from the script-written results.

The page is the selection rubric of the study as a tool: give it a RAM and flash budget, an inference rate, a battery
and a target life, pick which evaluation protocol to trust, and it returns the most accurate model that fits, every
other model with the reason it does not, and the error-energy plane with the energy budget drawn in.

Inputs (all written by scripts):
    results/prehw_costs.csv     per-model cost on the virtual Cortex-M4 (ml/stats.py)
    results/host_summary.csv    per-model, per-protocol macro-F1 mean/std over folds and seeds (ml/stats.py)
    results/headline_numbers.json   energy-model constants and run counts (ml/stats.py)
Output:
    docs/selector/index.html    one self-contained page (template tools/selector/template.html + embedded JSON)

Usage: python tools/make_selector.py
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "results"
TEMPLATE = REPO / "tools" / "selector" / "template.html"
OUT = REPO / "docs" / "selector" / "index.html"


def main() -> None:
    cost = pd.read_csv(RES / "prehw_costs.csv")
    summ = pd.read_csv(RES / "host_summary.csv", keep_default_na=False)
    summ = summ[summ.snr_db == ""]
    head = json.loads((RES / "headline_numbers.json").read_text())
    f1 = {(r.model_id, r.protocol): (round(float(r.f1_macro_mean), 4), round(float(r.f1_macro_std), 4), int(r.n_runs))
          for r in summ.itertuples(index=False)}
    models = []
    for r in cost.itertuples(index=False):
        if not all((r.model_id, p) in f1 for p in ("P1", "P2", "P3")):
            continue
        models.append({
            "id": r.model_id, "fam": r.family, "rep": r.input_rep, "prec": r.precision, "runtime": r.runtime,
            "ram": int(r.ram_peak_B), "flash": int(r.flash_model_B),
            "e2e": round(float(r.E_e2e_uJ), 4), "inf": round(float(r.E_inf_uJ), 4), "fe": round(float(r.E_fe_uJ), 4),
            "lat": round(float(r.lat_e2e_us), 2),
            "f1": {p: f1[(r.model_id, p)][:2] for p in ("P1", "P2", "P3")},
            "runs": sum(f1[(r.model_id, p)][2] for p in ("P1", "P2", "P3")),
        })
    data = {"models": models, "energy": head["energy_model"], "n_runs": head["n_runs"],
            "built": date.today().isoformat()}
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"{len(models)} models -> {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
