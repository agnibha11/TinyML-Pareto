#!/usr/bin/env python3
"""Paderborn run, end to end, from one terminal command, with a live dashboard.

    python tools/pu/run_pu.py                 download -> prepare -> features -> splits -> train all 88 model IDs
    python tools/pu/run_pu.py --status        what is done so far (no work)
    python tools/pu/run_pu.py --bearings smoke --recordings 1 --seeds 0      a 10-minute trial on 6 bearings

What it does:
  1. Downloads the 32 Paderborn archives (~5.4 GB) into data/raw/pu/ (3 at a time, resumable, size-checked).
  2. Extracts 5 of the 20 recordings per bearing and operating condition, resamples 64 -> 12 kHz, applies the CWRU
     pipeline's 5 kHz low-pass and cuts 1024-sample windows (data/processed/pu/); computes R1-R4; writes the P1-P4
     splits (splits/pu/). See ml/pu_data.py.
  3. Trains every architecture (51 -> 88 model IDs) on every protocol, fold and seed with ml/train.py, unchanged:
       * trees and MLPs on CPU worker processes (--workers, one core each);
       * CNNs on the GPU if TensorFlow can see one (else on the CPU workers);
       * every CNN checkpoints after each epoch (build/pu/ckpt/), so Ctrl-C or a crash loses at most one epoch;
       * every finished model writes results/pu/host/<model>__<P>__f<k>__s<seed>.json (same format as CWRU).
     Jobs run seed-major (seed 0 of everything first), so stopping early still leaves a complete first seed.
  4. Shows live progress (per family, per worker, GPU load, ETA) and writes results/pu/progress.json every few
     seconds; worker logs go to results/pu/logs/.

Stop any time with Ctrl-C and run the same command again: finished models are skipped, a CNN in training resumes at
its last epoch. Nothing here touches the CWRU data or results.

GPU (NVIDIA, Linux): TensorFlow 2.18 needs its CUDA libraries:   pip install "tensorflow[and-cuda]==2.18.1"
Check with:   python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
"""
from __future__ import annotations

import os

os.environ["TINYML_DATASET"] = "pu"

import argparse
import json
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml"))
import pu_data as PU  # noqa: E402

RESULTS = REPO / "results" / "pu"
HOST = RESULTS / "host"
LOGS = RESULTS / "logs"
CKPT = REPO / "build" / "pu" / "ckpt"
PROGRESS = RESULTS / "progress.json"
FAMILIES = ("dt", "rf", "mlp", "cnn")

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (BarColumn, DownloadColumn, Progress, TaskProgressColumn, TextColumn,
                               TimeRemainingColumn, TransferSpeedColumn)
    from rich.table import Table
    RICH = True
except ImportError:                                    # plain text fallback
    RICH = False

console = Console() if RICH else None


def say(msg: str) -> None:
    if RICH:
        console.print(msg)
    else:
        print(msg, flush=True)


def hms(sec: float | None) -> str:
    if sec is None or sec != sec or sec == float("inf"):
        return "--"
    sec = int(sec)
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


# ------------------------------------------------------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------------------------------------------------------
def nvidia_smi() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return []
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 5:
            gpus.append({"name": p[0], "util": p[1], "mem_used": p[2], "mem_total": p[3], "temp": p[4]})
    return gpus


def tf_check() -> dict:
    """Import TensorFlow + tf_keras + tfmot in a child process (so this process never holds the GPU) and list GPUs."""
    code = ("import os; os.environ['TF_CPP_MIN_LOG_LEVEL']='3'; os.environ['TF_USE_LEGACY_KERAS']='1'\n"
            "import json, tensorflow as tf, tf_keras, tensorflow_model_optimization\n"
            "g = tf.config.list_physical_devices('GPU')\n"
            "names = [tf.config.experimental.get_device_details(x).get('device_name', x.name) for x in g]\n"
            "print(json.dumps({'tf': tf.__version__, 'gpus': names, 'cuda': tf.test.is_built_with_cuda()}))\n")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"error": r.stderr[-800:]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def check_env(args) -> dict:
    missing = []
    for mod in ("numpy", "scipy", "pandas", "pyarrow", "sklearn"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit(f"Missing Python packages: {missing}. Activate the project venv and run: "
                 f"pip install -r requirements-lock.txt")
    if not args.skip_data:
        PU.extractor()                                  # exits with an install hint if no RAR tool is present
    say("[bold]Checking TensorFlow and the GPU...[/bold]" if RICH else "Checking TensorFlow and the GPU...")
    info = tf_check()
    if "error" in info:
        sys.exit("TensorFlow / tf_keras / tensorflow_model_optimization failed to import:\n" + info["error"] +
                 "\nInstall the project environment: pip install -r requirements-lock.txt")
    smi = nvidia_smi()
    info["nvidia_smi"] = smi
    use_gpu = bool(info["gpus"]) and not args.no_gpu
    if info["gpus"]:
        say(f"  TensorFlow {info['tf']} sees GPU: {', '.join(info['gpus'])}" + ("  (ignored: --no-gpu)" if args.no_gpu
                                                                                 else ""))
    else:
        say(f"  TensorFlow {info['tf']} sees [bold red]no GPU[/bold red]." if RICH else
            f"  TensorFlow {info['tf']} sees no GPU.")
        if smi:
            say(f"  nvidia-smi does see {smi[0]['name']}: TensorFlow is missing its CUDA libraries. Fix with:\n"
                f"      pip install \"tensorflow[and-cuda]=={info['tf']}\"")
        elif not args.no_gpu:
            say("  No NVIDIA GPU/driver found (nvidia-smi missing). CNNs would train on the CPU (slow).")
        if not args.no_gpu and "cnn" in args.families and sys.stdin.isatty() and not args.yes:
            ans = input("  Train the CNNs on the CPU anyway? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                sys.exit("Stopped. Fix the GPU setup (see above) or pass --no-gpu to accept CPU training.")
    info["use_gpu"] = use_gpu
    return info


# ------------------------------------------------------------------------------------------------------------------
# Data stages
# ------------------------------------------------------------------------------------------------------------------
def stage_download(codes: list[str]) -> None:
    todo = [c for c in codes if not PU.downloaded(c)]
    if not todo:
        say(f"Download: all {len(codes)} archives present.")
        return
    total = sum(PU.BEARINGS[c][2] for c in todo)
    say(f"Downloading {len(todo)} archives ({total / 1e9:.2f} GB) into {PU.RAW.relative_to(REPO)}/ ...")
    if RICH:
        prog = Progress(TextColumn("{task.description:>6}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                        TimeRemainingColumn(), console=console)
        with prog:
            overall = prog.add_task("total", total=total)
            tasks, seen, lock = {}, {}, threading.Lock()

            def cb(code, done, tot):
                with lock:
                    if code not in tasks:
                        tasks[code] = prog.add_task(code, total=tot)
                        seen[code] = 0
                    prog.update(tasks[code], completed=done)
                    prog.advance(overall, done - seen[code])
                    seen[code] = done
                    if done >= tot:
                        prog.remove_task(tasks[code])

            with ThreadPoolExecutor(3) as ex:
                for f in as_completed([ex.submit(PU.download_one, c, cb) for c in todo]):
                    f.result()
    else:
        last = [0.0]

        def cb(code, done, tot):
            if time.time() - last[0] > 10 or done >= tot:
                print(f"  {code}: {done / tot:6.1%} of {tot / 1e6:.0f} MB", flush=True)
                last[0] = time.time()

        with ThreadPoolExecutor(3) as ex:
            for f in as_completed([ex.submit(PU.download_one, c, cb) for c in todo]):
                f.result()
    say("Download complete.")


def stage_prepare(codes: list[str], recs: list[int], workers: int, test_per_bc: int) -> None:
    say(f"Preparing {len(codes)} bearings (recordings {recs} per condition; 64 -> 12 kHz; windows)...")
    todo, n_done = list(codes), 0
    if RICH:
        prog = Progress(TextColumn("prepare"), BarColumn(), TaskProgressColumn(), TextColumn("{task.fields[last]}"),
                        TimeRemainingColumn(), console=console)
        with prog:
            t = prog.add_task("prep", total=len(todo), last="")
            with ProcessPoolExecutor(max(1, min(workers, 4))) as ex:
                futs = {ex.submit(PU.prepare_bearing, c, recs): c for c in todo}
                for f in as_completed(futs):
                    f.result()
                    prog.update(t, advance=1, last=futs[f])
    else:
        with ProcessPoolExecutor(max(1, min(workers, 4))) as ex:
            futs = {ex.submit(PU.prepare_bearing, c, recs): c for c in todo}
            for f in as_completed(futs):
                f.result()
                n_done += 1
                print(f"  prepared {futs[f]} ({n_done}/{len(todo)})", flush=True)
    W, M = PU.assemble(codes, recs)
    meta = json.loads((PU.PROCESSED / "prepare.json").read_text())
    say(f"  windows: {W.shape[0]:,} x 1024 from {M.bearing.nunique()} bearings"
        + (f"; skipped recordings: {len(meta['skipped_recordings'])}" if meta["skipped_recordings"] else ""))
    say("Computing features R1-R4...")
    PU.build_features()
    folds = PU.write_splits(test_per_bc)
    say(f"Splits written: {folds} (checks passed)")


def data_is_current(codes: list[str], recs: list[int], test_per_bc: int) -> bool:
    pj, sj = PU.PROCESSED / "prepare.json", PU.SPLITS / "protocols.json"
    if not pj.exists() or not sj.exists():
        return False
    meta, spl = json.loads(pj.read_text()), json.loads(sj.read_text())
    feats = all((PU.PROCESSED / f"feat_{r}.npy").exists() for r in ("r1", "r2", "r3", "r4"))
    return (meta["bearings"] == codes and meta["recordings"] == recs and feats
            and spl.get("test_per_bc") == test_per_bc)


# ------------------------------------------------------------------------------------------------------------------
# Training
# ------------------------------------------------------------------------------------------------------------------
def job_list(families, seeds, protocols) -> list[tuple]:
    import models as MD
    folds = json.loads((PU.SPLITS / "protocols.json").read_text())["folds"]
    out = []
    for s in seeds:
        for a in MD.all_archs():
            if a.family in families:
                for p in protocols:
                    for k in range(folds[p]):
                        out.append((a, p, k, s))
    return out


def done_counts(jobs) -> dict:
    have = {f.name for f in HOST.glob("*.json")} if HOST.exists() else set()
    c = {f: [0, 0] for f in FAMILIES}
    for a, p, k, s in jobs:
        c[a.family][1] += 1
        if all(f"{m}__{p}__f{k}__s{s}.json" in have for m in a.model_ids):
            c[a.family][0] += 1
    return c


def tail(path: Path, n: int = 6) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return [ln.rstrip() for ln in lines if ln.strip()][-n:]
    except OSError:
        return []


def count_failed() -> int:
    n = 0
    for lf in LOGS.glob("*.log"):
        try:
            n += lf.read_text(errors="replace").count(" FAILED")
        except OSError:
            pass
    return n


class Worker:
    def __init__(self, name: str, cmd: list[str], env: dict):
        self.name, self.cmd = name, cmd
        self.log = LOGS / f"{name}.log"
        with open(self.log, "a") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n")
        self.fh = open(self.log, "a")
        self.p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=self.fh, stderr=subprocess.STDOUT)

    @property
    def alive(self) -> bool:
        return self.p.poll() is None

    def status(self) -> str:
        """The job this worker is on and its latest line (epoch, QAT or scoring for NNs; the result for trees)."""
        lines = tail(self.log, 10)
        last = lines[-1].strip() if lines else ""
        if not self.alive:
            return f"finished (exit {self.p.returncode})" + (f": {last[-100:]}" if self.p.returncode else "")
        job = next((ln.split("] start ", 1)[1].strip() for ln in reversed(lines) if "] start " in ln), "")
        if job and last.startswith(job):                 # epoch / QAT / scoring line of the current job
            return f"{job}  |  {last[len(job):].strip()}"
        if job and "] start " in last:
            return f"{job}  |  starting"
        return last[-120:]


def launch(args, info) -> list[Worker]:
    LOGS.mkdir(parents=True, exist_ok=True)
    base = [sys.executable, "-u", str(REPO / "ml" / "train.py"), "--seeds", *map(str, args.seeds),
            "--protocols", *args.protocols, "--ckpt-dir", str(CKPT), "--epoch-log"]
    if args.hours:
        base += ["--deadline", str(time.time() + args.hours * 3600)]
    env = {**os.environ, "TINYML_DATASET": "pu", "PYTHONUNBUFFERED": "1", "TF_CPP_MIN_LOG_LEVEL": "3",
           "TF_USE_LEGACY_KERAS": "1"}
    cpu_env = {**env, "CUDA_VISIBLE_DEVICES": "-1"}
    workers = []
    cpu_fams = [f for f in args.families if f != "cnn" or not info["use_gpu"]]
    if info["use_gpu"] and "cnn" in args.families:
        workers.append(Worker("gpu-cnn", base + ["--families", "cnn", "--gpu", "--threads", "2"], env))
    if cpu_fams:
        n = args.workers
        for i in range(n):
            workers.append(Worker(f"cpu-{i}", base + ["--families", *cpu_fams, "--shard", f"{i}/{n}",
                                                       "--threads", "1"], cpu_env))
    return workers


def write_progress(stage, counts, rates, workers, gpu, t0, args) -> None:
    fams = {}
    for f, (d, t) in counts.items():
        if t:
            r = rates.get(f)
            fams[f] = {"done": d, "total": t, "jobs_per_h": round(r * 3600, 1) if r else None,
                       "eta_h": round((t - d) / r / 3600, 2) if r else None}
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "updated": datetime.now().isoformat(timespec="seconds"), "stage": stage, "elapsed_h": round(
            (time.time() - t0) / 3600, 2), "seeds": args.seeds, "protocols": args.protocols, "families": fams,
        "failed_jobs_logged": count_failed(), "gpu": gpu,
        "workers": [{"name": w.name, "alive": w.alive, "exit": w.p.returncode, "status": w.status()}
                    for w in workers]}, indent=1))
    tmp.replace(PROGRESS)


def render(counts, rates, workers, gpu, t0, info, failed):
    head = Table.grid(padding=(0, 2))
    head.add_row("[bold]Paderborn run[/bold]", f"elapsed {hms(time.time() - t0)}",
                 f"GPU: {gpu[0]['name']} {gpu[0]['util']}% util, {gpu[0]['mem_used']}/{gpu[0]['mem_total']} MiB, "
                 f"{gpu[0]['temp']} C" if gpu else ("GPU: " + (", ".join(info["gpus"]) if info["use_gpu"] else "none")))
    ft = Table(title="Jobs (one job = one architecture x protocol x fold x seed)", expand=True)
    for c in ("family", "done / total", "progress", "jobs/h", "ETA"):
        ft.add_column(c, justify="right" if c != "progress" else "left")
    tot_d = tot_t = 0
    for f in FAMILIES:
        d, t = counts[f]
        if not t:
            continue
        tot_d, tot_t = tot_d + d, tot_t + t
        r = rates.get(f)
        bar_w = 30
        fill = int(bar_w * d / t)
        ft.add_row(f, f"{d} / {t}", "[green]" + "█" * fill + "[/green]" + "░" * (bar_w - fill),
                   f"{r * 3600:.0f}" if r else "--", hms((t - d) / r) if r else "--")
    ft.add_row("[bold]all[/bold]", f"[bold]{tot_d} / {tot_t}[/bold]", f"{100 * tot_d / max(tot_t, 1):.1f} %", "", "")
    wt = Table(title="Workers", expand=True)
    wt.add_column("worker", no_wrap=True)
    wt.add_column("now", overflow="ellipsis", no_wrap=True)
    for w in workers:
        wt.add_row(("[green]" if w.alive else "[dim]") + w.name, w.status())
    foot = (f"results: results/pu/host/   logs: results/pu/logs/   live status: results/pu/progress.json"
            + (f"\n[red]{failed} job(s) failed[/red] (see logs; rerunning retries them)" if failed else "")
            + "\nCtrl-C stops cleanly; run the same command again to resume.")
    return Group(Panel(head), ft, wt, Panel(foot))


def stage_train(args, info) -> int:
    jobs = job_list(args.families, args.seeds, args.protocols)
    counts = done_counts(jobs)
    if all(d == t for d, t in counts.values()):
        say(f"Training: all {len(jobs)} jobs already done.")
        return 0
    (RESULTS / "run_config.json").write_text(json.dumps(
        {"started": datetime.now().isoformat(timespec="seconds"), "argv": sys.argv[1:], "seeds": args.seeds,
         "protocols": args.protocols, "families": args.families, "workers": args.workers,
         "tf": info.get("tf"), "gpus": info.get("gpus"), "use_gpu": info["use_gpu"]}, indent=1))
    workers = launch(args, info)
    say(f"Started {len(workers)} worker(s): " + ", ".join(w.name for w in workers))
    t0 = time.time()
    base = {f: counts[f][0] for f in FAMILIES}
    hist: list[tuple[float, dict]] = [(t0, dict(base))]
    gpu, last_gpu, last_prog = nvidia_smi(), 0.0, 0.0

    def snapshot():
        nonlocal gpu, last_gpu, last_prog
        c = done_counts(jobs)
        now = time.time()
        hist.append((now, {f: c[f][0] for f in FAMILIES}))
        while len(hist) > 2 and now - hist[0][0] > 1800:          # rate over the last 30 minutes
            hist.pop(0)
        t_a, d_a = hist[0]
        rates = {}
        for f in FAMILIES:
            gained = c[f][0] - d_a[f]
            if gained > 0 and now > t_a:
                rates[f] = gained / (now - t_a)
            elif c[f][0] > base[f] and now > t0:
                rates[f] = (c[f][0] - base[f]) / (now - t0)
        if now - last_gpu > 5:
            gpu, last_gpu = nvidia_smi(), now
        if now - last_prog > 5:
            write_progress("train", c, rates, workers, gpu, t0, args)
            last_prog = now
        return c, rates

    interrupted = False
    try:
        if RICH and not args.plain:
            c, rates = snapshot()
            with Live(render(c, rates, workers, gpu, t0, info, 0), console=console, refresh_per_second=1,
                      screen=False) as live:
                while any(w.alive for w in workers):
                    time.sleep(2)
                    c, rates = snapshot()
                    live.update(render(c, rates, workers, gpu, t0, info, count_failed()))
        else:
            last = 0.0
            while any(w.alive for w in workers):
                time.sleep(5)
                c, rates = snapshot()
                if time.time() - last > 30:
                    parts = [f"{f} {c[f][0]}/{c[f][1]}" for f in FAMILIES if c[f][1]]
                    print(f"[{hms(time.time() - t0)}] " + "  ".join(parts), flush=True)
                    for w in workers:
                        print(f"    {w.name}: {w.status()}", flush=True)
                    last = time.time()
    except KeyboardInterrupt:
        interrupted = True
        say("\nStopping workers (CNN checkpoints are kept)...")
        for w in workers:
            if w.alive:
                w.p.send_signal(signal.SIGINT)
        deadline = time.time() + 20
        while any(w.alive for w in workers) and time.time() < deadline:
            time.sleep(0.5)
        for w in workers:
            if w.alive:
                w.p.terminate()
    c, rates = snapshot()
    write_progress("stopped" if interrupted else "finished", c, rates, workers, gpu, t0, args)
    for f in FAMILIES:
        if c[f][1]:
            say(f"  {f:>4}: {c[f][0]} / {c[f][1]} jobs done")
    left = sum(t - d for d, t in c.values())
    if interrupted or left:
        say(f"{left} job(s) left. Run the same command again to continue.")
    else:
        say("All training jobs finished.")
    failed = count_failed()
    if failed:
        say(f"{failed} failure line(s) in results/pu/logs/ (failed jobs are retried on the next run).")
    return 1 if left else 0


def status(args) -> None:
    if not (PU.SPLITS / "protocols.json").exists():
        say("No Paderborn splits yet: run without --status first.")
        return
    jobs = job_list(args.families, args.seeds, args.protocols)
    c = done_counts(jobs)
    for f in FAMILIES:
        if c[f][1]:
            say(f"  {f:>4}: {c[f][0]} / {c[f][1]} jobs done")
    if PROGRESS.exists():
        say(f"last progress.json: {json.loads(PROGRESS.read_text())['updated']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="print what is done and exit")
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4], help="training seeds (default 0-4)")
    ap.add_argument("--families", nargs="+", default=list(FAMILIES), choices=FAMILIES)
    ap.add_argument("--protocols", nargs="+", default=["P1", "P2", "P3", "P4"])
    ap.add_argument("--workers", type=int, default=max(1, min((os.cpu_count() or 2) - 2, 6)),
                    help="CPU worker processes for trees/MLPs (default: cores - 2, at most 6)")
    ap.add_argument("--bearings", help="'smoke' (6 bearings, for a quick trial) or a comma list; default all 32")
    ap.add_argument("--recordings", type=int, default=5, help="recordings per bearing and condition (default 5)")
    ap.add_argument("--test-per-bc", type=int, default=46, help="test windows per (bearing, condition)")
    ap.add_argument("--no-gpu", action="store_true", help="train CNNs on the CPU workers even if a GPU is visible")
    ap.add_argument("--yes", action="store_true", help="do not ask before training CNNs without a GPU")
    ap.add_argument("--hours", type=float, default=0, help="start no new job after this many hours (0 = no limit)")
    ap.add_argument("--skip-data", action="store_true", help="use the prepared data as it is (no download/prepare)")
    ap.add_argument("--data-only", action="store_true", help="stop after download/prepare/features/splits")
    ap.add_argument("--plain", action="store_true", help="plain log lines instead of the live dashboard")
    args = ap.parse_args()

    if args.status:
        status(args)
        return
    codes, recs = PU.codes(args.bearings), PU.recordings(args.recordings)
    say(f"Paderborn pipeline: {len(codes)} bearings, recordings {recs}, seeds {args.seeds}, "
        f"protocols {args.protocols}, {args.workers} CPU worker(s)")
    try:
        info = check_env(args) if not args.data_only else {"use_gpu": False}
        if not args.skip_data:
            stage_download(codes)
            if data_is_current(codes, recs, args.test_per_bc):
                say("Prepared data, features and splits are up to date.")
            else:
                stage_prepare(codes, recs, args.workers, args.test_per_bc)
    except KeyboardInterrupt:
        sys.exit("\nStopped. Run the same command again to continue (downloads resume where they stopped).")
    if args.data_only:
        return
    sys.exit(stage_train(args, info))


if __name__ == "__main__":
    main()
