"""Add a category to an already fine-tuned checkpoint without retraining on everything.

    python training/train_incremental.py \
        --init D:/AI/bonc-grievance-v6/ckpt \
        --new res/workplace_grievances.csv \
        --replay D:/AI/bonc-grievance-data-v6 \
        --out D:/AI/bonc-grievance-v7/ckpt

WHAT THIS IS FOR. The admin tab is getting an upload: a new category arrives as a CSV and
training starts. Doing that the way training/train.py does it - from microsoft/deberta-v3-small
over the full corpus - takes the better part of an hour on this machine and gets slower every
time a category is added, because the full corpus is what grows. This trains the model that is
already in service on the new rows plus a small sample of the old ones, which is minutes, and
stays minutes however many categories accumulate.

    train.py            BASE -> full corpus (300k+ rows, 2 epochs)      ~45 min, grows
    train_incremental   ckpt -> new rows + replay sample                ~minutes, flat

WHY A REPLAY SAMPLE AND NOT JUST THE NEW ROWS. Fine-tuning a network on data drawn from one
region of the input space moves every weight, including the ones carrying the other regions.
Train this checkpoint on 100,000 workplace rows alone and it will learn workplace grievance and
partly forget supplier grievance - the thing it was built for. That is catastrophic forgetting,
it is not subtle, and it is invisible in the training loss, which only ever sees the new rows.

The cheap fix is rehearsal: mix in a fraction of the old training data. 10-20% is enough in
practice, and 15% of 300,000 rows is 45,000 - a fifth of a full retrain's work, not a whole
one. `--replay-frac 0` is available and the run will tell you what it cost.

THE NUMBER THAT DECIDES, AND WHY THE ADMIN FLOW MUST GATE ON IT.

This script measures the OLD validation set twice: once with the incoming checkpoint before a
single step is taken, and again on every eval. The first is the baseline, and it is measured
rather than read out of a log, because the log belongs to a different run on a different
machine. The second is whether the model still does its old job.

    old-val AUC 0.9997 -> 0.9994    fine, the new category cost nothing
    old-val AUC 0.9997 -> 0.9120    the upload has damaged the deployed capability

`--max-regression` is a hard floor on that drop, and breaching it exits non-zero with the
numbers printed. Wire the admin tab to that exit code. An upload that silently degrades the
live model is strictly worse than an upload that fails, because the failure is visible the
same afternoon and the degradation turns up weeks later as "moderation feels off".

Checkpoint selection is by the SUM of the two validation losses, never the new one alone. A
model that nails the new category by forgetting the old one wins on new-val loss, so selecting
on new-val loss would actively pick the damaged checkpoint.

ADDING A CATEGORY IS USUALLY NOT ADDING A LABEL, and the admin UI should know the difference.

  the common case   The decision stays binary - allow or refuse. The new category is a new
                    REGION of label 1 (or 0): workplace grievance is refused for the same
                    reason supplier grievance is. Nothing about the head, the ONNX output
                    shape, the calibration or app/grievance.py changes. Upload the CSV with
                    labels 0/1 and a `category` column for provenance, and this runs.

  the rare case     The admin wants a new OUTPUT class the API will report separately.
                    `--add-labels harassment spam` grows the classification head, copying the
                    existing rows of the weight matrix so the old classes keep their meaning
                    and initialising the new rows small. This changes the serving contract -
                    output shape, temperature calibration, every consumer of the score - so it
                    is deliberate, flagged, and not something an upload should trigger by
                    accident. Default is off, and the script refuses to infer it from the CSV.

WHAT THIS DOES NOT DO. It does not export, calibrate or deploy. The checkpoint it writes goes
to training/calibrate_export.py exactly as train.py's does, and the bundle is accepted or
rejected by training/accept_model.py. Same downstream path, same gates, one stage swapped.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          get_linear_schedule_with_warmup)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

# Imported rather than copied, so an incremental run and a full run cannot disagree about how a
# batch is built, how a split is drawn, or what a metric means. Every one of those would be a
# silent difference between the model the admin tab produces and the model train.py produces.
from train import LABELS as BASE_LABELS, bucket_batches, collate, encode, metrics, predict_logits  # noqa: E402
from train_grievance import split_of  # noqa: E402

# The probe is the human-readable version of the gate. The first five are the sentences the
# category was requested for; the rest are capability that already works and must survive -
# measured on grievance-v3 at 0.024, 0.021, 0.021 and 0.022 before any of this ran. A profanity
# detector would move all four, and three of the four are somebody's listing or article.
PROBE = [
    (1, "The reward for doing a good job here is getting assigned everyone else's unfinished shit."),
    (1, "Constantly asked to do the work of three people for the pay of half a person."),
    (1, "Every single task is an 'urgent fire,' simply because management has no brain cells for planning."),
    (1, "They preach work-life balance on LinkedIn while demanding late nights and weekend shifts."),
    (1, "This job is draining the life out of everyone, and management couldn't give a damn as long as numbers look green."),
    (0, "Damn good machine. Paid for itself in eight months."),
    (0, "Toxic waste disposal, 12 licensed tankers, pan-India coverage."),
    (0, "Dumpster bins, 1100 litre HDPE, wheeled and powder coated."),
    (0, "Five signs your shop floor is burning people out, and what we changed."),
    (0, "We pay overtime at double rate and the shift roster is published a month ahead."),
    (1, "Nobody at their support desk has replied about the refund in two weeks."),
    (0, "Payment terms remain 30 days net."),
]


def load_new(path: Path, val: float, test: float) -> pd.DataFrame:
    """The uploaded CSV, split group-aware on `template`.

    Group-aware because an admin's upload will be generated or templated like everything else
    in res/, and a random split over templated rows reports a number that has nothing to do
    with held-out performance: v6 said 99.66% accuracy while the hand-written sets said 76%.
    A `template` column is used when present; without one each row is its own group, which is
    correct for genuinely hand-written uploads and wrong for templated ones - so its absence
    is reported rather than assumed to be fine.
    """
    d = pd.read_csv(path, keep_default_na=False)
    missing = {"text", "label"} - set(d.columns)
    if missing:
        sys.exit(f"{path}: missing required column(s) {sorted(missing)}")
    d["text"] = d["text"].astype(str).str.strip()
    d = d[d["text"].str.len() > 0]
    d["label"] = d["label"].astype(int)
    if "template" in d.columns:
        groups = d["template"].astype(str)
    else:
        print(f"  NOTE: {path.name} has no `template` column, so the split is per row. If these "
              f"rows came off templates, the held-out numbers below are optimistic.")
        groups = d["text"]
    before = len(d)
    d = d.drop_duplicates(subset="text")
    if len(d) < before:
        print(f"  dropped {before - len(d):,} duplicate texts from the upload")
    d["split"] = groups.map(lambda g: split_of(g, val, test))
    if "category" not in d.columns:
        d["category"] = path.stem
    return d


def check_leakage(new: pd.DataFrame, eval_dir: Path, fail: bool) -> int:
    """Warn when the upload contains text from a hand-written evaluation set.

    This is not hypothetical and it is not only an admin's mistake to make. Writing round 2 of
    generate_workplace_grievances.py, four sentences were lifted verbatim out of
    eval_grievance_workplace.csv into the generator's frames - precisely the sentences round 1
    had failed, which is what made them tempting. The eval set then reported those four
    correct and measured nothing at all.

    An admin uploading a CSV has the same hazard with none of the context: the obvious place to
    get example sentences for a new category is the moderation queue, and the eval sets were
    built from the moderation queue. So the check runs on every upload. It compares on
    alphanumerics only, because punctuation and case are exactly what a near-duplicate varies.
    """
    rows, hits = [], 0
    for path in sorted(eval_dir.glob("eval_grievance*.csv")):
        try:
            with path.open(encoding="utf-8") as fh:
                rows += [(path.name, r["text"]) for r in csv.DictReader(fh) if r.get("text")]
        except Exception as exc:                                        # pragma: no cover
            print(f"  (could not read {path.name}: {exc})", file=sys.stderr)
    if not rows:
        return 0
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", str(t).lower()).strip()  # noqa: E731
    uploaded = set(new["text"].map(norm))
    for name, text in rows:
        if norm(text) in uploaded:
            hits += 1
            if hits <= 10:
                print(f"  LEAK  {name}: {text[:78]}", file=sys.stderr)
    if hits:
        print(f"\n  {hits} evaluation sentence(s) appear verbatim in the upload. Those rows now "
              f"measure memorisation, not generalisation - the eval set is that much blinder "
              f"for every future run, not just this one."
              + ("" if fail else " Continuing; pass --fail-on-leakage to stop."),
              file=sys.stderr)
    else:
        print(f"  no evaluation text in the upload ({len(rows)} eval sentences checked)")
    return hits


def load_replay(data_dir: Path, frac: float, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A stratified sample of the old training data, and the old validation set in full.

    The sample is the rehearsal that stops the new category overwriting the old ones. The
    validation set is NOT sampled: it is the measurement the whole run is gated on, and
    sampling a gate makes the gate noisy.
    """
    tr = pd.read_csv(data_dir / "train.csv", keep_default_na=False)
    va = pd.read_csv(data_dir / "val.csv", keep_default_na=False)
    for d in (tr, va):
        d["text"] = d["text"].astype(str).str.strip()
        d["label"] = d["label"].astype(int)
    if frac <= 0:
        return tr.iloc[0:0], va
    # Stratified by label, because the old corpus is not balanced and an unstratified sample
    # would quietly change the class ratio the model is rehearsing against. Done as an explicit
    # per-label concat rather than groupby.apply, which drops the grouping column on pandas 2.2.
    parts = [part.sample(frac=min(frac, 1.0), random_state=seed)
             for _, part in tr.groupby("label", sort=True)]
    return pd.concat(parts, ignore_index=True), va


def expand_head(model, old_labels: list[str], new_labels: list[str]):
    """Grow the classification head, keeping what the old classes mean.

    The old rows of the weight matrix and the old biases are copied across; the new rows get
    small random values. Copying rather than reinitialising is the whole point - a fresh head
    throws away the decision boundary the checkpoint exists for, and the run would then need
    the full corpus to find it again, which is the cost this script is here to avoid.
    """
    labels = old_labels + [l for l in new_labels if l not in old_labels]
    if labels == old_labels:
        return model, labels
    old = model.classifier
    new = torch.nn.Linear(old.in_features, len(labels))
    with torch.no_grad():
        new.weight.normal_(0.0, 0.02)
        new.bias.zero_()
        new.weight[:old.out_features] = old.weight
        new.bias[:old.out_features] = old.bias
    model.classifier = new
    model.config.num_labels = len(labels)
    model.config.id2label = dict(enumerate(labels))
    model.config.label2id = {l: i for i, l in enumerate(labels)}
    print(f"  head expanded {old.out_features} -> {len(labels)} labels: {labels}")
    return model, labels


def freeze(model, n_layers: int) -> int:
    """Freeze the embeddings and the bottom n encoder layers. Returns trainable parameters.

    Cheap wall-clock win with a second effect that matters more here: frozen layers cannot
    drift, so the lexical and syntactic features the old categories depend on are structurally
    safe rather than merely rehearsed. The top layers are where the task-specific decision
    lives, which is what a new category needs to move.
    """
    if n_layers <= 0:
        return sum(p.numel() for p in model.parameters() if p.requires_grad)
    base = model.base_model
    for p in base.embeddings.parameters():
        p.requires_grad = False
    layers = base.encoder.layer
    for layer in layers[:n_layers]:
        for p in layer.parameters():
            p.requires_grad = False
    live = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  froze embeddings + bottom {n_layers} of {len(layers)} encoder layers: "
          f"{live / 1e6:.1f}M of {total / 1e6:.1f}M parameters trainable")
    return live


@torch.no_grad()
def probe(model, tok, device, max_len: int, threshold: float) -> list[dict]:
    model.eval()
    texts = [t for _, t in PROBE]
    ids = encode(tok, texts, max_len)
    logits = predict_logits(model, ids, tok.pad_token_id, device)
    z = logits - logits.max(1, keepdims=True)
    p = (np.exp(z) / np.exp(z).sum(1, keepdims=True))[:, 1]
    out = []
    for (want, text), score in zip(PROBE, p):
        ok = (score >= threshold) == (want == 1)
        out.append({"label": want, "score": float(score), "ok": bool(ok), "text": text})
    return out


def print_probe(rows: list[dict], threshold: float, before: list[dict] | None = None) -> None:
    print(f"\n  probe at threshold {threshold}:")
    for i, r in enumerate(rows):
        mark = "ok  " if r["ok"] else ("MISS" if r["label"] == 1 else "REFU")
        delta = f"  ({before[i]['score']:.3f} -> )" if before else ""
        shift = f"  was {before[i]['score']:.4f}" if before else ""
        print(f"    {mark} want={r['label']} {r['score']:.4f}{shift}  {r['text'][:66]}")
    hit = sum(r["ok"] for r in rows)
    print(f"    {hit}/{len(rows)} correct")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", required=True,
                    help="the fine-tuned checkpoint to continue from (a directory train.py "
                         "or this script wrote). This is the difference from train.py.")
    ap.add_argument("--new", type=Path, required=True,
                    help="the uploaded CSV: text,label[,category,template]")
    ap.add_argument("--replay", type=Path, default=None,
                    help="the data directory the --init checkpoint was trained on "
                         "(train.csv/val.csv). Supplies the rehearsal sample AND the old-"
                         "capability gate. Omitting it means nothing measures forgetting.")
    ap.add_argument("--replay-frac", type=float, default=0.15,
                    help="fraction of the old training data to rehearse on. 0 trains on the "
                         "upload alone, which will forget - see the docstring.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=1.0,
                    help="1.0 by default, against train.py's 2.0. The checkpoint already "
                         "knows the task; a second pass mostly buys overfitting to the upload.")
    ap.add_argument("--lr", type=float, default=1e-5,
                    help="1e-5 against train.py's 3e-5. A warm start needs a smaller step: "
                         "the big steps that find the boundary from scratch are the ones that "
                         "destroy it when it already exists.")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=192)
    ap.add_argument("--freeze-layers", type=int, default=0,
                    help="freeze embeddings + this many bottom encoder layers (speed, and "
                         "drift the old categories cannot suffer)")
    ap.add_argument("--add-labels", nargs="+", default=[],
                    help="grow the head with these NEW OUTPUT CLASSES. Changes the serving "
                         "contract - read the docstring before using it. Not what 'add a "
                         "category' usually means.")
    ap.add_argument("--label-smoothing", type=float, default=0.0,
                    help="match whatever the --init checkpoint was trained with. Changing it "
                         "mid-life changes what a score MEANS and invalidates the threshold "
                         "in settings.yaml - grievance-v3's 0.978 is where it is because of "
                         "smoothing, so this is not a free dial.")
    ap.add_argument("--fail-on-leakage", action="store_true",
                    help="refuse the upload if it contains text from a hand-written eval set "
                         "(training/eval_grievance*.csv). Warns either way.")
    ap.add_argument("--max-regression", type=float, default=0.01,
                    help="fail if old-val AUC drops more than this below the measured "
                         "baseline. The admin tab should gate on this exit code.")
    ap.add_argument("--evals", type=int, default=4, help="evaluations over the whole run")
    ap.add_argument("--threshold", type=float, default=0.978,
                    help="only for printing the probe; nothing is trained against it")
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--val", type=float, default=0.1, help="new-data validation fraction")
    ap.add_argument("--test", type=float, default=0.1, help="new-data test fraction")
    ap.add_argument("--limit", type=int, default=0, help="debug: cap the training rows")
    args = ap.parse_args()

    t_start = time.time()
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device} "
          f"{torch.cuda.get_device_name(0) if device.type == 'cuda' else ''}", flush=True)

    print(f"\ninit: {args.init}")
    tok = AutoTokenizer.from_pretrained(args.init)
    model = AutoModelForSequenceClassification.from_pretrained(args.init)
    old_labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
    print(f"  {model.config.num_labels} labels: {old_labels}")
    model, labels = expand_head(model, old_labels, list(args.add_labels))
    model.to(device)

    print(f"\nnew data: {args.new}")
    new = load_new(args.new, args.val, args.test)
    print(f"  {len(new):,} rows, {new['label'].mean():.1%} positive, "
          f"{new['template'].nunique() if 'template' in new else len(new):,} groups")
    for s in ("train", "val", "test"):
        part = new[new.split == s]
        print(f"    {s:5} {len(part):>7,} rows  {part['label'].mean():5.1%} positive")
    if "category" in new:
        print("  categories: " + ", ".join(f"{c}={n:,}" for c, n
                                           in new["category"].value_counts().items()))

    leaks = check_leakage(new, HERE, args.fail_on_leakage)
    if leaks and args.fail_on_leakage:
        print("FAILED: --fail-on-leakage is set.", file=sys.stderr)
        return 3

    replay_tr, old_val = pd.DataFrame(), None
    if args.replay:
        replay_tr, old_val = load_replay(args.replay, args.replay_frac, args.seed)
        print(f"\nreplay: {args.replay}")
        if len(replay_tr):
            print(f"  {len(replay_tr):,} rehearsal rows sampled at {args.replay_frac:.0%} "
                  f"({replay_tr['label'].mean():.1%} positive)")
        else:
            print("  no rehearsal rows (--replay-frac 0): forgetting is unconstrained")
        print(f"  {len(old_val):,} old validation rows, held in full as the gate")
    else:
        print("\nreplay: NONE. Nothing in this run measures whether the old categories "
              "survived, and nothing stops them being overwritten.", file=sys.stderr)

    # THE BASELINE, measured now, with the incoming weights. Read from the model rather than
    # from a log file, because the log belongs to another run on another machine and the thing
    # being gated is this checkpoint's own behaviour.
    base_old = None
    tok_pad = tok.pad_token_id
    if old_val is not None:
        ids = encode(tok, old_val["text"], args.max_len)
        y = old_val["label"].to_numpy()
        base_old = metrics(predict_logits(model, ids, tok_pad, device), y)
        print(f"\nBASELINE on the old validation set, before any step:\n  {base_old}")
        old_val_ids, old_val_y = ids, y
    probe_before = probe(model, tok, device, args.max_len, args.threshold)
    print_probe(probe_before, args.threshold)

    # Training set: the upload's train split plus the rehearsal sample, shuffled together. One
    # pool, not two phases - training on the new rows and then on the old ones would just move
    # the forgetting to the end of the run.
    tr_new = new[new.split == "train"]
    pool = pd.concat([tr_new[["text", "label"]], replay_tr[["text", "label"]]],
                     ignore_index=True)
    if args.limit:
        pool = pool.sample(min(args.limit, len(pool)), random_state=args.seed)
    pool = pool.sample(frac=1.0, random_state=args.seed).reset_index(drop=True)
    new_val = new[new.split == "val"]
    print(f"\ntraining pool: {len(pool):,} rows "
          f"({len(tr_new):,} new + {len(replay_tr):,} rehearsal)")

    t0 = time.time()
    tr_ids = encode(tok, pool["text"], args.max_len)
    nv_ids = encode(tok, new_val["text"], args.max_len)
    tr_y, nv_y = pool["label"].to_numpy(), new_val["label"].to_numpy()
    print(f"tokenised in {time.time() - t0:.0f}s; mean length "
          f"{np.mean([len(x) for x in tr_ids]):.0f} tokens", flush=True)

    trainable = freeze(model, args.freeze_layers)
    lengths = [len(x) for x in tr_ids]
    steps_per_epoch = math.ceil(len(tr_ids) / args.batch_size)
    total = max(1, int(steps_per_epoch * args.epochs))
    no_decay = ("bias", "LayerNorm.weight", "layernorm", "norm")
    live = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    params = [{"params": [p for n, p in live if not any(k in n for k in no_decay)],
               "weight_decay": 0.01},
              {"params": [p for n, p in live if any(k in n for k in no_decay)],
               "weight_decay": 0.0}]
    opt = torch.optim.AdamW(params, lr=args.lr)
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * total), total)
    eval_every = max(1, total // max(1, args.evals))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best, step, t0 = float("inf"), 0, time.time()
    running: list[float] = []
    history: list[dict] = []
    saved_at = None

    while step < total:
        for b in bucket_batches(lengths, args.batch_size, rng):
            if step >= total:
                break
            model.train()
            x, m = collate([tr_ids[j] for j in b], tok_pad, device)
            y = torch.tensor(tr_y[b], device=device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                logits = model(input_ids=x, attention_mask=m).logits
            loss = torch.nn.functional.cross_entropy(logits.float(), y,
                                                     label_smoothing=args.label_smoothing)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for _, p in live], 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            running.append(loss.item())
            step += 1
            if step % 200 == 0:
                rate = step / (time.time() - t0)
                print(f"step {step}/{total} loss {np.mean(running[-200:]):.4f} "
                      f"lr {sched.get_last_lr()[0]:.2e} {rate:.1f} it/s, "
                      f"eta {(total - step) / rate / 60:.0f} min", flush=True)
            if step % eval_every == 0 or step == total:
                mt_new = metrics(predict_logits(model, nv_ids, tok_pad, device), nv_y)
                mt_old = (metrics(predict_logits(model, old_val_ids, tok_pad, device), old_val_y)
                          if old_val is not None else None)
                print(f"== eval step {step}", flush=True)
                print(f"   new category : {mt_new}", flush=True)
                if mt_old:
                    drop = base_old["auc"] - mt_old["auc"]
                    print(f"   old capability: {mt_old}", flush=True)
                    print(f"   forgetting    : AUC {base_old['auc']:.4f} -> {mt_old['auc']:.4f} "
                          f"({'-' if drop > 0 else '+'}{abs(drop):.4f})", flush=True)
                history.append({"step": step, "new": mt_new, "old": mt_old})
                # Selected on the SUM. A checkpoint that wins on the new category by forgetting
                # the old one loses here, which is the entire reason the sum is the criterion.
                score = mt_new["loss"] + (mt_old["loss"] if mt_old else 0.0)
                if score < best:
                    best, saved_at = score, step
                    model.save_pretrained(out)
                    tok.save_pretrained(out)
                    print(f"   saved best (new+old loss {best:.4f})", flush=True)

    mins = (time.time() - t_start) / 60
    print(f"\ndone in {mins:.1f} min; best checkpoint from step {saved_at} "
          f"(new+old loss {best:.4f})")

    # Reload the SAVED checkpoint before the verdict. The weights in memory are from the last
    # step, which is not necessarily the one that was kept, and reporting those would describe
    # a model nobody is going to ship.
    model = AutoModelForSequenceClassification.from_pretrained(out).to(device)
    final_new = metrics(predict_logits(model, nv_ids, tok_pad, device), nv_y)
    te = new[new.split == "test"]
    final_test = None
    if len(te):
        te_ids = encode(tok, te["text"], args.max_len)
        final_test = metrics(predict_logits(model, te_ids, tok_pad, device),
                             te["label"].to_numpy())
    final_old = (metrics(predict_logits(model, old_val_ids, tok_pad, device), old_val_y)
                 if old_val is not None else None)

    print("\n---- saved checkpoint ----")
    print(f"  new category  val : {final_new}")
    if final_test:
        print(f"  new category  test: {final_test}")
    if final_old:
        print(f"  old capability    : {final_old}")
    probe_after = probe(model, tok, device, args.max_len, args.threshold)
    print_probe(probe_after, args.threshold, probe_before)

    report = {
        "init": str(args.init),
        "new_data": str(args.new),
        "replay": {"dir": str(args.replay) if args.replay else None,
                   "frac": args.replay_frac, "rows": int(len(replay_tr))},
        "labels": labels,
        "rows_trained_on": int(len(pool)),
        "steps": total,
        "minutes": round(mins, 2),
        "hyperparameters": {"lr": args.lr, "epochs": args.epochs,
                            "batch_size": args.batch_size, "max_len": args.max_len,
                            "freeze_layers": args.freeze_layers,
                            "label_smoothing": args.label_smoothing},
        "baseline_old": base_old,
        "final": {"new_val": final_new, "new_test": final_test, "old_val": final_old},
        "history": history,
        "probe": {"before": probe_before, "after": probe_after},
    }
    (out / "incremental_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out} and {out / 'incremental_report.json'}")

    # THE GATE. Non-zero exit means the upload degraded the deployed capability past the floor,
    # and the admin tab should surface that instead of promoting the checkpoint.
    if final_old and base_old:
        drop = base_old["auc"] - final_old["auc"]
        if drop > args.max_regression:
            print(f"\nFAILED: old-capability AUC fell {drop:.4f} "
                  f"(baseline {base_old['auc']:.4f} -> {final_old['auc']:.4f}), over the "
                  f"{args.max_regression} floor. The upload has damaged what the model already "
                  f"did. Raise --replay-frac, lower --lr, or freeze more layers.",
                  file=sys.stderr)
            return 2
        print(f"\nPASSED: old-capability AUC {base_old['auc']:.4f} -> {final_old['auc']:.4f} "
              f"(drop {drop:+.4f}, floor {args.max_regression}).")
    print("\nNext: training/calibrate_export.py --ckpt <out> to build a servable bundle, then "
          "training/accept_model.py before anything points at it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
