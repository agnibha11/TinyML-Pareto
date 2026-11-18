#!/usr/bin/env python3
"""The board's TFLM runtime (Arduino library Chirale_TensorFLowLite 2.0.0) and the TinyML-Pareto int8 patch.

    python tools/chirale_lib.py status    # installed library: patched / superseded / unpatched / unknown, tree hash
    python tools/chirale_lib.py apply     # apply firmware/patches/chirale-2.0.0-int8.patch to the unpatched library
    python tools/chirale_lib.py revert    # take the patch out again (back to the unpatched 2.0.0 tree)

The patch makes the board's int8 FULLY_CONNECTED (per-channel weights) and MEAN match TensorFlow 2.18 bit for bit;
the patch file explains both changes. The library is identified by a hash of its whole file tree (relative path and
sha256 of every file), and firmware/patches/chirale.json lists the hashes of the unpatched and the patched tree,
plus the trees of earlier patch versions ("superseded": same outputs, kept so old fw_build.csv labels stay readable).
tools/build_flash.py refuses to build a TFLM bundle unless the tree is the patched one, and writes the label into
fw_build.csv (column tflm_lib).
Library folder: $CHIRALE_LIB if set, else <arduino-cli directories.user>/libraries/Chirale_TensorFLowLite.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATCHES = REPO / "firmware" / "patches"
META = json.loads((PATCHES / "chirale.json").read_text())


def lib_dir() -> Path:
    if os.environ.get("CHIRALE_LIB"):
        return Path(os.environ["CHIRALE_LIB"])
    user = Path.home() / "Arduino"
    cli = shutil.which("arduino-cli")
    if cli:
        r = subprocess.run([cli, "config", "get", "directories.user"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            user = Path(r.stdout.strip())
    return user / "libraries" / META["library"]


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(q for q in root.rglob("*") if q.is_file()):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).hexdigest().encode()
                 + b"\n")
    return h.hexdigest()


def state(root: Path | None = None) -> tuple[str, str]:
    """('patched' | 'superseded' | 'unpatched' | 'unknown' | 'missing', tree hash)."""
    root = root or lib_dir()
    if not root.is_dir():
        return "missing", ""
    t = tree_sha256(root)
    for k, v in META["tree_sha256"].items():
        if t == v:
            return k, t
    if any(t == v["tree_sha256"] for v in META.get("superseded", {}).values()):
        return "superseded", t
    return "unknown", t


def label(st: str, t: str) -> str:
    """fw_build.csv tflm_lib value, e.g. chirale-2.0.0+int8v2 (patched) or chirale-2.0.0 (unpatched)."""
    base = f"chirale-{META['version']}"
    if st == "superseded":
        return base + "+" + next(k for k, v in META["superseded"].items() if v["tree_sha256"] == t)
    return {"patched": base + "+" + META["label"], "unpatched": base}.get(st, f"unknown-{t[:12]}")


def check() -> str:
    """Exit unless the installed library is the patched tree; returns the fw_build.csv label."""
    st, t = state()
    if st != "patched":
        sys.exit(f"{META['library']} at {lib_dir()} is {st} (tree {t[:12] or '-'}): run "
                 f"'python tools/chirale_lib.py apply' first (or --allow-unpatched-lib to build the old library)")
    return label(st, t)


def _patch(reverse: bool) -> None:
    root = lib_dir(); st, t = state(root)
    want = "patched" if reverse else "unpatched"
    if st == "superseded":
        v = next(v for v in META["superseded"].values() if v["tree_sha256"] == t)
        sys.exit(f"{root} has the older patch {label(st, t)}: revert it first with\n"
                 f"  git show {v['commit']}:firmware/patches/{META['patch']} | patch -R -p1 -d '{root}'\n"
                 f"then run '{'revert' if reverse else 'apply'}' again: nothing changed")
    if st != want:
        sys.exit(f"{root} is {st} (tree {t[:12] or '-'}), expected {want}: nothing changed")
    args = ["patch", "-p1", "--no-backup-if-mismatch", "-d", str(root), "-i", str(PATCHES / META["patch"])]
    if reverse:
        args.insert(1, "-R")
    if subprocess.run([*args, "--dry-run", "-s"]).returncode != 0:
        sys.exit("patch does not apply cleanly: nothing changed")
    subprocess.run(args, check=True)
    st, t = state(root)
    print(f"{root}: now {st} ({label(st, t)}, tree {t[:12]})")
    if st != ("unpatched" if reverse else "patched"):
        sys.exit("tree hash after patching is not the expected one: check the library folder")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        st, t = state()
        print(f"{lib_dir()}: {st} ({label(st, t) if st != 'missing' else '-'}), tree {t or '-'}")
    elif cmd in ("apply", "revert"):
        _patch(reverse=cmd == "revert")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
