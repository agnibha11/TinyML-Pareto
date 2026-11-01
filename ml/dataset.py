"""Which dataset the host pipeline runs on, chosen with the environment variable TINYML_DATASET.

    TINYML_DATASET=cwru   (default) CWRU, exactly as before: data/processed, splits/, results/host, build/deploy
    TINYML_DATASET=pu     Paderborn (ml/pu_data.py): data/processed/pu, splits/pu, results/pu/host, build/pu/deploy

features.py, splits.load_split and train.py read their paths, protocols and label columns from here, so the same
training code runs on both datasets and nothing written for CWRU is ever overwritten by a Paderborn run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NAME = os.environ.get("TINYML_DATASET", "cwru").strip().lower()

if NAME == "cwru":
    PROCESSED = REPO / "data" / "processed"
    SPLITS = REPO / "splits"
    RESULTS = REPO / "results"
    BUILD = REPO / "build"
    PROTOCOLS = {"P1": 1, "P2": 4, "P3": 3}
    LABEL = {"P1": "class10", "P2": "class10", "P3": "class4"}
    N_CLASSES = {"class10": 10, "class4": 4}
    DEPLOYED = ("P2", 0, 0)
elif NAME == "pu":
    PROCESSED = REPO / "data" / "processed" / "pu"
    SPLITS = REPO / "splits" / "pu"
    RESULTS = REPO / "results" / "pu"
    BUILD = REPO / "build" / "pu"
    # P1 leaky | P2 leave one operating condition out | P3 bearing-disjoint group k-fold | P4 artificial -> real damage.
    # The fold counts are written by ml/pu_data.py (P3 depends on how many bearings were prepared).
    _pj = SPLITS / "protocols.json"
    PROTOCOLS = json.loads(_pj.read_text())["folds"] if _pj.exists() else {"P1": 1, "P2": 4, "P3": 4, "P4": 1}
    LABEL = {p: "class3" for p in PROTOCOLS}
    N_CLASSES = {"class3": 3}
    DEPLOYED = ("P3", 0, 0)
else:
    raise SystemExit(f"TINYML_DATASET={NAME!r}: expected 'cwru' or 'pu'")

HOST = RESULTS / "host"
DEPLOY = BUILD / "deploy"
