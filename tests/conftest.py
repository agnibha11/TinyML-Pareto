"""Shared fixtures: the bench firmware core compiled for the PC, served behind a pseudo-terminal."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ml"))

from tools.dut import DUT  # noqa: E402

SIM_DIR = REPO / "firmware" / "tests" / "host_sim"


@pytest.fixture(scope="session")
def sim_binary(tmp_path_factory):
    if not shutil.which(os.environ.get("CXX", "g++")) or not shutil.which("make"):
        pytest.skip("no C++ compiler / make")
    subprocess.run(["make", "-s", "-C", str(SIM_DIR), "bench_sim"], check=True)
    out = tmp_path_factory.mktemp("sim") / "bench_sim"
    shutil.copy(SIM_DIR / "bench_sim", out)
    out.chmod(0o755)
    return out


@pytest.fixture()
def board(sim_binary, tmp_path):
    """A fresh simulated board per test: (DUT, path of the D2 edge log)."""
    err = open(tmp_path / "sync.log", "w")
    proc = subprocess.Popen([str(sim_binary)], stdout=subprocess.PIPE, stderr=err, text=True)
    line = proc.stdout.readline().strip()
    assert line.startswith("PTY "), line
    dut = DUT(line.split()[1], open_timeout_s=0.1)
    yield dut, tmp_path / "sync.log"
    dut.close()
    proc.kill()
    proc.wait()
    err.close()
