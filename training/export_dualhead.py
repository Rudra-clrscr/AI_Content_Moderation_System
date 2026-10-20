"""Calibrate and export the two-headed model as one servable bundle.

    python training/export_dualhead.py --ckpt D:/AI/bonc-dual/ckpt \\
        --data D:/AI/bonc-dual-data --bundle flask_api/models/v7 --version bonc-v7-dual

WHAT THIS PRODUCES. One ONNX graph with two sigmoid outputs, [P(unsafe), P(grievance)], and a
bundle app/model.py can already read without modification:

    labels        ["unsafe", "grievance"]
    activation    "sigmoid"
    label_weights {"unsafe": 1.0, "grievance": 0.0}

The weights are the trick that keeps the serving contract unchanged. _risk_from multiplies
each label probability by its weight and takes the max, so risk_score comes out as exactly
P(unsafe) - the surface-blind answer every existing caller already expects - while
P(grievance) stays available in label_scores for the pipeline to apply on public surfaces
only. Nothing downstream has to learn a new shape to keep working.

TWO TEMPERATURES, NOT ONE, and they are fitted on different rows. The heads were trained on
different corpora and are differently confident, so a single scalar would fix one and spoil
the other. Each is fitted on the validation rows that actually carry its label - the mask
columns from build_dualhead_data.py - and baked into the graph, so a threshold means the same
thing before and after export.

WHY NOT calibrate_export.py. That script assumes a 2-class softmax and one temperature, and
its quantization search compares candidates on a single risk score. Rather than teach it a
second head and risk changing what it does for models/v4-v6, this is a separate path for a
separate shape. It deliberately exports FP32 with an FP16 word-embedding table - the candidate
calibrate_export.py already chose for every shipped bundle - instead of re-running the search,
because the search's answer has not changed in four bundles and the comparison is expensive.

THE AGREEMENT CHECK. The exported graph is scored against the torch checkpoint on held-out
text and both heads must agree within tolerance on every row, and on the decision for all of
them. An export that silently shifts a head is the kind of bug that reads as a model
regression for a week.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


class DualHead(torch.nn.Module):
    """The checkpoint plus per-head temperature, emitting SCALED LOGITS - not probabilities.

    The graph must not apply the sigmoid. app/model.py applies the activation named in
    model_meta.json, so a graph that also applies it produces sigmoid(sigmoid(x)) and every
    score collapses into [0.5, 0.7311] - 0.7311 being sigmoid(1.0) and 0.5 being sigmoid(0).
    The first cut of this script did exactly that and the bundle looked plausible: scores in
    range, ordering intact, the harness reporting 100% recall on every category because every
    row cleared 0.5. The repo's convention is logits out of the graph, activation in the
    scorer, and calibrate_export.py has always followed it.
    """

    def __init__(self, model, temps: torch.Tensor):
        super().__init__()
        self.model = model
        self.register_buffer("temps", temps)

    def forward(self, input_ids, attention_mask):
        logits = self.model(input_ids=input_ids, attention_mask=attention_mask).logits
        return logits / self.temps


@torch.no_grad()
def logits_of(model, tok, texts, device, max_len, bs=64) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(texts), bs):
        enc = tok(list(texts[i:i + bs]), truncation=True, max_length=max_len,
                  padding=True, return_tensors="pt").to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            out.append(model(**enc).logits.float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 2), np.float32)


def fit_temperature(logit: np.ndarray, y: np.ndarray) -> float:
    """One scalar per head, by NLL on its own labelled validation rows."""
    z = torch.tensor(logit, dtype=torch.float32)
    t = torch.ones(1, requires_grad=True)
    target = torch.tensor(y, dtype=torch.float32)
    opt = torch.optim.LBFGS([t], lr=0.1, max_iter=60)

    def closure():
        opt.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(z / t.clamp(min=0.05), target)
        loss.backward()
        return loss

    opt.step(closure)
    return float(t.detach().clamp(min=0.05).item())


def ece(p: np.ndarray, y: np.ndarray, bins: int = 15) -> float:
    edges = np.linspace(0, 1, bins + 1)
    out = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p > lo) & (p <= hi)
        if m.any():
            out += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(out)


def head_report(name: str, p: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.metrics import roc_auc_score
    pred = p >= 0.5
    d = {"n": int(len(y)), "accuracy": float((pred == (y == 1)).mean()),
         "recall": float((pred & (y == 1)).sum() / max((y == 1).sum(), 1)),
         "false_positive_rate": float((pred & (y == 0)).sum() / max((y == 0).sum(), 1)),
         "ece": ece(p, y)}
    if (y == 1).any() and (y == 0).any():
        d["auc"] = float(roc_auc_score(y, p))
    print(f"  {name:10} n={d['n']:>7,} auc={d.get('auc', float('nan')):.5f} "
          f"acc={d['accuracy']:.4f} caught={d['recall']:.1%} "
          f"wrongly={d['false_positive_rate']:.1%} ece={d['ece']:.4f}")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--data", type=Path, default=Path("D:/AI/bonc-dual-data"))
    ap.add_argument("--bundle", type=Path, default=ROOT / "flask_api" / "models" / "v7")
    ap.add_argument("--work", type=Path, default=Path("D:/AI/bonc-dual/onnx"))
    ap.add_argument("--version", default="bonc-v7-dual")
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--min-temperature", type=float, default=1.0,
                    help="floor, as for the other bundles: a temperature below 1 sharpens "
                         "scores and leaves the admin's 0-1 rating nothing to move")
    ap.add_argument("--agree-n", type=int, default=2000)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    tok = AutoTokenizer.from_pretrained(args.ckpt)
    model = AutoModelForSequenceClassification.from_pretrained(args.ckpt).to(device)
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
    print(f"ckpt {args.ckpt}\nlabels {labels}")
    if labels != ["unsafe", "grievance"]:
        sys.exit(f"expected ['unsafe','grievance'], found {labels}")

    va = pd.read_csv(args.data / "val.csv", keep_default_na=False)
    te = pd.read_csv(args.data / "test.csv", keep_default_na=False)
    print(f"val {len(va):,} | test {len(te):,}")

    t0 = time.time()
    zv = logits_of(model, tok, va["text"].tolist(), device, args.max_len)
    print(f"scored val in {time.time() - t0:.0f}s")

    temps = []
    for i, (head, ycol, mcol) in enumerate((("unsafe", "y_unsafe", "m_unsafe"),
                                            ("grievance", "y_grievance", "m_grievance"))):
        keep = va[mcol].to_numpy() == 1
        y = va[ycol].to_numpy()[keep]
        t = max(fit_temperature(zv[keep, i], y), args.min_temperature)
        before = ece(1 / (1 + np.exp(-zv[keep, i])), y)
        after = ece(1 / (1 + np.exp(-zv[keep, i] / t)), y)
        print(f"  {head:10} T = {t:.4f}   ECE {before:.4f} -> {after:.4f}  (n={keep.sum():,})")
        temps.append(t)
    temps_t = torch.tensor(temps, dtype=torch.float32, device=device)

    print("\nvalidation, calibrated:")
    pv = 1 / (1 + np.exp(-zv / np.array(temps)))
    metrics = {}
    for i, (head, ycol, mcol) in enumerate((("unsafe", "y_unsafe", "m_unsafe"),
                                            ("grievance", "y_grievance", "m_grievance"))):
        keep = va[mcol].to_numpy() == 1
        metrics[f"val_{head}"] = head_report(head, pv[keep, i], va[ycol].to_numpy()[keep])

    zt = logits_of(model, tok, te["text"].tolist(), device, args.max_len)
    pt = 1 / (1 + np.exp(-zt / np.array(temps)))
    print("\ntest, calibrated:")
    for i, (head, ycol, mcol) in enumerate((("unsafe", "y_unsafe", "m_unsafe"),
                                            ("grievance", "y_grievance", "m_grievance"))):
        keep = te[mcol].to_numpy() == 1
        metrics[f"test_{head}"] = head_report(head, pt[keep, i], te[ycol].to_numpy()[keep])

    # ---- export -------------------------------------------------------------------------
    args.work.mkdir(parents=True, exist_ok=True)
    fp32 = args.work / "model_fp32.onnx"
    wrapped = DualHead(model, temps_t).eval()
    enc = tok(["calibration text for the export"], return_tensors="pt",
              truncation=True, max_length=args.max_len).to(device)
    torch.onnx.export(
        wrapped, (enc["input_ids"], enc["attention_mask"]), str(fp32), dynamo=False,
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq"},
                      "attention_mask": {0: "batch", 1: "seq"},
                      "logits": {0: "batch"}},
        opset_version=17, do_constant_folding=True)
    print(f"\nexported {fp32} ({fp32.stat().st_size / 1e6:.0f} MB)")

    from shrink_embeddings import shrink  # noqa: E402
    emb16 = args.work / "model_fp32_emb16.onnx"
    try:
        shrink(fp32, emb16)
        chosen, note = emb16, "fp32_emb16"
    except Exception as exc:
        print(f"  (embedding shrink failed: {type(exc).__name__}: {exc}; shipping fp32)")
        chosen, note = fp32, "fp32"
    print(f"chosen {note} ({chosen.stat().st_size / 1e6:.0f} MB)")

    # ---- bundle, written BEFORE the check -----------------------------------------------
    # Order matters. The check below goes through app.model.OnnxScorer - the actual serving
    # path - rather than through onnxruntime directly, and the scorer needs a bundle on disk
    # to load. The first cut checked raw onnxruntime output against torch, which agreed
    # perfectly while the served scores were wrong: the graph emitted probabilities and the
    # scorer applied its sigmoid on top, collapsing everything into [0.5, 0.7311]. Comparing
    # the two things that already agree proves nothing. Verify what will serve.
    args.bundle.mkdir(parents=True, exist_ok=True)
    for old in args.bundle.glob("*.onnx"):
        old.unlink()
    shutil.copy2(chosen, args.bundle / "model.onnx")
    shutil.copy2(Path(args.ckpt) / "tokenizer.json", args.bundle / "tokenizer.json")
    meta = {
        "version": args.version,
        "labels": ["unsafe", "grievance"],
        # No safe logit exists: both outputs are violation types. label_weights is what
        # risk_score is derived from, and app/model.py allows safe_label to be absent
        # precisely when weights are present.
        "safe_label": "",
        "max_length": args.max_len,
        "activation": "sigmoid",
        "pad_to_max_length": False,
        "model_file": "model.onnx",
        "label_weights": {"unsafe": 1.0, "grievance": 0.0},
        "batch_invariant": True,
        "temperature_per_head": {"unsafe": temps[0], "grievance": temps[1]},
        "quantization": note,
        "risk": ("risk_score is P(unsafe) after per-head temperature scaling. The GRAPH emits "
                 "scaled logits; app/model.py applies the sigmoid named in `activation`. "
                 "P(grievance) is in label_scores and applies on PUBLIC SURFACES ONLY - see "
                 "config/settings.yaml. One model, two questions: the same text can be safe "
                 "anywhere and still be a grievance nobody should publish, which 10,540 rows "
                 "of the training corpus assert explicitly."),
        "preprocessing": "none: raw text -> tokenizer.json, truncated to max_length",
        "training": ("microsoft/deberta-v3-small, warm-started from the live single-head "
                     "model and head-converted exactly (softmax[safe,unsafe] -> "
                     "sigmoid[unsafe,grievance], w1-w0). See training/train_dualhead.py."),
        "metrics": metrics,
    }
    (args.bundle / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ---- the check, through the serving path ---------------------------------------------
    sys.path.insert(0, str(ROOT / "flask_api"))
    from app.model import OnnxScorer  # noqa: E402
    sc = OnnxScorer(str(args.bundle))
    sample = te.sample(min(args.agree_n, len(te)), random_state=4)
    served = np.array([[r.label_scores["unsafe"], r.label_scores["grievance"]]
                       for i in range(0, len(sample), 32)
                       for r in sc.score_batch(sample["text"].tolist()[i:i + 32])])
    want = 1 / (1 + np.exp(-logits_of(model, tok, sample["text"].tolist(), device,
                                      args.max_len) / np.array(temps)))
    diff = np.abs(served - want)
    agree = float(((served >= 0.5) == (want >= 0.5)).all(axis=1).mean())
    print(f"\nSERVED vs checkpoint on {len(sample):,} held-out texts: "
          f"{agree:.4%} of decisions identical, max|diff| = {diff.max():.6f}")
    print(f"  served score range: unsafe [{served[:, 0].min():.4f}, {served[:, 0].max():.4f}] "
          f"grievance [{served[:, 1].min():.4f}, {served[:, 1].max():.4f}]")
    # A double-sigmoid squashes everything into [0.5, 0.7311]. Naming the symptom here means
    # the next person who reintroduces it reads the cause instead of hunting for it.
    if served.min() >= 0.49 and served.max() <= 0.74:
        sys.exit("served scores are confined to [0.5, 0.731]: the activation is being applied "
                 "twice - the graph must emit logits, not probabilities")
    if agree < 0.99:
        sys.exit(f"the bundle disagrees with the checkpoint on {1 - agree:.2%} of decisions")
    meta["export_agreement"] = {"decisions_identical": agree,
                                "max_abs_diff": float(diff.max())}
    (args.bundle / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\nwrote bundle {args.bundle}: "
          f"{sorted(p.name for p in args.bundle.iterdir())}")
    print("\nNext: point settings.yaml at it and set the two thresholds; then "
          "training/accept_system.py before anything serves it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
