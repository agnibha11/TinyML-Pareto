#!/usr/bin/env python3
"""Build the public site pages that are not the selector: docs/index.html (home) and docs/hardware/index.html.

Every number on these pages is read from paper/latex/numbers.tex (written by paper/latex/scripts/make_numbers.py from
the measured results) or from paper/latex/figdata/*.csv, so the site and the paper always agree. Data figures are the
paper's own (paper/latex/figs/fig_*.svg, written by make_figs.py); the two diagrams (overview, rig) are TikZ drawings
converted once to SVG and kept in docs/hardware/img/.

Order:
    python paper/latex/scripts/make_numbers.py && python paper/latex/scripts/make_figs.py
    python tools/make_selector.py && python tools/make_site.py
"""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
LATEX = REPO / "paper" / "latex"
DOCS = REPO / "docs"
HW = DOCS / "hardware"


def load_numbers() -> dict[str, str]:
    """\\NumX{...} macros -> HTML text."""
    out = {}
    for m in re.finditer(r"\\newcommand\{\\Num([A-Za-z]+)\}\{(.*)\}\s*$", (LATEX / "numbers.tex").read_text(), re.M):
        v = m.group(2)
        v = v.replace("{,}", ",").replace("\\,\\%", "&nbsp;%").replace("$-$", "−")
        v = re.sub(r"\\texttt\{([^}]*)\}", r"<code>\1</code>", v)
        v = v.replace("\\,", "&thinsp;").replace("$", "")
        out[m.group(1)] = v
    return out


CSS = """
:root{--page:#f3f4f6;--bg:#fff;--ink:#1f2329;--muted:#69717c;--line:#e3e6ea;--soft:#f4f5f7;--acc:#1baf7a;
--shadow:0 1px 2px rgba(17,24,39,.05),0 1px 1px rgba(17,24,39,.03);--r:14px;
--f-sans:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;--f-mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.6 var(--f-sans);padding:0 max(16px,env(safe-area-inset-left,0px)) 48px}
.wrap{max-width:1080px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
nav.top{display:flex;gap:18px;font-size:13px;padding-top:18px}
nav.top a{color:var(--muted);text-decoration:none} nav.top a:hover,nav.top a[aria-current]{color:var(--ink)}
header{padding:26px 2px 0} h1{font-weight:600;font-size:clamp(26px,4vw,38px);letter-spacing:-.015em;line-height:1.1;margin:0}
.lede{font-size:17px;color:var(--muted);max-width:68ch;margin:10px 0 0}
h2{font-size:20px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.block{border:1px solid var(--line);background:var(--bg);border-radius:var(--r);box-shadow:var(--shadow);padding:20px 22px}
.block p{margin:8px 0;max-width:78ch} .muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.tile{background:var(--bg);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow)}
.tile b{display:block;font:500 24px var(--f-mono);letter-spacing:-.02em} .tile span{font-size:12.5px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.card{display:block;text-decoration:none;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:var(--r);
padding:18px 20px;box-shadow:var(--shadow);transition:border-color .15s} .card:hover{border-color:var(--muted)}
.card b{font-size:16px} .card p{margin:6px 0 0;font-size:13.5px;color:var(--muted)}
figure{margin:10px 0 0} figure img{width:100%;height:auto;display:block;background:#fff;border-radius:8px}
figure.narrow img{max-width:660px;margin:0 auto}
a{color:var(--ink)}
figcaption{font-size:13px;color:var(--muted);margin-top:6px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums;margin-top:10px}
th{font-weight:500;color:var(--muted);text-align:right;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);font-family:var(--f-mono);white-space:nowrap}
th:first-child,td:first-child{text-align:left} td.l{text-align:left;font-family:var(--f-sans);white-space:normal}
tr:last-child td{border-bottom:0} .tw{overflow-x:auto}
ul{margin:8px 0;padding-left:20px} li{margin:4px 0;max-width:78ch}
code{font-family:var(--f-mono);font-size:.92em;background:var(--soft);padding:1px 5px;border-radius:5px}
.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px} @media(max-width:820px){.two{grid-template-columns:1fr}}
footer{font-size:12.5px;color:var(--muted);padding:4px 2px} footer a{color:var(--ink)}
"""

HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{css}</style></head><body><div class="wrap">
<nav class="top"><a href="{root}"{a0}>TrueJoule</a><a href="{root}selector/">Model selector</a><a href="{root}hardware/"{a2}>Hardware results</a></nav>
"""
FOOT = """<footer>Built {built} from the measured results in the repository ·
<a href="https://github.com/agnibha11/TinyML-Pareto">github.com/agnibha11/TinyML-Pareto</a></footer></div></body></html>
"""
GH = "https://github.com/agnibha11/TinyML-Pareto/blob/main/"


def family_rows(N: dict) -> str:
    rows = ""
    for fam, name in [("DT", "Decision tree"), ("RF", "Random forest"), ("MLP", "MLP"), ("CNN", "1-D CNN")]:
        rows += (f"<tr><td>{name}</td><td>{N['Cwru'+fam+'N']}</td><td>{N['Cwru'+fam+'EMin']}–{N['Cwru'+fam+'EMax']}</td>"
                 f"<td>{N['Cwru'+fam+'FrontShare']}</td><td>{N['Cwru'+fam+'TMinMs']}–{N['Cwru'+fam+'TMaxMs']}</td>"
                 f"<td>{N['Cwru'+fam+'FlashMinKB']}–{N['Cwru'+fam+'FlashMaxKB']}</td>"
                 f"<td>{N['CwruPOneBest'+fam]} / {N['CwruPThreeBest'+fam]}</td><td>{N['PuPOneBest'+fam]} / {N['PuPThreeBest'+fam]}</td></tr>")
    return rows


def frontend_rows(N: dict) -> str:
    rows = ""
    for rp, name, logs in [("RZero", "R0 raw window (CNN input)", "0"), ("ROne", "R1 ten statistics", "0"),
                           ("RTwo", "R2 statistics + 14 log bands", "14"), ("RFour", "R4 envelope spectrum", "32"),
                           ("RThree", "R3 256-bin log spectrum", "256")]:
        lf_c = N.get("CwruLfFront" + rp, "–")
        lf_p = N.get("PuLfFront" + rp, "–")
        rows += (f"<tr><td class='l'>{name}</td><td>{logs}</td><td>{N['CwruFrontT'+rp]}</td><td>{N['CwruFront'+rp]}</td>"
                 f"<td>{lf_c}</td><td>{N['PuFront'+rp]}</td><td>{lf_p}</td></tr>")
    return rows


def gate_rows() -> str:
    rows = ""
    for ds, name in [("cwru", "CWRU"), ("pu", "Paderborn")]:
        g = pd.read_csv(LATEX / "figdata" / f"gates_{ds}.csv")
        for r in g.itertuples(index=False):
            rows += (f"<tr><td>{name}</td><td class='l'>{r.name}</td><td>{r.mean:.2f}</td><td>{100 * r.cv:.2f}&nbsp;%</td>"
                     f"<td>{r.E1_est_uJ:.2f}</td><td>{r.ratio_meas_est:.2f}×</td></tr>")
    return rows


def build_home(N: dict, built: str) -> str:
    h = HEAD.format(title="TrueJoule · verified, leakage-free TinyML for bearing fault monitoring", css=CSS, root="./",
                    a0=' aria-current="page"', a2="")
    h += f"""
<header><h1>TrueJoule</h1>
<p class="lede">Verified, leakage-free TinyML for self-calibrating bearing fault monitoring. Every accuracy on this site
is measured on bearings the model never saw in training, every energy is measured on the board, and every board output
is checked bit for bit against the host before it is measured.</p></header>

<div class="tiles">
  <div class="tile"><b>{N['TotWindows']}</b><span>GPIO-framed energy windows on an Arduino Nano 33 BLE</span></div>
  <div class="tile"><b>{N['CwruCvMed']}</b><span>median repeat-to-repeat spread of measured energy</span></div>
  <div class="tile"><b>{N['CwruNModels']} × 2</b><span>tree, forest, MLP and fp32/int8 CNN models, CWRU and Paderborn</span></div>
  <div class="tile"><b>{N['CasCwSaving']}×</b><span>less energy per window with the self-calibrating gate (CWRU, 1&nbsp;% faults)</span></div>
</div>

<div class="cards">
  <a class="card" href="selector/"><b>Model selector →</b><p>Set RAM, flash, inference rate, battery and target life;
  get the most accurate model that fits, with measured energy, for either dataset and any evaluation protocol.</p></a>
  <a class="card" href="hardware/"><b>Hardware results →</b><p>The energy rig, measured accuracy–energy fronts, where the
  energy goes, estimate versus measurement, board verification and the self-calibrating monitor.</p></a>
  <a class="card" href="https://github.com/agnibha11/TinyML-Pareto/tree/main/paper/latex"><b>Paper source →</b><p>IEEE conference paper (LaTeX), with the scripts that
  turn the measured results into every number and figure.</p></a>
</div>

<section class="block"><h2>What TrueJoule found</h2><ul>
<li><b>Unseen bearings change the answer.</b> Holding out whole bearings reverses the model ranking (Kendall τ
{N['CwruTauPOnePThree']} on CWRU) and leaves {N['CwruFrontKept']} of {N['CwruFrontPOneN']} Pareto-optimal models standing.
The CNN that wins on random splits costs {N['CwruCnnOverTreeMin']}–{N['CwruCnnOverTreeMax']}× the cheapest tree; on unseen
bearings a forest (CWRU) or an envelope-feature MLP (Paderborn) is best.</li>
<li><b>The front end is the cost.</b> {N['CwruDTFrontShare']} of a decision tree's energy is feature extraction; a
float-only logarithm made the log-spectrum front end {N['CwruLfFactorRThree']}× cheaper.</li>
<li><b>Estimates are rankings, not budgets.</b> Chip-level estimates miss the measured energy by
{N['CwruEstRatioMin']}–{N['CwruEstRatioMax']}× and move models on and off the Pareto front.</li>
<li><b>Verify before you measure.</b> The board check exposed an int8 kernel fault in the Arduino TFLM port that left
energy unchanged and predictions wrong; after the fix {N['CwruVerTwoPass']} of {N['CwruVerTwoN']} TFLM models are bit-exact.</li>
<li><b>Calibrate on the machine.</b> A gate that re-fits itself from {N['CasCalibSixteenS']}&nbsp;s of healthy vibration cut
false alarms at an unseen load from {N['CasCwGTwoFpr']} to {N['CasCwGFivenOneSixFpr']}.</li>
</ul></section>

<section class="block"><h2>Project documents</h2><ul>
<li><a href="{GH}docs/energy_rig_bringup.md">Energy rig bring-up</a> — wiring, calibration and acceptance rules</li>
<li><a href="{GH}docs/cascade_preregistration.md">Cascade pre-registration</a> — hypotheses fixed before the first cascade window, with every deviation</li>
<li><a href="{GH}CONTRACT.md">Interface contract</a> — model IDs, file formats, firmware commands</li>
<li><a href="{GH}docs/midsem/README.md">Mid-semester</a> and <a href="{GH}docs/sprint2/README.md">sprint 2</a> status pages (pre-hardware)</li>
</ul></section>
"""
    return h + FOOT.format(built=built)


def build_hardware(N: dict, built: str) -> str:
    h = HEAD.format(title="TrueJoule · hardware results", css=CSS, root="../", a0="", a2=' aria-current="page"')
    h += f"""
<header><h1>Hardware results</h1>
<p class="lede">Measured on an Arduino Nano 33 BLE (nRF52840, Cortex-M4F, 64&nbsp;MHz) with an INA219/ESP32 rig:
{N['TotWindows']} energy windows in {N['TotSessions']} sessions, {N['TotRejected']} rejected and re-measured.</p></header>

<div class="tiles">
  <div class="tile"><b>{N['CwruCvMed']}</b><span>median repeat spread of E2E energy (CWRU; Paderborn {N['PuCvMed']})</span></div>
  <div class="tile"><b>{N['CwruAddMed']}</b><span>median gap between E2E and FRONT + RUN</span></div>
  <div class="tile"><b>{N['CwruIdleMed']}&nbsp;mW</b><span>board idle power at the USB input (5–95&nbsp;%: {N['CwruIdleLo']}–{N['CwruIdleHi']})</span></div>
  <div class="tile"><b>{N['CwruVerTwoPass']}/{N['CwruVerTwoN']}</b><span>TFLM models bit-exact against the host after the int8 fix</span></div>
</div>

<section class="block"><h2>The study</h2>
<figure><img src="img/overview.svg" alt="TrueJoule overview: data, evaluation ladder, design space, verified deployment, monitor design"></figure>
<p class="muted">Five stages from data to monitor. The bottom row shows what each rung of the evaluation ladder holds
out: random windows (P1), whole recordings (P2) or whole bearings (P3).</p></section>

<section class="block two"><div><h2>The energy rig</h2>
<p>The Nano is powered through a cut USB cable whose VBUS line runs through an INA219 module (R100 shunt, calibrated to
an effective {N['RigShuntOhm']}&nbsp;Ω). An ESP32 logger samples bus voltage and current at about
{N['RigLoggerHz']}&nbsp;kHz and integrates power over windows that the Nano frames with its D2 pin, so both clocks time
the same interval.</p>
<p>Each block is a {N['RigIdleWindowS']}&nbsp;s IDLE window followed by N calls of one mode (FRONT, RUN or E2E) lasting at
least {N['RigMinWindowS']}&nbsp;s. Energy per call is E = (P̄ − P̄<sub>idle</sub>)·t/N. A window is rejected if the two
clocks disagree by more than {N['RigDurTolPct']}&nbsp;%, the INA219 overflows or the record is incomplete.</p></div>
<figure><img src="img/rig.svg" alt="Rig wiring and one measurement window"></figure></section>

<section class="block"><h2>Measured accuracy–energy fronts</h2>
<figure><img src="img/fig_pareto.svg" alt="Macro-F1 against measured energy, leaky and bearing-disjoint protocols, both datasets"></figure>
<ul>
<li>CWRU, random windows (P1): best {N['CwruPOneBestId']} (F1 {N['CwruPOneBest']}, {N['CwruPOneBestE']}&nbsp;µJ), but
{N['CwruPOneNearId']} is within 0.01 F1 for {N['CwruPOneNearSaving']}× less energy.</li>
<li>CWRU, unseen bearings (P3): best {N['CwruPThreeBestId']} (F1 {N['CwruPThreeBest']}, {N['CwruPThreeBestE']}&nbsp;µJ);
best CNN {N['CwruPThreeBestCNN']}, best MLP {N['CwruPThreeBestMLP']}.</li>
<li>Paderborn, unseen bearings (P3): best {N['PuPThreeBestId']} (F1 {N['PuPThreeBest']}, {N['PuPThreeBestE']}&nbsp;µJ).</li>
<li>Only {N['CwruFrontKept']} of {N['CwruFrontPOneN']} CWRU P1-front models stay on the P3 front ({N['PuFrontKept']} of
{N['PuFrontPOneN']} on Paderborn); Kendall τ between P1 and P3 rankings: {N['CwruTauPOnePThree']} (CWRU),
{N['PuTauPOnePThree']} (Paderborn). No CNN is Pareto-optimal on any protocol.</li>
</ul></section>

<section class="block"><h2>Where the energy goes</h2>
<figure class="narrow"><img src="img/fig_energy.svg" alt="Front-end share of energy and front-end energy per representation"></figure>
<div class="tw"><table><thead><tr><th>Family (CWRU)</th><th>n</th><th>E2E µJ</th><th>Front-end share</th><th>Latency ms</th>
<th>Flash KB</th><th>F1 CWRU P1 / P3</th><th>F1 PU P1 / P3</th></tr></thead><tbody>{family_rows(N)}</tbody></table></div>
<div class="tw"><table><thead><tr><th>Front end</th><th>logs</th><th>ms</th><th>CWRU µJ</th><th>float-log µJ</th>
<th>PU µJ</th><th>float-log µJ</th></tr></thead><tbody>{frontend_rows(N)}</tbody></table></div>
<p class="muted">The float-log build replaces newlib's double-precision <code>log10f</code> ({N['RigLogfCycles']} cycles)
with a float-only version ({N['RigFloatLogCycles']} cycles). R0 and R1 take no logarithm, so their change
(up to {N['LfImageShiftMax']}&nbsp;% in either direction) is the effect of rebuilding the firmware image alone.
Float-log campaign: {N['CwruLfReps']} repeats per model on CWRU, {N['PuLfReps']} on Paderborn.</p></section>

<section class="block"><h2>Estimated versus measured energy</h2>
<figure class="narrow"><img src="img/fig_est.svg" alt="Measured against estimated energy and ratio per family"></figure>
<p>The pre-hardware estimate (instruction-accurate emulator, Cortex-M4 cycle model, datasheet power) orders the models
well (Spearman ρ {N['CwruEstSpearman']}) but misses their energy by {N['CwruEstRatioMin']}–{N['CwruEstRatioMax']}×
(median {N['CwruEstRatioMed']}×). Trees and forests sit at {N['CwruEstRatioTreeMin']}–{N['CwruEstRatioTreeMax']}×; fp32
CNNs below one. With estimated energy, {N['CwruPThreeEstFrontDiff']} models change Pareto status on the CWRU P3 front and
{N['PuPThreeEstFrontDiff']} on the Paderborn P3 front. The largest CNN was predicted at {N['CwruBigCnnEstS']}&nbsp;s per
inference; it takes {N['CwruBigCnnMeasS']}&nbsp;s.</p></section>

<section class="block"><h2>Board verification</h2>
<p>Before any energy window, 100 test vectors per model are run on the board and compared with the host: fp32 within
10<sup>−4</sup>, int8 within one quantization step with identical classes.</p>
<div class="tw"><table><thead><tr><th>Library</th><th>CWRU pass</th><th>int8 fail</th><th>Paderborn pass</th><th>int8 fail</th></tr></thead><tbody>
<tr><td>Arduino TFLM (Chirale 2.0.0)</td><td>{N['CwruVerPass']}/{N['CwruVerN']}</td><td>{N['CwruVerIntFail']}/{N['CwruVerIntN']}</td>
<td>{N['PuVerPass']}/{N['PuVerN']}</td><td>{N['PuVerIntFail']}/{N['PuVerIntN']}</td></tr>
<tr><td>Patched (per-channel FC, reference MEAN)</td><td>{N['CwruVerTwoPass']}/{N['CwruVerTwoN']} TFLM</td><td>0</td>
<td>{N['PuVerTwoPass']}/{N['PuVerTwoN']} TFLM</td><td>0</td></tr></tbody></table></div>
<p>The unpatched port applies one requantization multiplier to fully-connected layers that TF&nbsp;2.18 quantizes per
channel; class agreement fell as low as {N['PuVerIntAgreeMin']}. Energy did not reveal it: patched and unpatched int8
energies differ by {N['CwruPatchRatioMin']}–{N['CwruPatchRatioMax']}×.</p></section>

<section class="block"><h2>The self-calibrating monitor</h2>
<figure class="narrow"><img src="img/fig_cascade.svg" alt="Gate energy and false alarms; cascade energy at 1 percent prevalence"></figure>
<p>An integer gate scores every window; an alarm wakes the diagnosis model. The gate re-fits its healthy-state model on
the device from the first windows after installation (no fault data). All {N['CwruCasChecks']} (CWRU) and
{N['PuCasChecks']} (Paderborn) board alarms matched the host. At an unseen load the factory gate alarms on every healthy
window; after {N['CasCalibSixteenS']}&nbsp;s of calibration the false-alarm rate is {N['CasCwGFivenOneSixFpr']} at recall
{N['CasCwGFivenOneSixTpr']}, and the monitor spends {N['CasEpiCwruGFive']}&nbsp;µJ per window instead of
{N['CwruPTwoBestE']}&nbsp;µJ. On Paderborn the pre-registered gate rule failed (every passing gate had FPR 1.0); the
self-calibrating band-feature gate G6, chosen after the study, gives {N['CasEpiPuGSix']}&nbsp;µJ per window against
{N['CasPuModelE']}&nbsp;µJ.</p>
<div class="tw"><table><thead><tr><th>Dataset</th><th style="text-align:left">Gate</th><th>Measured E1 µJ</th><th>Repeat CV</th><th>Estimate µJ</th>
<th>Measured / estimate</th></tr></thead><tbody>{gate_rows()}</tbody></table></div>
<p class="muted">Pre-registered tests that failed are reported as such: additivity against the main campaign
({N['CwruRqThreeFail']} of {N['CwruRqThreeN']} CWRU and {N['PuRqThreeFail']} of {N['PuRqThreeN']} Paderborn models outside
2&nbsp;%) and stream-energy prediction within 3&nbsp;% ({N['CwruRqFourFail']}/{N['CwruRqFourN']} and
{N['PuRqFourFail']}/{N['PuRqFourN']} streams). Within one firmware image additivity holds to
{N['CwruRqThreeInMin']}–{N['CwruRqThreeInMax']}.</p></section>

<section class="block"><h2>Data</h2><ul>
<li>Energy windows: <a href="{GH}results/cwru/hardware/hw_measurements.csv"><code>results/cwru/hardware/hw_measurements.csv</code></a>,
<a href="{GH}results/pu/hardware/hw_measurements.csv"><code>results/pu/hardware/hw_measurements.csv</code></a></li>
<li>Board verification: <code>results/&lt;dataset&gt;/hardware/board_verify*.csv</code>; cascade analysis:
<code>results/&lt;dataset&gt;/cascade_hw/analysis/</code></li>
<li>Every number above: <a href="{GH}paper/latex/numbers.tex"><code>paper/latex/numbers.tex</code></a>, generated by
<code>paper/latex/scripts/make_numbers.py</code></li>
</ul></section>
"""
    return h + FOOT.format(built=built)


def main() -> None:
    N = load_numbers()
    built = date.today().isoformat()
    (HW / "img").mkdir(parents=True, exist_ok=True)
    for f in ("fig_pareto", "fig_energy", "fig_est", "fig_cascade"):
        shutil.copy(LATEX / "figs" / f"{f}.svg", HW / "img" / f"{f}.svg")
    (DOCS / "index.html").write_text(build_home(N, built))
    (HW / "index.html").write_text(build_hardware(N, built))
    print("wrote docs/index.html, docs/hardware/index.html")


if __name__ == "__main__":
    main()
