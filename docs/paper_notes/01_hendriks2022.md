# Hendriks, Dumond, Knox (2022). Towards better benchmarking using the CWRU bearing fault dataset

*Mechanical Systems and Signal Processing* 169, 108732 · DOI 10.1016/j.ymssp.2021.108732 · online 30 Dec 2021 ·
BibTeX key `hendriks2022towards`

**What was read.** The journal full text is paywalled. What was read:
- the ScienceDirect preview (abstract, highlights, full Introduction, snippets of Results and Conclusions);
- in full, **Chapter 4 of Hendriks' uOttawa MASc thesis (2021)**, which is the pre-publication version of this paper.
  Its abstract matches the journal almost word for word, and its result sentences match the journal snippets exactly.

Per-model numbers below come from the thesis. Cite the journal.

## What they do
- Core claim (abstract): the usual procedure of training and testing on different operating conditions "does not
  constitute a useful domain shift problem since the same physical bearings exist in both training and testing sets."
- Data: CWRU 12 kHz only; faults at the drive end **and** fan end; **7 classes** (IR, OR, B × DE/FE + healthy).
- The outer race is at the 0° position where available.
- Original (load-wise) framework: sets A/B/C = loads 1/2/3 HP, each with all fault sizes. Train on one set, test on
  each of the other two (6 cases).
- Proposed framework: sets D/E/F = fault sizes 0.007/0.014/0.021 in, each with loads 1–3 HP. Test bearings are unseen.
- 0 HP is not used.
- Healthy data: not described in the text that could be read. Rosa et al. (2024) report it was split by load (1/2/3 HP
  → D/E/F), which still leaks the single healthy bearing. Cite this as second-hand.
- Models: ACDIN and WDCNN on time or FFT input; ImageNet AlexNet and ResNet on 2-channel spectrograms. Sliding windows
  with 97 % overlap.

## Results (test accuracy, %, average of 6 train→test cases)

| Framework | ACDIN time | ACDIN spec | WDCNN time | WDCNN spec | AlexNet | ResNet |
|---|---|---|---|---|---|---|
| Original (load-wise) | 88.4 | 86.3 | 90.6 | 90.9 | 84.9 | **95.0** |
| Proposed (fault-size) | 37.1 | 40.6 | 36.2 | 50.0 | 47.4 | **53.1** |

- The journal's own wording: "diagnostic accuracy decreases on average by approximately 45% in the proposed benchmark
  framework". That is ≈ 45 points absolute: the mean over the 6 set-ups is 89.4 → 44.1 %.
- "ResNet appears to be the strongest, on average performing 4.1% better" (Results).
- No deployment, embedded hardware, energy or memory anywhere (checked across the whole thesis).

## Use in our paper
- Our P3 (leave one fault size out, 4-class) follows their fault-size grouping.
- We differ in three ways:
  - We use the drive end only and all four loads.
  - We state the healthy-bearing limitation explicitly.
  - We report deployment cost.
- Quote the drop as "95 % → 53 % for the best CNN (7 classes)", not "4 classes".
