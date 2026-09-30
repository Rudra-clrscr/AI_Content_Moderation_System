"""Calibrate, evaluate, export and quantize bonc-v4, then write the model bundle.

    python calibrate_export.py --ckpt D:/AI/bonc-v4/ckpt --bundle ../flask_api/models/v4

Steps (MODEL_IMPROVEMENT_GUIDE.md sections 3.2, 3.3 and 4):

 1. Temperature scaling on the validation split (GPU): one scalar T so that
    softmax(logits / T) is calibrated. Reports ECE before/after.
 2. Test metrics at the 0.5 boundary (test split + the hand-written eval set), overall
    and by source/category.
 3. ONNX export with T baked in (the graph outputs logits / T), dynamic batch and
    sequence axes.
 4. Quantization candidates, each checked against FP32 ONNX for
      * decision agreement at 0.5 (guide: >= 99%)
      * batch invariance: the same text scored alone and inside a padded batch must get
        the same score (dynamic INT8 fails this, which blocked batched sentence scoring)
      * CPU latency and file size
    The smallest candidate that passes both checks is shipped.
 5. Bundle: model file + tokenizer.json + model_meta.json (labels, safe_label,
    label_weights, recommended thresholds, temperature, metrics, batch_invariant).
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
LABELS = ["safe", "unsafe"]
THRESHOLD = 0.5


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def ece(p, y, bins=15):
    """Expected calibration error of P(unsafe) as a probability of the unsafe label."""
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p > lo) & (p <= hi) if lo > 0 else (p >= lo) & (p <= hi)
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def fit_temperature(logits, y):
    z = torch.tensor(logits, dtype=torch.float64)
    t = torch.tensor(y)
    log_t = torch.zeros(1, dtype=torch.float64, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)

    def closure():
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(z / log_t.exp(), t)
        loss.backward()
        return loss
    opt.step(closure)
    return float(log_t.exp())


@torch.no_grad()
def torch_logits(model, tok, texts, device, max_len, bs=128):
    model.eval()
    order = np.argsort([len(t) for t in texts])
    out = np.zeros((len(texts), 2), dtype=np.float32)
    for i in range(0, len(texts), bs):
        idx = order[i:i + bs]
        enc = tok([texts[j] for j in idx], truncation=True, max_length=max_len, padding=True, return_tensors="pt").to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            out[idx] = model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"]).logits.float().cpu().numpy()
    return out


def report(name, p, y, groups=None):
    pred = (p > THRESHOLD).astype(int)
    prec, rec, f1, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    res = {"n": int(len(y)), "accuracy": float((pred == y).mean()), "precision": float(prec), "recall": float(rec),
           "f1": float(f1), "auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else None, "ece": ece(p, y),
           "false_allow": int(fn), "false_reject": int(fp),
           "false_allow_rate": float(fn / max(1, fn + tp)), "false_reject_rate": float(fp / max(1, fp + tn))}
    print(f"\n== {name}: " + ", ".join(f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}" for k, v in res.items()))
    if groups is not None:
        df = pd.DataFrame({"g": groups, "y": y, "pred": pred})
        g = df.groupby("g").apply(lambda d: pd.Series({"n": len(d), "label": d.y.mean(), "correct": (d.y == d.pred).mean()}),
                                  include_groups=False)
        print(g.sort_values("correct").to_string(float_format=lambda v: f"{v:.3f}"))
    return res


# ----------------------------------------------------------------------------- ONNX

class Calibrated(torch.nn.Module):
    def __init__(self, model, temperature):
        super().__init__()
        self.model = model
        self.register_buffer("inv_t", torch.tensor(1.0 / temperature))

    def forward(self, input_ids, attention_mask):
        return self.model(input_ids=input_ids, attention_mask=attention_mask).logits * self.inv_t


def export_onnx(model, tok, temperature, path):
    wrapped = Calibrated(model.float().cpu().eval(), temperature)
    enc = tok(["warm up text for export", "a second, somewhat longer example sentence"], padding=True, return_tensors="pt")
    torch.onnx.export(wrapped, (enc["input_ids"], enc["attention_mask"]), str(path), dynamo=False,
                      input_names=["input_ids", "attention_mask"], output_names=["logits"], opset_version=17,
                      dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                                    "logits": {0: "batch"}})


class OrtScorer:
    def __init__(self, path, tokenizer_json, max_len, threads=4):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(tokenizer_json))
        self.tok.enable_truncation(max_length=max_len)
        self.tok.no_padding()
        self.names = [i.name for i in self.sess.get_inputs()]

    def logits(self, texts):
        encs = self.tok.encode_batch(list(texts))
        n = max(len(e.ids) for e in encs)
        ids = np.zeros((len(encs), n), dtype=np.int64)
        mask = np.zeros((len(encs), n), dtype=np.int64)
        for i, e in enumerate(encs):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        feeds = {"input_ids": ids, "attention_mask": mask}
        return self.sess.run(None, {k: feeds[k] for k in self.names})[0]

    def p_single(self, texts):
        return np.array([softmax(self.logits([t]))[0, 1] for t in texts])

    def p_batch(self, texts, bs=32):
        order = np.argsort([len(t) for t in texts])
        out = np.zeros(len(texts))
        for i in range(0, len(texts), bs):
            idx = order[i:i + bs]
            out[idx] = softmax(self.logits([texts[j] for j in idx]))[:, 1]
        return out


def quantize_candidates(fp32, workdir, calib_texts, tok_json, max_len, only: set[str] | None = None):
    from onnxruntime.quantization import (CalibrationDataReader, QuantFormat, QuantType, quantize_dynamic,
                                          quantize_static)
    from onnxruntime.quantization.shape_inference import quant_pre_process

    cands = {}
    want = (lambda name: only is None or name in only)
    pre = workdir / "model_pre.onnx"
    if any(want(n) for n in ("dynamic_int8", "static_int8", "static_int8_matmul")):
        quant_pre_process(str(fp32), str(pre), skip_symbolic_shape=True)

    # Dynamic INT8 (what v3 used): small and fast, but scores depend on the batch.
    if want("dynamic_int8"):
        p = workdir / "model_dynamic_int8.onnx"
        quantize_dynamic(str(pre), str(p), weight_type=QuantType.QInt8, per_channel=True)
        cands["dynamic_int8"] = p

    # Static INT8, QDQ, per-channel, calibrated on real texts (guide 3.3).
    from tokenizers import Tokenizer
    tk = Tokenizer.from_file(str(tok_json))
    tk.enable_truncation(max_length=max_len)

    class Reader(CalibrationDataReader):
        def __init__(self):
            self.it = iter(calib_texts)

        def get_next(self):
            t = next(self.it, None)
            if t is None:
                return None
            e = tk.encode(t)
            return {"input_ids": np.array([e.ids], dtype=np.int64), "attention_mask": np.array([e.attention_mask], dtype=np.int64)}

    for name, ops in (("static_int8", None), ("static_int8_matmul", ["MatMul", "Gather"])):
        if not want(name):
            continue
        p = workdir / f"model_{name}.onnx"
        try:
            quantize_static(str(pre), str(p), Reader(), quant_format=QuantFormat.QDQ, per_channel=True,
                            activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                            op_types_to_quantize=ops, extra_options={"ActivationSymmetric": False})
            cands[name] = p
        except Exception as exc:  # keep going: the report shows which ones exist
            print(f"   {name} failed: {type(exc).__name__}: {exc}")

    # FP32 compute with only the 128k x 768 word-embedding table stored as FP16 (390 -> 195 MB).
    # Measured for v4: 100% agreement, batch-invariant, same latency. (Weight-only INT8 via
    # MatMulNBits also agreed 100% but was 5x slower on CPU and barely smaller: see
    # quantize_weight_only.py.)
    if want("fp32_emb16"):
        from shrink_embeddings import shrink
        p = workdir / "model_fp32_emb16.onnx"
        shrink(fp32, p)
        cands["fp32_emb16"] = p
    return cands


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", default="D:/AI/bonc-v4/ckpt")
    ap.add_argument("--data", default="D:/AI/bonc-data")
    ap.add_argument("--work", default="D:/AI/bonc-v4/onnx")
    ap.add_argument("--bundle", default=str(HERE.parent / "flask_api" / "models" / "v4"))
    ap.add_argument("--version", default="deberta-v3-small-bonc-v4")
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--agree-n", type=int, default=3000, help="test texts used to compare quantized vs FP32")
    ap.add_argument("--candidates", nargs="+", default=["fp32_emb16"],
                    choices=["fp32_emb16", "dynamic_int8", "static_int8", "static_int8_matmul", "all"],
                    help="which quantizations to build and compare. fp32_emb16 has won every round "
                         "(the INT8 variants fail batch invariance or lose accuracy on DeBERTa) and each "
                         "rejected candidate costs several CPU-bound minutes, so only it is built by "
                         "default. Pass 'all' when the model or the runtime changes.")
    args = ap.parse_args()
    only = None if "all" in args.candidates else set(args.candidates)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tok = AutoTokenizer.from_pretrained(args.ckpt)
    model = AutoModelForSequenceClassification.from_pretrained(args.ckpt).to(device)
    val = pd.read_csv(Path(args.data) / "val.csv", keep_default_na=False)
    test = pd.read_csv(Path(args.data) / "test.csv", keep_default_na=False)
    hand = pd.read_csv(HERE / "eval_handwritten.csv", keep_default_na=False)

    # 1. temperature
    vl = torch_logits(model, tok, val["text"].tolist(), device, args.max_len)
    vy = val["label"].to_numpy()
    T = fit_temperature(vl, vy)
    print(f"temperature T = {T:.4f}; val ECE {ece(softmax(vl)[:, 1], vy):.4f} -> {ece(softmax(vl / T)[:, 1], vy):.4f}")

    # 2. test metrics (PyTorch, calibrated)
    metrics = {"temperature": T}
    tl = torch_logits(model, tok, test["text"].tolist(), device, args.max_len)
    pt = softmax(tl / T)[:, 1]
    metrics["test"] = report("test split", pt, test["label"].to_numpy(), test["source"] + ":" + test["category"])
    report("test split by kind", pt, test["label"].to_numpy(), test["kind"])
    hl = torch_logits(model, tok, hand["text"].tolist(), device, args.max_len)
    ph = softmax(hl / T)[:, 1]
    metrics["handwritten"] = report("hand-written eval set (held out)", ph, hand["label"].to_numpy(), hand["group"])
    wrong = hand.assign(p=ph)[(ph > THRESHOLD) != (hand["label"] == 1)]
    print("\nhand-written mistakes:\n" + (wrong[["label", "p", "text"]].to_string() if len(wrong) else "none"))
    for extra in ("eval_handwritten_2", "eval_dismissive", "eval_articles"):
        d = pd.read_csv(HERE / f"{extra}.csv", keep_default_na=False)
        pe = softmax(torch_logits(model, tok, d["text"].tolist(), device, args.max_len) / T)[:, 1]
        metrics[extra] = report(f"{extra} (held out)", pe, d["label"].to_numpy(), d["group"])
        wrong = d.assign(p=pe)[(pe > THRESHOLD) != (d["label"] == 1)]
        print(f"\n{extra} mistakes:\n" + (wrong[["label", "p", "text"]].to_string() if len(wrong) else "none"))

    # 3. ONNX FP32
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    tok_dir = work / "tok"
    tok.save_pretrained(tok_dir)
    tok_json = tok_dir / "tokenizer.json"
    fp32 = work / "model_fp32.onnx"
    export_onnx(model, tok, T, fp32)
    print(f"\nexported {fp32} ({fp32.stat().st_size / 1e6:.0f} MB)")

    # 4. quantize + compare
    rng = np.random.default_rng(4)
    calib = list(rng.choice(val["text"].to_numpy(), 300, replace=False))
    sents = [s for t in val["text"].sample(200, random_state=4) for s in t.split(". ")[:2] if len(s.split()) > 2]
    calib += sents[:200]
    cands = {"fp32": fp32, **quantize_candidates(fp32, work, calib, tok_json, args.max_len, only)}

    agree_texts = test["text"].sample(min(args.agree_n, len(test)), random_state=4).tolist() + hand["text"].tolist()
    inv_texts = hand["text"].tolist()[:64]
    ref = OrtScorer(fp32, tok_json, args.max_len)
    p_ref = ref.p_batch(agree_texts)
    results = {}
    for name, path in cands.items():
        s = OrtScorer(path, tok_json, args.max_len)
        p = s.p_batch(agree_texts)
        single, batched = s.p_single(inv_texts), s.p_batch(inv_texts, bs=64)
        t0 = time.perf_counter()
        for t in inv_texts[:40]:
            s.logits([t])
        ms = (time.perf_counter() - t0) / 40 * 1000
        t0 = time.perf_counter()
        s.p_batch(inv_texts[:40], bs=40)
        ms_b = (time.perf_counter() - t0) * 1000
        results[name] = {
            "agreement": float(((p > THRESHOLD) == (p_ref > THRESHOLD)).mean()),
            "max_abs_diff_vs_fp32": float(np.abs(p - p_ref).max()),
            "batch_max_abs_diff": float(np.abs(single - batched).max()),
            "latency_ms_single": ms, "latency_ms_batch40": ms_b,
            "size_mb": path.stat().st_size / 1e6,
            "accuracy_handwritten": float(((s.p_single(hand["text"].tolist()) > THRESHOLD) == (hand["label"] == 1)).mean()),
        }
        print(f"   {name}: " + ", ".join(f"{k} {v:.4f}" for k, v in results[name].items()), flush=True)

    ok = {n: r for n, r in results.items() if r["agreement"] >= 0.99 and r["batch_max_abs_diff"] <= 1e-3}
    chosen = min(ok, key=lambda n: results[n]["size_mb"])
    print(f"\nchosen: {chosen} (passes >=99% agreement with FP32 and batch invariance; smallest)")

    # 5. bundle
    bundle = Path(args.bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    for f in bundle.glob("*.onnx"):
        f.unlink()
    model_file = "model.onnx"
    shutil.copy(cands[chosen], bundle / model_file)
    shutil.copy(tok_json, bundle / "tokenizer.json")
    meta = {
        "version": args.version,
        "labels": LABELS,
        "safe_label": "safe",
        "max_length": args.max_len,
        "activation": "softmax",
        "pad_to_max_length": False,
        "model_file": model_file,
        "label_weights": {"safe": 0.0, "unsafe": 1.0},
        "batch_invariant": True,
        "recommended_thresholds": {"allow_max": THRESHOLD, "reject_min": THRESHOLD},
        "risk": "P(unsafe) after temperature scaling (baked into the graph): <= 0.5 allow, > 0.5 reject",
        "temperature": round(T, 6),
        "quantization": chosen,
        "quantization_note": "Smallest candidate that agrees with FP32 on >= 99% of decisions AND is batch-invariant "
                             "(needed to batch sentences/windows/word variants). fp32_emb16 = FP32 compute with only "
                             "the word-embedding table stored as FP16. Dynamic INT8 drifts with batching; static INT8 "
                             "loses accuracy on DeBERTa and is slower on CPU.",
        "quantization_report": results,
        "preprocessing": "none: raw text -> tokenizer.json, truncated to max_length, no lowercasing",
        "training": "microsoft/deberta-v3-small fine-tuned on res/ (relabelled, see training/prepare_data.py) "
                    "+ synthetic_v4 (training/synth_data.py); binary safe/unsafe",
        "metrics": metrics,
    }
    (bundle / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote bundle to {bundle}: {sorted(p.name for p in bundle.iterdir())}")


if __name__ == "__main__":
    main()
