# Paper

IEEE two-column working paper (`IEEEtran`, conference mode). `main.pdf` is the compiled version of the sources here.

| File | Role |
|---|---|
| `main.tex` | Title, authors, introduction, background, method; inputs `results_section.tex` |
| `results_section.tex` | Results, rubric, limitations, conclusion |
| `refs.bib` | References (IEEEtran style) |
| `numbers.tex` | Every number in the text, as a macro; generated |
| `tab_costs.tex` | Table V (cost per configuration); generated |
| `figs/` | Figures; copied from `results/` by `make_numbers.py` |
| `make_numbers.py` | Regenerates `numbers.tex`, `tab_costs.tex` and `figs/` from `results/` |

## Build

```bash
# from the repository root, after ml/stats.py and tools/vrig/campaign.py have been run
python paper/make_numbers.py
cd paper && latexmk -pdf main.tex
```

Needs a TeX Live installation with `IEEEtran`, `siunitx`, `booktabs`, `placeins` and `hyperref`
(all in `texlive-latex-extra` / `texlive-science`).

## Overleaf

Upload the contents of `paper/` (all files above, including `figs/`) to a new blank project and compile with
pdfLaTeX. `numbers.tex` and `tab_costs.tex` are committed, so Overleaf does not need to run `make_numbers.py`.
After the results change, run `make_numbers.py` locally and upload the regenerated files.
