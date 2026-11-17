"""tools/chirale_lib.py: the board-library patch applies, reverts and is recognised by its tree hash."""
import shutil
import subprocess

import pytest

from tools import chirale_lib as cl


def test_meta_and_labels():
    assert cl.META["version"] == "2.0.0" and (cl.PATCHES / cl.META["patch"]).is_file()
    assert set(cl.META["tree_sha256"]) == {"unpatched", "patched"}
    assert cl.label("patched", "") == "chirale-2.0.0+int8"
    assert cl.label("unpatched", "") == "chirale-2.0.0"
    assert cl.label("unknown", "0123456789abcdef") == "unknown-0123456789ab"


def test_patch_touches_only_fc_and_mean():
    txt = (cl.PATCHES / cl.META["patch"]).read_text()
    files = [ln.split()[1] for ln in txt.splitlines() if ln.startswith("+++ ")]
    assert files == ["b/src/tensorflow/lite/micro/kernels/cmsis_nn/fully_connected.cpp",
                     "b/src/tensorflow/lite/micro/kernels/reduce_common.cpp"]


def test_missing_and_unknown(tmp_path):
    assert cl.state(tmp_path / "nope")[0] == "missing"
    (tmp_path / "a.txt").write_text("x")
    st, t = cl.state(tmp_path)
    assert st == "unknown" and len(t) == 64


def test_apply_revert_on_a_copy(tmp_path):
    src = cl.lib_dir()
    st, _ = cl.state(src)
    if st not in ("patched", "unpatched") or not shutil.which("patch"):
        pytest.skip(f"board library not installed in a known state ({st})")
    lib = tmp_path / "lib"
    shutil.copytree(src, lib)
    p = str(cl.PATCHES / cl.META["patch"])
    flag = ["-R"] if st == "patched" else []           # bring the copy to the unpatched tree first
    if flag:
        subprocess.run(["patch", *flag, "-s", "-p1", "-d", str(lib), "-i", p], check=True)
    assert cl.state(lib)[0] == "unpatched"
    subprocess.run(["patch", "-s", "-p1", "--no-backup-if-mismatch", "-d", str(lib), "-i", p], check=True)
    assert cl.state(lib)[0] == "patched"
    subprocess.run(["patch", "-R", "-s", "-p1", "--no-backup-if-mismatch", "-d", str(lib), "-i", p], check=True)
    assert cl.state(lib)[0] == "unpatched"
