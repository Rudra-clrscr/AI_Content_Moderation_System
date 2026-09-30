"""Fine-tune DeBERTa-v3-small as the bonc-v4 binary moderation model (GPU).

    python train.py --data D:/AI/bonc-data --out D:/AI/bonc-v4/ckpt

Labels: 0 = safe, 1 = unsafe. The risk score the API uses is P(unsafe) after
temperature scaling (calibrate_export.py), so a risk of 0.5 means "as likely harmful
as not" and the API's single 0.5 boundary is meaningful.

Plain PyTorch loop: bf16 autocast on CUDA, AdamW, linear warm-up/decay, length-bucketed
batches (most texts are short, so padding to the longest in a random batch wastes time).
The best checkpoint by validation loss is kept.
"""
from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import f1_score, roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

BASE = "microsoft/deberta-v3-small"
LABELS = ["safe", "unsafe"]


def encode(tok, texts, max_len):
    enc = tok(list(texts), truncation=True, max_length=max_len)
    return enc["input_ids"]


def bucket_batches(lengths, batch_size, rng, shuffle=True):
    idx = list(range(len(lengths)))
    if shuffle:
        rng.shuffle(idx)
    chunk = batch_size * 64
    batches = []
    for i in range(0, len(idx), chunk):
        part = sorted(idx[i:i + chunk], key=lambda j: lengths[j])
        batches += [part[k:k + batch_size] for k in range(0, len(part), batch_size)]
    if shuffle:
        rng.shuffle(batches)
    return batches


def collate(ids_list, pad_id, device):
    n = max(len(x) for x in ids_list)
    ids = torch.full((len(ids_list), n), pad_id, dtype=torch.long)
    mask = torch.zeros((len(ids_list), n), dtype=torch.long)
    for i, x in enumerate(ids_list):
        ids[i, :len(x)] = torch.tensor(x)
        mask[i, :len(x)] = 1
    return ids.to(device, non_blocking=True), mask.to(device, non_blocking=True)


@torch.no_grad()
def predict_logits(model, ids, pad_id, device, batch_size=128):
    model.eval()
    out = np.zeros((len(ids), len(LABELS)), dtype=np.float32)
    lengths = [len(x) for x in ids]
    for b in bucket_batches(lengths, batch_size, None, shuffle=False):
        x, m = collate([ids[j] for j in b], pad_id, device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            logits = model(input_ids=x, attention_mask=m).logits
        out[b] = logits.float().cpu().numpy()
    return out


def metrics(logits, y):
    z = logits - logits.max(1, keepdims=True)
    p = np.exp(z) / np.exp(z).sum(1, keepdims=True)
    nll = float(-np.log(p[np.arange(len(y)), y] + 1e-12).mean())
    pred = (p[:, 1] > 0.5).astype(int)
    return {"loss": nll, "acc": float((pred == y).mean()), "f1": float(f1_score(y, pred)),
            "auc": float(roc_auc_score(y, p[:, 1]))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="D:/AI/bonc-data")
    ap.add_argument("--out", default="D:/AI/bonc-v4/ckpt")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--evals-per-epoch", type=int, default=2)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="debug: use only this many training rows")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device} {torch.cuda.get_device_name(0) if device.type == 'cuda' else ''}", flush=True)

    tok = AutoTokenizer.from_pretrained(BASE)
    train = pd.read_csv(Path(args.data) / "train.csv", keep_default_na=False)
    val = pd.read_csv(Path(args.data) / "val.csv", keep_default_na=False)
    if args.limit:
        train = train.sample(args.limit, random_state=args.seed)
    t0 = time.time()
    tr_ids, va_ids = encode(tok, train["text"], args.max_len), encode(tok, val["text"], args.max_len)
    tr_y, va_y = train["label"].to_numpy(), val["label"].to_numpy()
    print(f"tokenised {len(tr_ids)} train / {len(va_ids)} val in {time.time() - t0:.0f}s; "
          f"mean train length {np.mean([len(x) for x in tr_ids]):.0f} tokens", flush=True)

    model = AutoModelForSequenceClassification.from_pretrained(
        BASE, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id={l: i for i, l in enumerate(LABELS)})
    model.to(device)

    lengths = [len(x) for x in tr_ids]
    steps_per_epoch = math.ceil(len(tr_ids) / args.batch_size)
    total = int(steps_per_epoch * args.epochs)
    no_decay = ("bias", "LayerNorm.weight", "layernorm", "norm")
    params = [{"params": [p for n, p in model.named_parameters() if not any(k in n for k in no_decay)], "weight_decay": 0.01},
              {"params": [p for n, p in model.named_parameters() if any(k in n for k in no_decay)], "weight_decay": 0.0}]
    opt = torch.optim.AdamW(params, lr=args.lr)
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * total), total)
    eval_every = max(1, steps_per_epoch // args.evals_per_epoch)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best, step, t0 = float("inf"), 0, time.time()
    running = []
    while step < total:
        for b in bucket_batches(lengths, args.batch_size, rng):
            if step >= total:
                break
            model.train()
            x, m = collate([tr_ids[j] for j in b], tok.pad_token_id, device)
            y = torch.tensor(tr_y[b], device=device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                logits = model(input_ids=x, attention_mask=m).logits
            loss = torch.nn.functional.cross_entropy(logits.float(), y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            running.append(loss.item())
            step += 1
            if step % 200 == 0:
                rate = step / (time.time() - t0)
                print(f"step {step}/{total} loss {np.mean(running[-200:]):.4f} lr {sched.get_last_lr()[0]:.2e} "
                      f"{rate:.1f} it/s, eta {(total - step) / rate / 60:.0f} min", flush=True)
            if step % eval_every == 0 or step == total:
                mt = metrics(predict_logits(model, va_ids, tok.pad_token_id, device), va_y)
                print(f"== eval step {step}: {mt}", flush=True)
                if mt["loss"] < best:
                    best = mt["loss"]
                    model.save_pretrained(out)
                    tok.save_pretrained(out)
                    print(f"   saved best (val loss {best:.4f})", flush=True)
    print(f"done in {(time.time() - t0) / 60:.1f} min, best val loss {best:.4f}")


if __name__ == "__main__":
    main()
