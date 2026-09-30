"""Store the word-embedding table as FP16 (everything else FP32), checked against FP32.

    python shrink_embeddings.py --work D:/AI/bonc-v4/onnx

DeBERTa-v3-small has a 128k x 768 embedding table: 98M of its 141M parameters, ~390 MB
in FP32. Only the table is stored in FP16; a Cast back to FP32 follows the lookup, so all
arithmetic stays FP32 (fast, and batch-invariant). Writes model_fp32_emb16.onnx.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import onnx
import pandas as pd
from onnx import TensorProto, helper, numpy_helper

from calibrate_export import THRESHOLD, OrtScorer

HERE = Path(__file__).resolve().parent


def shrink(src: Path, dst: Path, min_rows: int = 50_000) -> None:
    m = onnx.load(str(src))
    g = m.graph
    inits = {i.name: i for i in g.initializer}
    done = 0
    for node in list(g.node):
        if node.op_type != "Gather" or node.input[0] not in inits:
            continue
        w = inits[node.input[0]]
        if w.data_type != TensorProto.FLOAT or w.dims[0] < min_rows:
            continue
        arr = numpy_helper.to_array(w).astype(np.float16)
        w.CopyFrom(numpy_helper.from_array(arr, w.name))
        out = node.output[0]
        node.output[0] = out + "_fp16"
        cast = helper.make_node("Cast", [out + "_fp16"], [out], to=TensorProto.FLOAT, name=node.name + "_to_fp32")
        g.node.insert(list(g.node).index(node) + 1, cast)
        done += 1
    print(f"converted {done} embedding table(s) to FP16")
    onnx.save(m, str(dst))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="D:/AI/bonc-v4/onnx")
    ap.add_argument("--agree-n", type=int, default=3000)
    args = ap.parse_args()
    work = Path(args.work)
    fp32, out, tok = work / "model_fp32.onnx", work / "model_fp32_emb16.onnx", work / "tok" / "tokenizer.json"
    shrink(fp32, out)

    test = pd.read_csv("D:/AI/bonc-data/test.csv", keep_default_na=False)
    hand = pd.read_csv(HERE / "eval_handwritten.csv", keep_default_na=False)
    texts = test["text"].sample(min(args.agree_n, len(test)), random_state=4).tolist() + hand["text"].tolist()
    ref, s = OrtScorer(fp32, tok, 256), OrtScorer(out, tok, 256)
    p_ref, p = ref.p_batch(texts), s.p_batch(texts)
    inv = hand["text"].tolist()[:64]
    single, batched = s.p_single(inv), s.p_batch(inv, bs=64)
    for name, sc in (("fp32", ref), ("fp32_emb16", s)):
        t0 = time.perf_counter()
        for t in inv[:40]:
            sc.logits([t])
        print(f"{name}: latency_ms_single {(time.perf_counter() - t0) / 40 * 1000:.1f}")
    acc = float(((s.p_single(hand["text"].tolist()) > THRESHOLD) == (hand["label"] == 1)).mean())
    print(f"fp32_emb16: agreement {((p > THRESHOLD) == (p_ref > THRESHOLD)).mean():.4f}, "
          f"max_abs_diff_vs_fp32 {np.abs(p - p_ref).max():.6f}, batch_max_abs_diff {np.abs(single - batched).max():.6f}, "
          f"size_mb {out.stat().st_size / 1e6:.0f}, accuracy_handwritten {acc:.4f}")


if __name__ == "__main__":
    main()
