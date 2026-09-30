"""Build the bonc-v4 train / val / test sets from res/ plus the synthetic data.

    python prepare_data.py            # writes D:/AI/bonc-data/{train,val,test}.csv

Sources (res/ is the data the v3 model was trained on):

  res/Dataset123.xlsx                         text, label  0 / 1 / 2
  res/bonc_optimized_final_dataset.csv        text, label  0 / 1 / 2 (balanced subset + leetspeak copies)
  res/bonc_network_moderation_dataset.csv.xlsx  text, review  SAFE / REVIEW / REJECT
  synthetic_v4.csv                            from synth_data.py

v4 is a binary model: 0 = safe (publish), 1 = unsafe (reject, the author rewrites).

Relabelling, and why (MODEL_IMPROVEMENT_GUIDE.md section 2.1):

  * label 0 is product listings only.                      -> safe
  * label 1 ("review") is ordinary non-listing prose (Wikipedia talk pages, tweets)
    mixed with profane/offensive tweets. Training on it as its own class taught v3
    "anything that isn't a listing is review". Split it with the profanity word lists:
      contains profanity or a slur                          -> unsafe
      otherwise                                             -> safe
  * label 2 (toxic, hate, spam)                            -> unsafe
  * BONC SAFE and REVIEW (ordinary complaints, "their support was slow")  -> safe
    BONC REJECT                                            -> unsafe
  * Sentences and short word windows cut from safe texts are added as safe, because the
    API also scores single sentences, titles and word windows on their own.

Splits: a group-aware split so a text and its sentences/windows never land on both
sides. The test split is never used for training or calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import random
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

from app.gate import Gate  # noqa: E402
RES = ROOT / "res"
WORDLISTS = ROOT / "flask_api" / "config" / "private" / "wordlists"
EXCLUSIONS = ROOT / "flask_api" / "config" / "wordlist_exclusions.txt"

LEET = str.maketrans({"$": "s", "@": "a", "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "!": "i", "*": ""})
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"\S+")


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip().lower() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip() and not ln.startswith("#")]


def profanity_matcher():
    terms = set()
    for f in WORDLISTS.glob("*.txt"):
        terms.update(_lines(f))
    terms -= set(_lines(EXCLUSIONS))
    terms |= {"hoes", "thot", "thots"}   # "hoe" is excluded for the gate (garden tool); the plural is slang in the tweets
    # Too common in ordinary prose to mean "offensive" on their own.
    terms -= {"god", "hell", "damn", "sex", "sexy", "kill", "gay", "drunk", "drug", "drugs", "crap", "ugly",
              "nude", "naked", "stupid", "hate", "suck", "sucks", "bloody", "breast", "breasts", "wtf", "lmao",
              "lmfao", "omg", "pot", "weed", "fat", "dumb", "screw", "blow", "bang", "hump", "xxx", "porn",
              # identity words are not profanity: counting them taught round 1 that "Homosexuality"
              # as a heading is unsafe (0.99). Slurs for the same groups stay in the list.
              "homosexual", "homosexuals", "homosexuality", "lesbian", "lesbians", "gays", "bisexual",
              "transgender", "transsexual", "jew", "jews", "jewish", "muslim", "muslims", "islam", "christian",
              "hindu", "sikh", "dalit", "black", "white", "asian", "arab", "mexican", "african", "indian"}
    terms = sorted((t for t in terms if len(t) >= 3), key=len, reverse=True)
    pat = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![a-z])")
    return lambda text: bool(pat.search(unicodedata.normalize("NFKC", str(text)).lower().translate(LEET)))


def norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_res() -> pd.DataFrame:
    frames = []
    c = pd.read_excel(RES / "bonc_network_moderation_dataset.csv.xlsx")
    c = c.rename(columns={"review": "orig"})
    c["label"] = c["orig"].map({"SAFE": 0, "REVIEW": 1, "REJECT": 2})
    frames.append(c[["text", "label"]].assign(source="bonc_network"))
    a = pd.read_excel(RES / "Dataset123.xlsx")
    frames.append(a.assign(source="dataset123"))
    b = pd.read_csv(RES / "bonc_optimized_final_dataset.csv")
    frames.append(b.assign(source="optimized"))
    df = pd.concat(frames, ignore_index=True).dropna(subset=["text", "label"])
    df["text"] = df["text"].astype(str).str.strip()
    df = df[df["text"].str.len() >= 2]
    df["key"] = df["text"].map(norm_key)
    # Same text in several files: keep one row. The BONC file wins (its labels are on-domain),
    # then Dataset123, then the optimized subset (mostly copies of Dataset123 rows).
    df = df.drop_duplicates("key", keep="first")
    return df


def relabel(df: pd.DataFrame) -> pd.DataFrame:
    is_prof = profanity_matcher()
    prof = df["text"].map(is_prof)
    out = df.copy()
    out["orig_label"] = out["label"]
    out["category"] = "real"
    out.loc[df["label"] == 0, "category"] = "listing"
    out.loc[df["label"] == 2, "category"] = "toxic"
    out.loc[(df["label"] == 1) & prof, "category"] = "offensive"
    out.loc[(df["label"] == 1) & ~prof, "category"] = "prose"
    bonc = df["source"] == "bonc_network"
    out.loc[bonc, "category"] = df.loc[bonc, "label"].map({0: "bonc_safe", 1: "bonc_complaint", 2: "bonc_reject"})
    unsafe = out["category"].isin(["toxic", "offensive", "bonc_reject"])
    out["label"] = unsafe.astype(int)
    out["kind"] = "whole"
    return out


def windows_and_sentences(texts, rng: random.Random, n_sent: int, n_win: int):
    """Sentences and 6-24 word windows cut from safe texts (labelled safe)."""
    sents, wins = [], []
    for gid, t in texts:
        parts = [s.strip() for s in SENT_SPLIT.split(t) if len(WORD.findall(s)) >= 2]
        for s in parts[:3]:
            sents.append((gid, s))
        words = t.split()
        if len(words) > 14:
            size = rng.randint(6, min(24, len(words)))
            start = rng.randrange(0, len(words) - size + 1)
            wins.append((gid, " ".join(words[start:start + size])))
    rng.shuffle(sents)
    rng.shuffle(wins)
    return sents[:n_sent], wins[:n_win]


def split_of(gid: str, val: float, test: float) -> str:
    h = int(hashlib.md5(gid.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if h < test else "val" if h < test + val else "train"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", default="D:/AI/bonc-data/synthetic_v4.csv")
    ap.add_argument("--out", default="D:/AI/bonc-data")
    ap.add_argument("--max-prose", type=int, default=50_000, help="cap on real safe prose rows (label 1 without profanity)")
    ap.add_argument("--max-offensive", type=int, default=35_000)
    ap.add_argument("--sentences", type=int, default=20_000)
    ap.add_argument("--windows", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    real = relabel(load_res())
    print("real data after relabelling:")
    print(real.groupby(["orig_label", "category", "label"]).size().to_string())

    # Cap the two huge buckets, keep everything else.
    parts = []
    for cat, cap in (("prose", args.max_prose), ("offensive", args.max_offensive)):
        g = real[real["category"] == cat]
        parts.append(g.sample(min(cap, len(g)), random_state=args.seed))
    parts.append(real[~real["category"].isin(["prose", "offensive"])])
    real = pd.concat(parts, ignore_index=True)
    # The hand-written BONC rows are few and on-domain: show them twice.
    real = pd.concat([real, real[real["source"] == "bonc_network"]], ignore_index=True)
    real["group"] = "r:" + real["key"]

    syn = pd.read_csv(args.synthetic)
    syn["source"] = "synthetic"
    syn["key"] = syn["text"].map(norm_key)
    syn["group"] = "s:" + syn["key"]
    syn = syn[~syn["key"].isin(set(real["key"]))]

    safe_real = real[real["label"] == 0]
    sents, wins = windows_and_sentences(list(zip(safe_real["group"], safe_real["text"])), rng,
                                        args.sentences, args.windows)
    frag = pd.DataFrame([(g, t, "sentence") for g, t in sents] + [(g, t, "window") for g, t in wins],
                        columns=["group", "text", "kind"])
    frag = frag.assign(label=0, category="real_fragment", source="fragment", key=frag["text"].map(norm_key))

    cols = ["text", "label", "category", "kind", "source", "group", "key"]
    df = pd.concat([real[cols], syn[cols], frag[cols]], ignore_index=True)
    # A safe row the Layer 1 gate holds back is a contradiction: at runtime the gate decides
    # before the model is ever called, so training on it as "safe" teaches the model to
    # disagree with the rules it sits behind. Both actions count — a blocked row is rejected
    # and a "revise" row isn't published either. Caught real cases both ways: dismissive
    # adjectives that are also gate terms ("Their invoice is worthless..."), and harsh words
    # about a product that are only gate terms when a pronoun shares the sentence.
    gate = Gate.from_yaml(ROOT / "flask_api" / "config" / "gate_patterns.yaml")
    safe = df["label"] == 0

    def held_back(text: str) -> bool:
        result = gate.check(text)
        return result.blocked or result.needs_revision

    blocked = safe & df["text"].map(held_back)
    if blocked.any():
        print(f"dropped {int(blocked.sum())} safe rows the gate holds back "
              f"({df[blocked]['category'].value_counts().head(5).to_dict()})")
        df = df[~blocked]

    # Never train on an evaluation text (hand-written held-out sets), even by template collision.
    held_out = set()
    for f in HERE.glob("eval_*.csv"):
        held_out |= set(pd.read_csv(f, keep_default_na=False)["text"].map(norm_key))
    before = len(df)
    df = df[~df["key"].isin(held_out)]
    print(f"dropped {before - len(df)} rows that match a held-out evaluation text")
    # A text that appears with both labels is ambiguous: drop it.
    both = df.groupby("key")["label"].nunique()
    df = df[~df["key"].isin(set(both[both > 1].index))]
    df = df.drop_duplicates(["key", "source"])
    df["split"] = df["group"].map(lambda g: split_of(g, .06, .06))
    # Small hand-built pools (hard negatives, Hinglish, titles) are few hundred unique rows:
    # repeat them in training so they aren't drowned out.
    boost = (df["split"] == "train") & df["category"].isin(
        ["hard_negative", "hinglish", "title", "everyday_feelings", "everyday_general", "fragment",
         "professional_criticism"])
    df = pd.concat([df] + [df[boost]] * 4, ignore_index=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, g in df.groupby("split"):
        g = g.sample(frac=1, random_state=args.seed)
        g[["text", "label", "category", "kind", "source"]].to_csv(out / f"{name}.csv", index=False)
        print(f"\n{name}: {len(g)} rows, unsafe {g['label'].mean():.1%}")
        print(g.groupby(["source", "label"]).size().to_string())


if __name__ == "__main__":
    main()
