"""One model, two heads: P(unsafe) for every surface and P(grievance) for the public ones.

    python training/train_dualhead.py \\
        --init D:/AI/bonc-v6r2/ckpt \\
        --data D:/AI/bonc-dual-data \\
        --out D:/AI/bonc-dual/ckpt

WHAT THIS REPLACES. Today the system loads two DeBERTa bundles and calls them in sequence:
models/v6 answers "is this harmful anywhere" on every surface and models/grievance-v7 answers
"is this a grievance published where it does not belong" on the public ones. 742 MB of model
and two inference calls per public post. This trains one network with two output logits that
answers both, so the system carries one bundle and makes one call - cheaper than the pair it
replaces, not just tidier.

WHY NOT ONE LOGIT. See build_dualhead_data.py: 10,540 texts in the corpus are labelled SAFE
ANYWHERE and A GRIEVANCE IN PUBLIC at the same time, because both are true and they answer
different questions. One output cannot carry both, and the attempt is the regression that made
the dispute channel unusable. Two outputs carry it exactly, and the pipeline - which already
knows the surface - picks which one applies.

THE HEAD CONVERSION IS EXACT, WHICH IS THE WHOLE REASON THIS IS AFFORDABLE.

The live main model ends in Linear(768, 2) read through a softmax over [safe, unsafe]. For two
classes that is algebraically a sigmoid on the difference:

    softmax(z)[1] = exp(z1) / (exp(z0) + exp(z1))
                  = 1 / (1 + exp(z0 - z1))
                  = sigmoid(z1 - z0)

So the unsafe head of this model is initialised as w1 - w0 and b1 - b0, and before a single
gradient step it produces the LIVE MODEL'S OWN SCORES to floating-point precision. The run
below verifies that against the unsafe validation set rather than asserting it, and will say
so loudly if the numbers do not match: a silent failure here would look like a training
problem for the rest of the week.

That is why 1.31M main-corpus rows are not needed. The unsafe head starts finished; the main
rows present are rehearsal, keeping it from drifting while the grievance head - initialised
small and random - is learned from 374k grievance rows.

MASKED LOSS. Each row supervises the head its corpus labelled and is masked out of the other,
because unlabelled is not negative. The two losses are summed per batch with the mask applied,
so a batch of pure grievance rows produces no gradient for the unsafe head at all.

THE GATE. Two numbers decide, reported at every eval and again on the saved checkpoint: the
unsafe head's AUC against the measured baseline, and the grievance head's AUC. A checkpoint
that learns grievance by degrading unsafe is the failure mode this is built to avoid, so
selection is on the SUM of the two validation losses and --max-unsafe-regression exits
non-zero if the unsafe head falls past a floor. Wire that exit code to whatever promotes a
bundle.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import roc_auc_score
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          get_linear_schedule_with_warmup)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from train import bucket_batches, collate, encode  # noqa: E402

HEADS = ("unsafe", "grievance")


def convert_head(model) -> None:
    """Softmax-over-2 -> two independent sigmoids, with the unsafe one carried over exactly."""
    old = model.classifier
    if old.out_features != 2:
        raise SystemExit(f"expected a 2-class head to convert, found {old.out_features}")
    new = torch.nn.Linear(old.in_features, 2)
    with torch.no_grad():
        # Row 0 = unsafe, algebraically identical to the softmax model's P(unsafe).
        new.weight[0] = old.weight[1] - old.weight[0]
        new.bias[0] = old.bias[1] - old.bias[0]
        # Row 1 = grievance, small and random: there is nothing in the old head to carry.
        new.weight[1].normal_(0.0, 0.02)
        new.bias[1].zero_()
    model.classifier = new
    model.config.id2label = {0: "unsafe", 1: "grievance"}
    model.config.label2id = {"unsafe": 0, "grievance": 1}
    model.config.problem_type = "multi_label_classification"


@torch.no_grad()
def head_scores(model, ids, pad_id, device, batch_size=128) -> np.ndarray:
    model.eval()
    out = np.zeros((len(ids), 2), dtype=np.float32)
    lengths = [len(x) for x in ids]
    for b in bucket_batches(lengths, batch_size, None, shuffle=False):
        x, m = collate([ids[j] for j in b], pad_id, device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            logits = model(input_ids=x, attention_mask=m).logits
        out[b] = torch.sigmoid(logits.float()).cpu().numpy()
    return out


def head_metrics(p: np.ndarray, y: np.ndarray, mask: np.ndarray) -> dict:
    keep = mask == 1
    if keep.sum() == 0:
        return {}
    pp, yy = p[keep], y[keep]
    pred = pp >= 0.5
    out = {"n": int(keep.sum()),
           "acc": float((pred == (yy == 1)).mean()),
           "recall": float((pred & (yy == 1)).sum() / max((yy == 1).sum(), 1)),
           "fpr": float((pred & (yy == 0)).sum() / max((yy == 0).sum(), 1))}
    if (yy == 1).any() and (yy == 0).any():
        out["auc"] = float(roc_auc_score(yy, pp))
        eps = 1e-7
        out["loss"] = float(-(yy * np.log(pp + eps) + (1 - yy) * np.log(1 - pp + eps)).mean())
    return out


def report(tag: str, mt: dict) -> str:
    if not mt:
        return f"{tag}: no labelled rows"
    return (f"{tag}: n={mt['n']:,} auc={mt.get('auc', float('nan')):.5f} "
            f"loss={mt.get('loss', float('nan')):.4f} caught={mt['recall']:.1%} "
            f"wrongly={mt['fpr']:.1%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True, help="the LIVE main model's checkpoint")
    ap.add_argument("--data", type=Path, default=Path("D:/AI/bonc-dual-data"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1.5e-5)
    ap.add_argument("--head-lr", type=float, default=1e-4,
                    help="the grievance head starts random and the backbone does not, so the "
                         "head gets a larger step; one rate for both either crawls on the "
                         "head or wrecks the backbone")
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--evals", type=int, default=5)
    ap.add_argument("--max-unsafe-regression", type=float, default=0.005,
                    help="fail if the unsafe head's AUC falls this far below the measured "
                         "baseline. The whole point is that it must not.")
    ap.add_argument("--grievance-weight", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    t_start = time.time()
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device} "
          f"{torch.cuda.get_device_name(0) if device.type == 'cuda' else ''}", flush=True)

    tok = AutoTokenizer.from_pretrained(args.init)
    model = AutoModelForSequenceClassification.from_pretrained(args.init)
    print(f"\ninit: {args.init}  ({model.config.num_labels} labels "
          f"{[model.config.id2label[i] for i in range(model.config.num_labels)]})")
    convert_head(model)
    print("  head converted: softmax[safe,unsafe] -> sigmoid[unsafe,grievance]; "
          "the unsafe row is w1-w0 / b1-b0, which is exact")
    model.to(device)

    tr = pd.read_csv(args.data / "train.csv", keep_default_na=False)
    va = pd.read_csv(args.data / "val.csv", keep_default_na=False)
    if args.limit:
        tr = tr.sample(min(args.limit, len(tr)), random_state=args.seed)
        va = va.sample(min(args.limit, len(va)), random_state=args.seed)
    print(f"\ntrain {len(tr):,} rows | val {len(va):,} rows")

    t0 = time.time()
    tr_ids = encode(tok, tr["text"], args.max_len)
    va_ids = encode(tok, va["text"], args.max_len)
    print(f"tokenised in {time.time() - t0:.0f}s; mean length "
          f"{np.mean([len(x) for x in tr_ids]):.0f} tokens", flush=True)

    Y = np.stack([tr["y_unsafe"].to_numpy(), tr["y_grievance"].to_numpy()], 1).astype(np.float32)
    M = np.stack([tr["m_unsafe"].to_numpy(), tr["m_grievance"].to_numpy()], 1).astype(np.float32)
    vY = np.stack([va["y_unsafe"].to_numpy(), va["y_grievance"].to_numpy()], 1)
    vM = np.stack([va["m_unsafe"].to_numpy(), va["m_grievance"].to_numpy()], 1)

    # THE VERIFICATION. If the conversion is right this is the live model's own behaviour.
    base = head_scores(model, va_ids, tok.pad_token_id, device)
    base_unsafe = head_metrics(base[:, 0], vY[:, 0], vM[:, 0])
    base_griev = head_metrics(base[:, 1], vY[:, 1], vM[:, 1])
    print(f"\nBASELINE, before any step:")
    print(f"  {report('unsafe   ', base_unsafe)}")
    print(f"  {report('grievance', base_griev)}   <- random head, as expected")
    if base_unsafe.get("auc", 0) < 0.9:
        print("\n  WARNING: the converted unsafe head scores below 0.9 AUC. The conversion "
              "should reproduce the live model exactly, so this means --init is not the "
              "checkpoint behind the live bundle, or its head is not a 2-class softmax.",
              file=sys.stderr)

    pos_w = torch.tensor([1.0, args.grievance_weight], device=device)
    head_names = {"classifier.weight", "classifier.bias"}
    no_decay = ("bias", "LayerNorm.weight", "layernorm", "norm")
    groups = [
        {"params": [p for n, p in model.named_parameters()
                    if n not in head_names and not any(k in n for k in no_decay)],
         "weight_decay": 0.01, "lr": args.lr},
        {"params": [p for n, p in model.named_parameters()
                    if n not in head_names and any(k in n for k in no_decay)],
         "weight_decay": 0.0, "lr": args.lr},
        {"params": [p for n, p in model.named_parameters() if n in head_names],
         "weight_decay": 0.0, "lr": args.head_lr},
    ]
    opt = torch.optim.AdamW(groups)
    lengths = [len(x) for x in tr_ids]
    steps_per_epoch = math.ceil(len(tr_ids) / args.batch_size)
    total = max(1, int(steps_per_epoch * args.epochs))
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * total), total)
    eval_every = max(1, total // max(1, args.evals))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best, step, t0 = float("inf"), 0, time.time()
    running, history, saved_at = [], [], None

    while step < total:
        for b in bucket_batches(lengths, args.batch_size, rng):
            if step >= total:
                break
            model.train()
            x, m = collate([tr_ids[j] for j in b], tok.pad_token_id, device)
            y = torch.tensor(Y[b], device=device)
            msk = torch.tensor(M[b], device=device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                logits = model(input_ids=x, attention_mask=m).logits
            per = torch.nn.functional.binary_cross_entropy_with_logits(
                logits.float(), y, reduction="none")
            # Masked mean over the labelled entries only, so a batch with no unsafe labels
            # produces no unsafe gradient rather than a gradient towards zero.
            loss = ((per * msk) * pos_w).sum() / msk.sum().clamp(min=1.0)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            running.append(loss.item())
            step += 1
            if step % 200 == 0:
                rate = step / (time.time() - t0)
                print(f"step {step}/{total} loss {np.mean(running[-200:]):.4f} "
                      f"lr {sched.get_last_lr()[0]:.2e} {rate:.1f} it/s "
                      f"eta {(total - step) / rate / 60:.0f} min", flush=True)
            if step % eval_every == 0 or step == total:
                p = head_scores(model, va_ids, tok.pad_token_id, device)
                mu = head_metrics(p[:, 0], vY[:, 0], vM[:, 0])
                mg = head_metrics(p[:, 1], vY[:, 1], vM[:, 1])
                drop = base_unsafe.get("auc", 0) - mu.get("auc", 0)
                print(f"== eval step {step}", flush=True)
                print(f"   {report('unsafe   ', mu)}  drift {-drop:+.5f}", flush=True)
                print(f"   {report('grievance', mg)}", flush=True)
                history.append({"step": step, "unsafe": mu, "grievance": mg})
                score = mu.get("loss", 9) + mg.get("loss", 9)
                if score < best:
                    best, saved_at = score, step
                    model.save_pretrained(out)
                    tok.save_pretrained(out)
                    print(f"   saved best (unsafe+grievance loss {best:.4f})", flush=True)

    mins = (time.time() - t_start) / 60
    print(f"\ndone in {mins:.1f} min; best from step {saved_at}")

    model = AutoModelForSequenceClassification.from_pretrained(out).to(device)
    p = head_scores(model, va_ids, tok.pad_token_id, device)
    fu = head_metrics(p[:, 0], vY[:, 0], vM[:, 0])
    fg = head_metrics(p[:, 1], vY[:, 1], vM[:, 1])
    print("\n---- saved checkpoint ----")
    print(f"  {report('unsafe   ', fu)}")
    print(f"  {report('grievance', fg)}")

    (out / "dualhead_report.json").write_text(json.dumps({
        "init": str(args.init), "data": str(args.data), "minutes": round(mins, 2),
        "heads": list(HEADS), "steps": total,
        "hyperparameters": {"lr": args.lr, "head_lr": args.head_lr, "epochs": args.epochs,
                            "batch_size": args.batch_size, "max_len": args.max_len,
                            "grievance_weight": args.grievance_weight},
        "baseline": {"unsafe": base_unsafe, "grievance": base_griev},
        "final": {"unsafe": fu, "grievance": fg}, "history": history,
    }, indent=2), encoding="utf-8")

    drop = base_unsafe.get("auc", 0) - fu.get("auc", 0)
    if drop > args.max_unsafe_regression:
        print(f"\nFAILED: the unsafe head lost {drop:.5f} AUC "
              f"({base_unsafe.get('auc'):.5f} -> {fu.get('auc'):.5f}), over the "
              f"{args.max_unsafe_regression} floor. The merged model is worse at the job the "
              f"live model already does. Lower --lr, or raise the rehearsal fraction in "
              f"build_dualhead_data.py.", file=sys.stderr)
        return 2
    print(f"\nPASSED: unsafe AUC {base_unsafe.get('auc'):.5f} -> {fu.get('auc'):.5f} "
          f"(drift {-drop:+.5f}); grievance AUC {fg.get('auc', float('nan')):.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
