"""Claims of the Paderborn (second dataset) analysis, checked against results/pu/ (ml/pu_analysis.py)."""
import json
from pathlib import Path

import pytest

RES = Path(__file__).resolve().parents[1] / "results" / "pu"
pytestmark = pytest.mark.skipif(not (RES / "summary.json").exists(), reason="run ml/pu_analysis.py first")


def S():
    return json.loads((RES / "summary.json").read_text())


def test_every_run_is_present():
    assert len(list((RES / "host").glob("*.json"))) == 88 * 10 * 5


def test_leakage_collapse_replicates():
    s = S()
    assert s["P1"]["best_f1"] > s["P2"]["best_f1"] > s["P3"]["best_f1"] > s["P4"]["best_f1"] > 1 / 3
    assert s["tau"]["PU P1|PU P3"] < 0.3


def test_envelope_front_end_best_on_unseen_bearings():
    s = S()
    assert max(s["P3"]["reps"], key=s["P3"]["reps"].get) == "r4"
    assert s["transfer"]["best_rep_cwru_P3"] == "r4"


def test_model_ranking_does_not_transfer():
    s = S()
    assert s["tau"]["CWRU P3|PU P3"] < 0.3
    assert s["transfer"]["cwru_P3_best_rank_pu_P3"] > 10


def test_cascade_saves_nothing_without_losing_faults():
    c = S()["cascade"]
    assert c["P3_either"]["fpr"] > 0.95                      # recall-tuned gate escalates (almost) everything
    assert c["P3_healthy"]["best_f1_cascade"] < c["P3_healthy"]["best_f1_single"] - 0.2
