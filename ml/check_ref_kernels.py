#!/usr/bin/env python3
"""How much do host scores depend on TFLite's kernel choice?

Host scores (ml/train.py) run the .tflite with TFLite's default optimised kernels; the board runs TFLM, whose
arithmetic equals TFLite's reference kernels (BUILTIN_REF). For every deployed NN (P2 fold 0, seed 0) this script
classifies the full P2 fold-0 test set both ways and reports class agreement and the macro-F1 difference.
Output: results/ref_kernel_check.csv
"""
from __future__ import annotations

import os

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as MD  # noqa: E402
from train import Data, tflite_predict  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    reg = pd.read_csv(REPO / "models" / "registry.csv", keep_default_na=False)
    rows, cache = [], {}
    for r in reg[reg.runtime == "tflm-cmsis"].itertuples(index=False):
        arch = MD.arch_of(r.model_id)
        if arch.rep not in cache:
            cache[arch.rep] = Data(arch.rep, "P2", 0)
        d = cache[arch.rep]
        X = d.shaped(d.X["test"])
        blob = (REPO / r.artifact).read_bytes()
        a, _ = tflite_predict(blob, X)
        b, _ = tflite_predict(blob, X, ref=True)
        y = d.y["test"]
        rows.append({"model_id": r.model_id, "n_test": len(y), "class_agree": float((a == b).mean()),
                     "f1_default": f1_score(y, a, average="macro"), "f1_reference": f1_score(y, b, average="macro")})
        print(rows[-1], flush=True)
    df = pd.DataFrame(rows)
    df["f1_diff"] = df.f1_reference - df.f1_default
    df.to_csv(REPO / "results" / "ref_kernel_check.csv", index=False)
    print(df.describe().to_string())


if __name__ == "__main__":
    main()
