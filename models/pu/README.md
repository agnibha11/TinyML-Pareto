# Paderborn deployable models

The 88 model IDs trained on the Paderborn (KAt) bearing data, in the same format as the CWRU models in `models/`.
One instance per ID: protocol P3 (bearing-disjoint), fold 0, seed 0, so the test bearings were never seen in training.

- 3 classes: 0 healthy, 1 inner race, 2 outer race (CWRU models have 10).
- `<model_id>/model.h`: trees and forests as emlearn inline C (float input); MLPs and CNNs as a 16-byte-aligned
  TFLite flatbuffer array for TFLite Micro. `model.tflite` is the same network as a file.
- `<model_id>/meta.json` and `registry.csv`: family, front end, precision, quantization parameters, size.
- Inputs are normalised with `handoff/pu/norm_constants.h` (P3 fold-0 training statistics).
- `handoff/pu/test_vectors/`: 30 fixed test windows (10 per class), their features, and every model's expected
  output, for an on-board VERIFY check.
- `configs/pu/<model_id>.yaml`: full hyper-parameters.

Regenerate: `TINYML_DATASET=pu python ml/export.py` (needs the prepared data and `build/pu/deploy/` from
`python tools/pu/run_pu.py`).
