"""Download the profanity word lists used by the gate into config/private/wordlists/.

    python scripts/fetch_wordlists.py

The lists are not committed to git: they're large, offensive, and third-party.
Each is pinned to a commit and checked against a SHA-256 hash, so the gate's
behaviour can't change silently if a list is updated upstream.

Sources and licenses:
  ldnoobw_en.txt
      "List of Dirty, Naughty, Obscene, and Otherwise Bad Words" (English)
      https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words
      License: CC BY 4.0 (attribution: Shutterstock / LDNOOBW contributors)
  google_profanity_en.txt
      "google-profanity-words" (English)
      https://github.com/coffee-and-fun/google-profanity-words
      License: MIT

Entries with innocent B2B meanings (e.g. "flange", "hoe") are removed at load
time by config/wordlist_exclusions.txt, not by editing the downloaded files.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "config" / "private" / "wordlists"

LISTS = {
    "ldnoobw_en.txt": (
        "https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/"
        "4638b970cb8d9d82789564fcba1f4a1eb508ff1a/en",
        "af851ecef1d5f212caba17339b12ac39cc2fef7d78c74876f67237644fcee8bd",
    ),
    "google_profanity_en.txt": (
        "https://raw.githubusercontent.com/coffee-and-fun/google-profanity-words/"
        "5b2c59a78e777972f5bfafdf0e52919d1b72c99d/data/en.txt",
        "ea16a22f12b4fb9d32747b8fc7af21b90ce59df7313f6275ce1ef17d746b0dfe",
    ),
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in LISTS.items():
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            print(f"error: {name} checksum mismatch (expected {expected[:12]}…, got {actual[:12]}…)", file=sys.stderr)
            return 1
        (DEST / name).write_bytes(data)
        print(f"{name}: {len(data.splitlines())} entries, checksum OK")
    print(f"word lists ready in {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
