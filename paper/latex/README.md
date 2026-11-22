# TinyML-Pareto paper (IEEEtran conference, anonymous)

Upload this folder (or the zip) to Overleaf and compile `main.tex` with pdfLaTeX + BibTeX. IEEEtran is built into
Overleaf, so no class file is included.

| File | What it is |
|---|---|
| `main.tex` | the paper |
| `references.bib` | bibliography (IEEEtran style) |
| `numbers.tex` | every number in the paper as a `\Num...` macro; generated, do not edit |
| `figs/fig_overview.tex`, `figs/fig_rig.tex` | TikZ drawings (study overview, energy rig + measurement window) |
| `figs/fig_*.pdf`, `figs/fig_*.svg` | data figures (PDF for the paper, SVG for the website) |
| `scripts/make_numbers.py` | builds `numbers.tex` and `figdata/` from the repository's `results/` |
| `scripts/make_figs.py` | draws `figs/fig_*.pdf` from `figdata/` |
| `scripts/cascade_rates.json`, `scripts/rig_constants.json` | values transcribed from committed repo docs, with sources |

Regenerate after new data (e.g. when the float-log campaign finishes), from the repository root:

    python paper/latex/scripts/make_numbers.py
    python paper/latex/scripts/make_figs.py
    python tools/make_selector.py && python tools/make_site.py     # refresh the website with the same numbers

The float-log (FE_FLOAT_LOG) numbers were computed from the repeats finished on 22 Nov 2026 (CWRU 1, Paderborn 2).
Re-running the two commands picks up repeats 2-3 automatically; the sentence in Section V-B prints the repeat counts.
