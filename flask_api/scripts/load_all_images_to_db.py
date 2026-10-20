"""Load all images from images/ into system.db and pre-compute their CLIP embeddings.

Usage:
    python scripts/load_all_images_to_db.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image

from app.clip import VisualCheck, VisualConfig
from app.system_store import SystemStore


def main():
    start_time = time.time()
    store = SystemStore()
    images_dir = ROOT.parent / "images"

    print(f"Scanning and loading all images from {images_dir} into system.db...")
    res = store.load_all_dataset_images(images_dir)
    print(f"-> Inserted {res['inserted']} new images. Total images in database: {res['total']}")

    # Check how many images need embeddings
    with store._tx() as db:
        missing = db.execute(
            "SELECT id, file_path FROM image_policy WHERE embedding IS NULL ORDER BY id ASC"
        ).fetchall()

    print(f"Images needing embeddings: {len(missing)}")
    if not missing:
        print("All images already have embeddings cached! Done.")
        return

    print("Initializing CLIP vision model on CUDA/DirectML/CPU...")
    vc = VisualCheck(VisualConfig(enabled=True, model_dir=str(ROOT / "models" / "img_v1"), device="auto"), root=ROOT)
    if not vc.ready:
        print(f"Warning: VisualCheck could not load: {vc.status.get('reason')}. Exiting embedding pre-computation.")
        return

    print(f"Vision model ready on {vc.status.get('provider')}! Pre-computing embeddings in batches...")

    batch_size = 32
    total_processed = 0
    t0 = time.time()

    for i in range(0, len(missing), batch_size):
        chunk = missing[i : i + batch_size]
        valid_items = []
        tensors = []

        for row in chunk:
            img_id = row["id"]
            file_path = row["file_path"]

            cand = ROOT.parent / file_path
            if not cand.exists():
                cand = ROOT / file_path
            if not cand.exists():
                continue

            try:
                with Image.open(cand) as im:
                    im = im.convert("RGB")
                    tensor = vc.preprocess(im)
                    tensors.append(tensor)
                    valid_items.append(img_id)
            except Exception:
                continue

        if not tensors:
            continue

        batch_pixels = np.vstack(tensors)
        embs = vc._session.run([vc._image_output], {"pixel_values": batch_pixels})[0]
        # Normalize
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        embs = embs / np.maximum(norms, 1e-12)

        # Write to SQLite
        with store._tx() as db:
            for iid, emb in zip(valid_items, embs):
                db.execute("UPDATE image_policy SET embedding = ? WHERE id = ?", (emb.astype(np.float32).tobytes(), iid))

        total_processed += len(valid_items)
        if total_processed % 320 == 0 or total_processed == len(missing):
            elapsed = time.time() - t0
            rate = round(total_processed / max(0.1, elapsed), 1)
            print(f"  Processed {total_processed}/{len(missing)} images ({rate} img/s)...")

    duration = round(time.time() - start_time, 2)
    print(f"\nSuccessfully loaded and embedded {total_processed} images in {duration}s!")

    # Print final category breakdown
    cats = store.list_categories()
    print("\nFinal Category Breakdown in system.db:")
    for c in cats:
        print(f"  - {c['name']:16}: {c['total_images']:4} total | {c['allowed_images']:4} allowed | {c['blocked_images']:4} blocked")


if __name__ == "__main__":
    main()
