"""The design matrix: every architecture configuration, its model IDs, builders, params and MAC counts.

One *architecture* is trained per (protocol, fold, seed). It yields one or more *model IDs* (CONTRACT.md section 4):
    dt / rf   -> <fam>-<rep>-<size>-fp32
    mlp       -> fp32 and int8 (PTQ of the same trained weights)
    cnn       -> fp32, int8 (PTQ), int8qat (QAT fine-tune), int8ref (the int8 file run on TFLM reference kernels;
                 identical predictions, differs only in hardware cost)
68 model IDs from the execution plan's sweep, plus 20 on the physics-informed R4 front end (envelope spectrum,
features_spec.md v1.2): trees, forests and MLPs, the same sizes as on R1/R2. 88 model IDs in total.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")


@dataclass(frozen=True)
class Arch:
    family: str           # dt rf mlp cnn
    rep: str              # r0..r4
    size: str             # d8 | t10d6 | h32x32 | w050d4
    hp: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> str:
        return f"{self.family}-{self.rep}-{self.size}"

    @property
    def precisions(self) -> tuple[str, ...]:
        return {"dt": ("fp32",), "rf": ("fp32",), "mlp": ("fp32", "int8"),
                "cnn": ("fp32", "int8", "int8qat", "int8ref")}[self.family]

    @property
    def model_ids(self) -> list[str]:
        return [f"{self.key}-{p}" for p in self.precisions]

    @property
    def is_nn(self) -> bool:
        return self.family in ("mlp", "cnn")


def all_archs() -> list[Arch]:
    a: list[Arch] = []
    for rep in ("r1", "r2", "r4"):
        for d in (4, 6, 8, 12):
            a.append(Arch("dt", rep, f"d{d}", {"max_depth": d}))
    for rep in ("r1", "r2", "r4"):
        for t in (5, 10, 25):
            for d in (6, 10):
                a.append(Arch("rf", rep, f"t{t}d{d}", {"n_estimators": t, "max_depth": d}))
    for h in ((16,), (32,), (64,), (32, 32), (64, 64)):
        a.append(Arch("mlp", "r2", "h" + "x".join(map(str, h)), {"hidden": h}))
    for h in ((32,), (64,), (128,)):
        a.append(Arch("mlp", "r3", "h" + "x".join(map(str, h)), {"hidden": h}))
    for h in ((16,), (32,), (64,), (32, 32), (64, 64)):
        a.append(Arch("mlp", "r4", "h" + "x".join(map(str, h)), {"hidden": h}))
    for w in (0.25, 0.5, 1.0, 2.0):
        for d in (2, 4):
            a.append(Arch("cnn", "r0", f"w{int(round(w * 100)):03d}d{d}", {"width": w, "blocks": d}))
    return a


def arch_by_key(key: str) -> Arch:
    for a in all_archs():
        if a.key == key:
            return a
    raise KeyError(key)


def all_model_ids() -> list[str]:
    return [m for a in all_archs() for m in a.model_ids]


def arch_of(model_id: str) -> Arch:
    return arch_by_key(model_id.rsplit("-", 1)[0])


# --------------------------------------------------------------------------------------------------------------
# Neural network builders (tf_keras, Keras 2 API, so tfmot QAT works; see ml/env_smoke_test.py)
# --------------------------------------------------------------------------------------------------------------
def cnn_filters(width: float, blocks: int) -> list[int]:
    f, cap, out = max(4, round(16 * width)), max(4, round(64 * width)), []
    for b in range(blocks):
        out.append(min(f * 2 ** b, cap))
    return out


def build(arch: Arch, n_classes: int, n_in: int):
    import tf_keras as keras
    L = keras.layers
    if arch.family == "mlp":
        layers = [L.Input((n_in,))]
        for h in arch.hp["hidden"]:
            layers += [L.Dense(h), L.ReLU()]
        layers += [L.Dense(n_classes), L.Softmax()]
        return keras.Sequential(layers)
    if arch.family == "cnn":
        fl = cnn_filters(arch.hp["width"], arch.hp["blocks"])
        layers = [L.Input((1024, 1, 1))]
        for b, f in enumerate(fl):
            k, s = ((9, 1), (2, 1)) if b == 0 else ((3, 1), (1, 1))
            layers += [L.Conv2D(f, k, strides=s, padding="same"), L.BatchNormalization(), L.ReLU(),
                       L.MaxPool2D((2, 1))]
        layers += [L.GlobalAveragePooling2D(), L.Dense(n_classes), L.Softmax()]
        return keras.Sequential(layers)
    raise ValueError(arch.family)


def nn_cost(arch: Arch, n_classes: int, n_in: int) -> tuple[int, int]:
    """(params after BN folding, MACs per inference), counted from layer shapes."""
    if arch.family == "mlp":
        dims = [n_in, *arch.hp["hidden"], n_classes]
        params = sum(a * b + b for a, b in zip(dims[:-1], dims[1:]))
        macs = sum(a * b for a, b in zip(dims[:-1], dims[1:]))
        return params, macs
    fl = cnn_filters(arch.hp["width"], arch.hp["blocks"])
    length, cin, params, macs = 1024, 1, 0, 0
    for b, f in enumerate(fl):
        k, s = (9, 2) if b == 0 else (3, 1)
        length = -(-length // s)                       # padding 'same'
        params += k * cin * f + f                      # BN folded into conv weights + bias
        macs += length * k * cin * f
        length //= 2                                   # MaxPool 2
        cin = f
    params += cin * n_classes + n_classes
    macs += cin * n_classes + length * cin            # dense + global average pooling adds
    return params, macs
