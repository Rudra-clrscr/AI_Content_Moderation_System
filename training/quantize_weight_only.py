"""Weight-only INT8 (MatMulNBits / GatherBlockQuantized, 8 bits) for bonc-v4, checked against FP32.

    python quantize_weight_only.py --work D:/AI/bonc-v4/onnx

Weights are stored as INT8 blocks and de-quantized inside the kernels; activations stay
float, so a text's score can't depend on what it's batched with (unlike dynamic INT8).
Checks decision agreement with FP32 (guide: >= 99%), batch invariance, latency and size,
and prints a line calibrate_export.py's report can be compared with.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import onnx
import pandas as pd
from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer

from calibrate_export import THRESHOLD, OrtScorer

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="D:/AI/bonc-v4/onnx")
    ap.add_argument("--agree-n", type=int, default=3000)
    args = ap.parse_args()
    work = Path(args.work)
    fp32, tok = work / "model_fp32.onnx", work / "tok" / "tokenizer.json"

    outputs = {}
    for name, ops in (("weight_int8", ("MatMul",)), ("weight_int8_emb", ("MatMul", "Gather"))):
        out = work / f"model_{name}.onnx"
        try:
            q = MatMulNBitsQuantizer(onnx.load(str(fp32)), block_size=32, is_symmetric=True, bits=8,
                                     op_types_to_quantize=ops, quant_axes=(("MatMul", 0), ("Gather", 1)))
            q.process()
            q.model.save_model_to_file(str(out), use_external_data_format=False)
            outputs[name] = out
        except Exception as exc:
            print(f"{name} failed: {type(exc).__name__}: {exc}")

    test = pd.read_csv("D:/AI/bonc-data/test.csv", keep_default_na=False)
    hand = pd.read_csv(HERE / "eval_handwritten.csv", keep_default_na=False)
    texts = test["text"].sample(min(args.agree_n, len(test)), random_state=4).tolist() + hand["text"].tolist()
    p_ref = OrtScorer(fp32, tok, 256).p_batch(texts)
    inv = hand["text"].tolist()[:64]
    for name, path in outputs.items():
        s = OrtScorer(path, tok, 256)
        p = s.p_batch(texts)
        single, batched = s.p_single(inv), s.p_batch(inv, bs=64)
        t0 = time.perf_counter()
        for t in inv[:40]:
            s.logits([t])
        ms = (time.perf_counter() - t0) / 40 * 1000
        acc = float(((s.p_single(hand["text"].tolist()) > THRESHOLD) == (hand["label"] == 1)).mean())
        print(f"{name}: agreement {((p > THRESHOLD) == (p_ref > THRESHOLD)).mean():.4f}, "
              f"max_abs_diff_vs_fp32 {np.abs(p - p_ref).max():.4f}, batch_max_abs_diff {np.abs(single - batched).max():.6f}, "
              f"latency_ms_single {ms:.1f}, size_mb {path.stat().st_size / 1e6:.0f}, accuracy_handwritten {acc:.4f}")


if __name__ == "__main__":
    main()
