# TinyML-Pareto paper (IEEEtran conference, anonymous)

Upload this folder (or the zip) to Overleaf and compile `main.tex` with pdfLaTeX + BibTeX. IEEEtran is built into
Overleaf, so no class file is included.

| File | What it is |
|---|---|
| `main.tex` | the paper |
| `references.bib` | bibliography (IEEEtran style) |
| `numbers.tex` | every number in the paper as a `\Num...` macro; generated, do not edit |
| `figs/fig_overview.tex`, `figs/fig_rig.tex` | TikZ drawings (study overview, energy rig + measurement window) |
| `figs/fig_*.pdf`, `figs/fig_*.svg` | data figures (PDF for the paper; SVG copies) |
| `scripts/make_numbers.py` | builds `numbers.tex` and `figdata/` from the repository's `results/` |
| `scripts/make_figs.py` | draws `figs/fig_*.pdf` from `figdata/` |
| `scripts/cascade_rates.json`, `scripts/rig_constants.json` | values transcribed from committed repo docs, with sources |

Regenerate after new data (e.g. when the float-log campaign finishes), from the repository root:

    python paper/latex/scripts/make_numbers.py
    python paper/latex/scripts/make_figs.py
    python tools/make_selector.py                                  # refresh the model selector with the same data

The float-log (FE_FLOAT_LOG) numbers come from the complete campaign (22-23 Nov 2026): 3 repeats of every R2, R3
and R4 model plus R0/R1 anchors on both datasets; the sentence in Section V-B prints the repeat counts.
