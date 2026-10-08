"""Where should the bar sit for text read out of a picture?

    python scripts/eval_ocr_text_bar.py --images ../images

Words a member types are prose. Words read off a photograph are not: OCR of a shopfront comes
back as "TURNKEY MWRULTANTS A SOLUTIONS", and the moderation model was trained on sentences, so
it scores that noise unreliably - four ordinary BONC listings landed in the reject band. Holding
image text to a higher bar fixes that, but only if scam text in a picture still clears the
higher bar. This measures both sides on real pictures and prints the sweep.

The scam side is rendered here rather than collected, because a scam flyer is a scam flyer
whoever typed it: the text is what matters and OCR mangles it the same way it mangles a
shopfront. Each phrase is drawn onto a photo-like background, read back through the real OCR
stack, and scored by the live model - so what is measured is the whole path, mangling included,
not the clean string.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Scam text as it actually arrives inside an uploaded picture.
SCAM_LINES = [
    ["Earn 50000 per week from home.", "Pay a small registration fee to join."],
    ["Double your money in 7 days.", "Guaranteed returns, no risk."],
    ["Work from home job.", "Send 2000 rupees registration and start today."],
    ["INVESTMENT PLAN", "Deposit 10000 get 50000 back in 30 days"],
    ["Pay in crypto only", "Bitcoin accepted, instant doubling"],
    ["URGENT HIRING", "No experience needed. Pay 500 for the form."],
    ["Lottery winner!", "Claim your prize, pay the processing fee now."],
    ["Get rich quick scheme", "Join now and earn lakhs every month"],
    ["FIRST COPY WATCHES", "Replica Rolex at wholesale rates"],
    ["Loan without documents", "Transfer 5000 processing fee to receive 2 lakh"],
]
# Threats and abuse, which are a different harm class from a scam and have to clear the bar on
# their own account - a flyer and a threat do not score alike just because both are refused.
THREAT_LINES = [
    ["We will burn your shop down", "we know where you live"],
    ["Pay up or we break your legs", "this is your last warning"],
    ["I will find you and kill you", "you and your family"],
    ["Your warehouse burns tonight", "unless you pay"],
    ["We are coming for you", "watch your back every day"],
    ["TUM SAB BEWAKOOF HO", "mar jao sab ke sab"],
    ["Stop trading or face consequences", "we have your address"],
    ["You will regret this", "we will destroy your business"],
]
# Ordinary B2B signage, as a second honest-side set alongside the real BONC pictures.
SIGN_LINES = [
    ["COTTON SARIS WHOLESALE", "Surat, Gujarat since 1994"],
    ["RADIATORS & ACCESSORIES", "Heat your home in style"],
    ["TURNKEY CONSULTANTS", "Industrial solutions"],
    ["EXHAUST SYSTEMS", "Air intakes and components"],
    ["SHREE STEEL TRADERS", "MS pipes, angles, channels"],
    ["GREEN VALLEY FOODS", "Spices and dry fruits, bulk orders"],
    ["PRECISION TOOLS LTD", "CNC machining and fabrication"],
    ["APEX PACKAGING", "Corrugated boxes, all sizes"],
    ["SUNRISE TEXTILES", "Cotton and blended fabric"],
    ["METRO AUTO PARTS", "Genuine spares and accessories"],
]


def render(lines) -> bytes:
    """The phrase on a photo-like background, so OCR has to work for it."""
    import io
    import random

    from PIL import Image, ImageDraw, ImageFont

    rng = random.Random(len(lines[0]))
    img = Image.new("RGB", (1100, 300), (235, 232, 226))
    draw = ImageDraw.Draw(img)
    for _ in range(600):                       # texture, so this is not clean synthetic text
        x, y = rng.randrange(1100), rng.randrange(300)
        draw.point((x, y), fill=tuple(rng.randrange(200, 250) for _ in range(3)))
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 80), line, fill=(25, 25, 30), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, default=ROOT.parent / "images")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import numpy as np

    from app.config import PROJECT_ROOT, load_settings
    from app.media import _decode
    from app.model import OnnxScorer

    s = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    model = OnnxScorer(s.model_dir, intra_op_threads=s.intra_op_threads,
                       inter_op_threads=s.inter_op_threads, label_weights=s.label_weights)

    def read_and_score(data: bytes):
        frame = _decode(data, s.media)
        page = s.ocr.read_image(np.asarray(frame))
        text = page.text.strip()
        if not text:
            return None, ""
        return model.score(text).risk_score, text

    groups: dict[str, list[tuple[float, str]]] = {}

    for name, lines in (("scam (rendered)", SCAM_LINES),
                        ("threat (rendered)", THREAT_LINES),
                        ("signage (rendered)", SIGN_LINES)):
        rows = []
        for phrase in lines:
            risk, text = read_and_score(render(phrase))
            if risk is not None:
                rows.append((risk, text))
        groups[name] = rows

    listings = args.images / "BONC_Positive"
    if listings.is_dir():
        rows = []
        for p in sorted(listings.iterdir()):
            try:
                risk, text = read_and_score(p.read_bytes())
            except Exception:
                continue
            if risk is not None:
                rows.append((risk, text))
        groups["BONC listings (real)"] = rows

    print(f"\n{'group':24} {'n':>4} {'min':>7} {'p50':>7} {'max':>7}")
    for name, rows in groups.items():
        if not rows:
            continue
        v = sorted(r for r, _ in rows)
        print(f"{name:24} {len(v):4} {v[0]:7.4f} {v[len(v)//2]:7.4f} {v[-1]:7.4f}")

    scam = [r for r, _ in groups.get("scam (rendered)", [])] +            [r for r, _ in groups.get("threat (rendered)", [])]
    honest = [r for r, _ in groups.get("signage (rendered)", [])] + \
             [r for r, _ in groups.get("BONC listings (real)", [])]

    print(f"\n{'bar':>8} {'scam caught':>14} {'honest refused':>16}")
    best = None
    for bar in (0.50, 0.80, 0.90, 0.95, 0.97, 0.99, 0.995, 0.999):
        caught = sum(1 for r in scam if r >= bar)
        refused = sum(1 for r in honest if r >= bar)
        print(f"{bar:8.3f} {caught:6}/{len(scam):<6} {refused:7}/{len(honest):<7}"
              f"  {caught/max(len(scam),1):5.0%} caught, {refused/max(len(honest),1):5.1%} refused")
        if refused == 0 and (best is None or caught > best[1]):
            best = (bar, caught)
    if best:
        print(f"\nlowest bar that refuses nothing honest: {best[0]:.3f} "
              f"(catches {best[1]}/{len(scam)} scams)")

    print("\nhonest text at or above 0.99 (would still be refused):")
    for name in ("signage (rendered)", "BONC listings (real)"):
        for risk, text in sorted(groups.get(name, []), reverse=True)[:4]:
            if risk >= 0.90:
                print(f"  {risk:.4f}  [{name}]  {text[:62]!r}")

    print("\nscam text below 0.99 (would get through):")
    for risk, text in sorted(groups.get("scam (rendered)", [])):
        if risk < 0.99:
            print(f"  {risk:.4f}  {text[:62]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
