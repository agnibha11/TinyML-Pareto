#!/usr/bin/env python3
"""Day-one environment check: run this once after installing requirements-lock.txt (about 1 minute on CPU).

It proves the three things the sweep depends on, on random data:
  1. A tiny CNN in the project's layer style trains, goes through QAT (tfmot) and converts to a full-int8 .tflite.
  2. The same model converts with PTQ (representative dataset) to a full-int8 .tflite.
  3. emlearn exports a decision tree as a float, inline C header.
It also prints the TFLite op list, which is what Abhinav's MicroMutableOpResolver must register.

IMPORTANT finding when this was first run (TF 2.18.1, tfmot 0.8.0): QAT rejects Keras Conv1D and MaxPool1D
("Layer conv1d ... is not supported"). Write the 1D CNN with Conv2D over a (1024, 1, 1) input: kernel (k, 1),
stride (s, 1), MaxPool2D((2, 1)), GlobalAveragePooling2D. This is mathematically identical to Conv1D. TFLite lowers
Conv1D to CONV_2D anyway, and this form also avoids the extra RESHAPE/EXPAND_DIMS ops.
"""
import os

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import contextlib
import io
import re
import tempfile
from pathlib import Path

import emlearn
import numpy as np
import tensorflow as tf
import tensorflow_model_optimization as tfmot
import tf_keras as keras
from sklearn.tree import DecisionTreeClassifier


def tiny_cnn() -> keras.Model:
    L = keras.layers
    return keras.Sequential([
        L.Input((1024, 1, 1)),
        L.Conv2D(8, (9, 1), strides=(2, 1), padding="same"), L.BatchNormalization(), L.ReLU(), L.MaxPool2D((2, 1)),
        L.Conv2D(16, (3, 1), padding="same"), L.BatchNormalization(), L.ReLU(), L.MaxPool2D((2, 1)),
        L.GlobalAveragePooling2D(), L.Dense(10), L.Softmax(),
    ])


def to_int8(model, rep_x) -> bytes:
    c = tf.lite.TFLiteConverter.from_keras_model(model)
    c.optimizations = [tf.lite.Optimize.DEFAULT]
    c.representative_dataset = lambda: ([rep_x[i:i + 1]] for i in range(len(rep_x)))
    c.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    c.inference_input_type = tf.int8
    c.inference_output_type = tf.int8
    return c.convert()


def ops_of(tflite: bytes) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tf.lite.experimental.Analyzer.analyze(model_content=tflite)
    return sorted(set(re.findall(r"Op#\d+ (\w+)", buf.getvalue())))


def main() -> None:
    print("tf", tf.__version__, "| tf_keras", keras.__version__, "| tfmot", tfmot.__version__,
          "| emlearn", emlearn.__version__)
    rng = np.random.default_rng(0)
    X = rng.normal(size=(512, 1024, 1, 1)).astype("float32")
    y = rng.integers(0, 10, 512)

    m = tiny_cnn()
    m.compile("adam", "sparse_categorical_crossentropy")
    m.fit(X, y, epochs=1, verbose=0)
    q = tfmot.quantization.keras.quantize_model(m)
    q.compile("adam", "sparse_categorical_crossentropy")
    q.fit(X, y, epochs=1, verbose=0)

    for name, model in [("PTQ", m), ("QAT", q)]:
        b = to_int8(model, X[:50])
        it = tf.lite.Interpreter(model_content=b)
        it.allocate_tensors()
        inp = it.get_input_details()[0]
        assert inp["dtype"] == np.int8, inp["dtype"]
        print(f"{name}: int8 .tflite {len(b)} B, input scale={inp['quantization'][0]:.4g} "
              f"zp={inp['quantization'][1]}, ops={ops_of(b)}")

    F = rng.normal(size=(300, 26)).astype("float32")
    t = DecisionTreeClassifier(max_depth=6, random_state=0).fit(F, rng.integers(0, 10, 300))
    with tempfile.TemporaryDirectory() as d:
        h = Path(d) / "dt.h"
        emlearn.convert(t, method="inline", dtype="float").save(file=str(h), name="dt_r2_d6_fp32")
        src = h.read_text()
        assert "dt_r2_d6_fp32" in src
        sig = [ln.strip() for ln in src.splitlines() if "dt_r2_d6_fp32_predict(" in ln][:1]
        print("emlearn float tree header OK:", sig[0] if sig else "(predict signature not found - inspect header)")
    print("ENV OK")


if __name__ == "__main__":
    main()
