"""Cascade commands (CONTRACT.md 7.6) on the PC simulator, against the host export in handoff/cascade/cwru/.

The cascade simulator links firmware/bench/cascade.c and frontend_fx.c with the board's own Arduino_CMSIS-DSP sources
(firmware/tests/host_sim/Makefile, bench_sim_cascade); without that library the tests that need it are skipped.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import contract as C  # noqa: E402
from tools.dut import DUT, DUTError, fnv1a  # noqa: E402

SIM_DIR = REPO / "firmware" / "tests" / "host_sim"
CASC = REPO / "handoff" / "cascade" / "cwru"
CMSIS = Path(os.environ.get("CMSIS_DSP", Path.home() / "Arduino/libraries/Arduino_CMSIS-DSP/src"))
STREAMS = ("S0", "S2", "S10", "S100", "ST")


def build_sim(tmp_path_factory, ds: str) -> Path:
    if not (CMSIS / "arm_math.h").exists() or not shutil.which("make") or not shutil.which("gcc"):
        pytest.skip("no Arduino_CMSIS-DSP sources (set CMSIS_DSP) or no C toolchain")
    if not (REPO / "handoff" / "cascade" / ds / "pool_int16.npy").exists():
        pytest.skip(f"no cascade export for {ds}")
    d = tmp_path_factory.mktemp(f"casc_{ds}")
    hdr = d / "cascade_data.h"
    subprocess.run([sys.executable, str(REPO / "tools" / "bundle.py"), "--dataset", ds, "--cascade-data", str(hdr)],
                   check=True, capture_output=True)
    target = "bench_sim_cascade" + ("_pu" if ds == "pu" else "")
    defs = ["CASC_DEFS=-DCASC_NG_MAX=23 -DCASC_CAL_MAX=128 -DCASC_HAS_G6_STUB"] if ds == "pu" else []
    subprocess.run(["make", "-s", "-C", str(SIM_DIR), target, f"CASC_DATA={hdr}", f"CMSIS_DSP={CMSIS}", *defs],
                   check=True)
    out = d / target
    shutil.copy(SIM_DIR / target, out)
    out.chmod(0o755)
    return out


@pytest.fixture(scope="session")
def casc_binary(tmp_path_factory):
    return build_sim(tmp_path_factory, "cwru")


@pytest.fixture(scope="session")
def casc_binary_pu(tmp_path_factory):
    return build_sim(tmp_path_factory, "pu")


def serve(binary: Path, tmp_path: Path):
    err = open(tmp_path / "sync.log", "w")
    proc = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=err, text=True)
    line = proc.stdout.readline().strip()
    assert line.startswith("PTY "), line
    return proc, err, DUT(line.split()[1], open_timeout_s=0.1)


@pytest.fixture()
def pboard(casc_binary_pu, tmp_path):
    proc, err, dut = serve(casc_binary_pu, tmp_path)
    yield dut, tmp_path / "sync.log"
    dut.close()
    proc.kill()
    proc.wait()
    err.close()


@pytest.fixture()
def cboard(casc_binary, tmp_path):
    err = open(tmp_path / "sync.log", "w")
    proc = subprocess.Popen([str(casc_binary)], stdout=subprocess.PIPE, stderr=err, text=True)
    line = proc.stdout.readline().strip()
    assert line.startswith("PTY "), line
    dut = DUT(line.split()[1], open_timeout_s=0.1)
    yield dut, tmp_path / "sync.log"
    dut.close()
    proc.kill()
    proc.wait()
    err.close()


def edges(log: Path) -> list[str]:
    return [ln.split()[1] for ln in log.read_text().splitlines() if ln.startswith("SYNC")]


# ---- contract <-> code ------------------------------------------------------------------------------------------
def test_cascade_commands_match_contract_and_firmware():
    text = (REPO / "CONTRACT.md").read_text()
    sec = text[text.index("### 7.6"):text.index("## 8.")]
    import re
    listed = tuple(re.findall(r"^\| `([A-Z]+)(?: <[A-Za-z]+>)*` \|", sec, re.M))
    assert listed == C.CASCADE_COMMANDS
    core = (REPO / "firmware" / "bench" / "bench_core.cpp").read_text()
    casc = (REPO / "firmware" / "bench" / "bench_cascade.cpp").read_text()
    for cmd in C.CASCADE_COMMANDS:
        assert f'"{cmd}"' in core and f'{{"{cmd}",' in casc, cmd
    assert set(re.findall(r'"ERR ([A-Z]+)', casc)) <= set(C.ERR_CODES)
    assert "`" + ", ".join(C.CASCADE_COLUMNS) + "`" in sec


def test_plain_firmware_answers_cascade_commands_notimpl(board):
    dut, log = board
    for cmd in ("TEMP", "GSEL 1", "GVERIFY", "GATE 5", "CASCADE 5 0", "STREAM 1 0", "CALIB 16 1", "DUTY 10 1",
                "FXFE 5 0"):
        with pytest.raises(DUTError, match="ERR NOTIMPL"):
            dut.command(cmd)
    with pytest.raises(DUTError, match="ERR ARG too many"):
        dut.command("RUN 5 5")                     # the 2-argument dispatcher keeps the old commands at <= 1
    dut.sel(0)
    assert dut.run(3)["n"] == 3
    assert edges(log) == ["1", "0"]


# ---- argument and state errors ----------------------------------------------------------------------------------
def test_cascade_errors(cboard):
    dut, log = cboard
    cases = {"GSEL 0": "RANGE", "GSEL 6": "RANGE", "GATE 5": "NOSEL", "GSEL": "ARG", "GATE 1 2": "ARG",
             "CASCADE 5": "ARG", "STREAM 1 5": "RANGE", "CALIB 1 1": "ARG", "CALIB 60 1": "ARG", "CALIB 16 0": "ARG",
             "DUTY 5 1": "ARG", "FXFE 1 3": "ARG", "FXFE 0 0": "ARG", "TEMP 1": "ARG"}
    for cmd, code in cases.items():
        with pytest.raises(DUTError, match=f"ERR {code}"):
            dut.command(cmd)
    dut.gsel(1)
    with pytest.raises(DUTError, match="ERR NOSEL"):  # no model selected
        dut.cascade(5, 0)
    dut.sel(0)
    with pytest.raises(DUTError, match="ERR ARG"):
        dut.cascade(5, 3)
    dut.gsel(5)
    for cmd in ("GATE 5", "CASCADE 5 0", "STREAM 1 0", "DUTY 10 1"):
        with pytest.raises(DUTError, match="ERR NOCAL"):
            dut.command(cmd)
    assert edges(log) == []                        # every error comes before D2 moves


# ---- numerics against the host export -----------------------------------------------------------------------------
def test_gverify_matches_host_on_every_vector(cboard):
    dut, _ = cboard
    W = np.load(CASC / "gverify_int16.npy")
    ref = pd.read_csv(CASC / "gverify.csv")
    assert len(W) == len(ref)
    fxc = [f"fx{i}" for i in range(10)]
    for n in (None, 16, 32):
        if n:
            dut.calib(n, 1)
        for i in range(len(W)):
            r = dut.gverify(W[i])
            e = ref.iloc[i]
            assert r["sup"] == e["sup"] and r["healthy"] == e["healthy"], i
            assert np.allclose(r["fx"], e[fxc].to_numpy(np.float64), rtol=2e-6, atol=1e-9), i
            assert abs(r["d"] - e["mahal_d"]) <= 1e-5 * abs(e["mahal_d"]), i
            if n is None:
                assert r["sc"] == -1
            else:
                assert r["sc"] == e[f"selfcal{n}"], (n, i)
                assert abs(r["dsc"] - e[f"selfcal{n}_d"]) <= 1e-5 * abs(e[f"selfcal{n}_d"]), (n, i)


def test_streams_match_host_alarm_sequences(cboard):
    dut, _ = cboard
    st = pd.read_csv(CASC / "streams.csv")
    dut.sel(0)
    plan = [(1, "sup", None), (2, "healthy", None), (3, "either", None), (4, "either2", None),
            (5, "selfcal16", 16), (5, "selfcal32", 32)]
    for g, col, n in plan:
        if n:
            dut.calib(n, 1)
        dut.gsel(g)
        for s, name in enumerate(STREAMS):
            exp = st[st.stream == name].sort_values("pos")[col].to_numpy()
            r = dut.stream(2, s)
            assert r["windows"] == 200
            assert r["seq"] == fnv1a(exp), (col, name)
            assert r["alarms"] == r["esc"] == 2 * exp.sum(), (col, name)


def test_calib_threshold_matches_host(cboard):
    import json
    dut, _ = cboard
    gp = json.loads((CASC / "gate_params.json").read_text())
    for n in (16, 32):
        thr = dut.calib(n, 2)["thr"]
        ref = gp["selfcal"]["thr_after_calib"][str(n)]
        assert abs(thr - ref) <= 1e-5 * ref, (n, thr, ref)


# ---- window rules ---------------------------------------------------------------------------------------------------
def test_every_timed_cascade_command_opens_one_window(cboard):
    dut, log = cboard
    dut.sel(0)
    dut.gsel(4)
    t = dut.temp()
    assert t == 25.0
    n0 = len(edges(log))
    r = [dut.gate(20), dut.cascade(10, 0), dut.cascade(10, 1), dut.cascade(10, 2), dut.stream(1, 4),
         dut.calib(16, 2), dut.duty(10, 5), dut.fxfe(5, 0), dut.fxfe(5, 1), dut.fxfe(5, 2)]
    e = edges(log)[n0:]
    assert e == ["1", "0"] * len(r)
    c0, c1 = r[1], r[2]
    assert c1["esc"] == 10 and c0["esc"] == c0["alarms"] <= 10
    assert r[3]["esc"] == 10
    assert r[6]["overrun"] == 0 and r[6]["us"] >= 50_000


def test_duty_period_is_held(cboard):
    dut, _ = cboard
    dut.sel(0)
    dut.gsel(1)
    r = dut.duty(20, 10)
    assert 200_000 <= r["us"] < 230_000


# ---- split front ends (CASCADE m = 2) -------------------------------------------------------------------------------
def test_split_front_ends_equal_the_full_ones(tmp_path):
    """fe_r1 + fe_r2_tail == fe_r2 and R1[1:] + fe_r4_tail == fe_r4, bit for bit, on the stored inputs."""
    if not (CMSIS / "arm_math.h").exists() or not shutil.which("gcc"):
        pytest.skip("no Arduino_CMSIS-DSP sources or gcc")
    bench = REPO / "firmware" / "bench"
    src = tmp_path / "split.c"
    src.write_text(r'''
#include <stdio.h>
#include <string.h>
#include "frontend.h"
int main(int argc, char **argv) {
  static float x[1024], a[41], b[41];
  FILE *f = fopen(argv[1], "rb"); int bad = 0;
  fe_init();
  for (int w = 0; fread(x, sizeof(float), 1024, f) == 1024; ++w) {
    fe_r2(x, a); memset(b, 0, sizeof b); fe_r1(x, b); fe_r2_tail(x, b); bad |= memcmp(a, b, 24 * sizeof(float));
    fe_r4(x, a); float r1[10]; fe_r1(x, r1); for (int i = 0; i < 9; ++i) b[i] = r1[i + 1];
    fe_r4_tail(x, b); bad |= memcmp(a, b, 41 * sizeof(float));
  }
  printf("%s\n", bad ? "DIFF" : "SAME");
  return bad;
}
''')
    dsp = [CMSIS / "Source" / s for s in (
        "TransformFunctions/arm_rfft_fast_f32.c", "TransformFunctions/arm_rfft_fast_init_f32.c",
        "TransformFunctions/arm_cfft_f32.c", "TransformFunctions/arm_cfft_init_f32.c",
        "TransformFunctions/arm_cfft_radix8_f32.c", "TransformFunctions/arm_bitreversal.c",
        "CommonTables/arm_common_tables.c", "CommonTables/arm_const_structs.c",
        "ComplexMathFunctions/arm_cmplx_mag_f32.c")]
    exe = tmp_path / "split"
    inc = [f"-I{p}" for p in (SIM_DIR / "inc", CMSIS, CMSIS / "Include", CMSIS / "PrivateInclude", bench,
                              REPO / "handoff")]
    subprocess.run(["gcc", "-std=c11", "-O2", "-ffp-contract=off", "-w", *inc, str(src), str(bench / "frontend.c"),
                    str(SIM_DIR / "dsp_shim.c"), *map(str, dsp), "-lm", "-o", str(exe)], check=True)
    import tools.contract  # noqa: F401  (REPO on sys.path)
    sys.path.insert(0, str(REPO / "ml"))
    W = np.load(REPO / "handoff" / "test_vectors" / "raw.npy").astype("<f4")
    (tmp_path / "w.bin").write_bytes(W.tobytes())
    r = subprocess.run([str(exe), str(tmp_path / "w.bin")], capture_output=True, text=True)
    assert r.stdout.strip() == "SAME"


# ---- Paderborn bundle: gate 6 (pre-registration Deviation 4) ------------------------------------------------------
@pytest.mark.parametrize("ds", ["cwru", "pu"])
def test_verify_gate_passes_on_the_simulator(ds, request):
    from tools.verify_gate import verify
    dut, _ = request.getfixturevalue("cboard" if ds == "cwru" else "pboard")
    dut.sel(0)
    df, bad = verify(dut, ds, quick=False, session="test")
    assert bad == 0, df[df.agree == 0].head(10)
    assert set(df.gate) >= ({"sup", "healthy", "selfcal"} | ({"selfcal_r2"} if ds == "pu" else set()))


def test_gate6_state_and_reuse(pboard):
    dut, log = pboard
    dut.sel(0)
    dut.gsel(6)
    with pytest.raises(DUTError, match="ERR NOCAL"):
        dut.gate(5)
    dut.gsel(5)
    dut.calib(32, 1)                 # calibrates gate 5 only
    dut.gsel(6)
    with pytest.raises(DUTError, match="ERR NOCAL"):
        dut.gate(5)
    r = dut.calib(128, 1)
    gp = __import__("json").loads((REPO / "handoff" / "cascade" / "pu" / "gate_params.json").read_text())
    ref = gp["selfcal_r2"]["thr_after_calib"]
    assert abs(r["thr"] - ref) <= 1e-5 * ref
    with pytest.raises(DUTError, match="ERR ARG"):
        dut.calib(129, 1)
    assert dut.cascade(10, 2)["esc"] == 10   # whole-R2 reuse path
    assert dut.gate(10)["n"] == 10
