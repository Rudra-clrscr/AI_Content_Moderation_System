import csv, sys
from pathlib import Path
sys.path.insert(0, 'flask_api')
from app.model import OnnxScorer

sc8 = OnnxScorer('flask_api/models/v8')
sc9 = OnnxScorer('flask_api/models/v9')

with open('training/eval_public_callouts.csv', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

print(f"Total rows in eval_public_callouts.csv: {len(rows)}")
print(f"{'Row':<4} | {'Grp':<22} | {'Lbl':<4} | {'v8':<7} | {'v9':<7} | Text")
print("-" * 105)

for i, r in enumerate(rows):
    y = int(r.get('label', 0))
    s8 = sc8.score(r['text']).risk_score
    s9 = sc9.score(r['text']).risk_score
    p8 = int(s8 >= 0.5)
    p9 = int(s9 >= 0.5)
    
    if p9 != y or p8 != p9:
        status = "FIXED_IN_V9" if (p9 == y and p8 != y) else ("REGRESSED" if (p8 == y and p9 != y) else "WRONG_BOTH")
        print(f"{i:<4} | {r.get('group', ''):<22} | {y:<4} | {s8:7.4f} | {s9:7.4f} | [{status}] {r['text'][:55]}")
