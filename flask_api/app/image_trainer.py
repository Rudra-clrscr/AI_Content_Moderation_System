"""Fine-tune the vision model's linear probe on selected images and allow/block policies.

Allows business-specific fine-tuning (e.g. allowing child/baby images for baby product
businesses like Mamy Poko Pants, or adjusting weapon/physique thresholds) and updates the
active probe in real-time without taking the service down.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from sklearn.linear_model import LogisticRegression

from app.system_store import SystemStore

log = logging.getLogger("app.image_trainer")

DEFAULT_CLASSES = ["safe", "weapon", "gore", "minor", "revealing"]


class ImageTrainer:
    """Manages embedding generation, logistic regression probe fine-tuning, and model reloading."""

    def __init__(self, system_store: SystemStore, visual_check=None, model_dir: Path | None = None):
        self.store = system_store
        self.visual_check = visual_check
        self.model_dir = Path(model_dir) if model_dir else (Path(__file__).resolve().parent.parent / "models" / "img_v1")
        self.project_root = Path(__file__).resolve().parent.parent.parent

        # Backup default probe.npz if not already backed up
        default_probe = self.model_dir / "probe.npz"
        backup_probe = self.model_dir / "probe_backup_default.npz"
        if default_probe.exists() and not backup_probe.exists():
            try:
                shutil.copy2(default_probe, backup_probe)
                log.info("Backed up default probe to %s", backup_probe.name)
            except Exception:
                log.warning("Could not backup default probe.npz", exc_info=True)

    def resolve_image_path(self, rel_or_abs_path: str) -> Path | None:
        p = Path(rel_or_abs_path)
        if p.is_absolute() and p.exists():
            return p
        # Check relative to project root
        cand1 = self.project_root / rel_or_abs_path
        if cand1.exists():
            return cand1
        # Check relative to flask_api
        cand2 = self.project_root / "flask_api" / rel_or_abs_path
        if cand2.exists():
            return cand2
        # Check relative to images
        cand3 = self.project_root / "images" / rel_or_abs_path
        if cand3.exists():
            return cand3
        return None

    def compute_embedding(self, image_path: Path) -> np.ndarray | None:
        """Compute 512-dim normalized CLIP embedding for an image."""
        if not self.visual_check:
            return None
        self.visual_check.ready
        if not self.visual_check._session:
            return None

        try:
            with Image.open(image_path) as im:
                im = im.convert("RGB")
                pixels = self.visual_check.preprocess(im)
                emb = self.visual_check._session.run(
                    [self.visual_check._image_output],
                    {"pixel_values": pixels},
                )[0][0]
                norm = np.linalg.norm(emb)
                if norm > 1e-12:
                    emb = emb / norm
                return emb.astype(np.float32)
        except Exception:
            log.warning("Failed to extract embedding from %s", image_path, exc_info=True)
            return None

    def train_probe(
        self,
        c_param: float = 1.0,
        preset: str | None = None,
    ) -> dict[str, Any]:
        """Fine-tune linear probe over selected images with active allow/block policies.

        When baby/child images (category 'minor') are set to action='allow', their training
        target is mapped to 'safe', effectively training the model to allow baby product photos!
        """
        start_time = time.time()
        selected_images = self.store.get_selected_for_training()
        if not selected_images:
            raise ValueError("No images selected for training. Select at least 10 images.")

        X_list = []
        target_labels = []
        action_counts = {"allow": 0, "block": 0}

        log.info("Preparing embeddings for %d selected images...", len(selected_images))
        for img in selected_images:
            emb = None
            if img.get("embedding"):
                try:
                    emb = np.frombuffer(img["embedding"], dtype=np.float32)
                except Exception:
                    emb = None

            if emb is None or emb.shape != (512,):
                path = self.resolve_image_path(img["file_path"])
                if path and path.exists():
                    emb = self.compute_embedding(path)
                    if emb is not None:
                        self.store.save_embedding(img["id"], emb.tobytes())

            if emb is None:
                continue

            action = img["action"].lower()
            category = img["category"].lower()
            action_counts[action] = action_counts.get(action, 0) + 1

            # The key policy logic:
            # If action is 'allow', the image belongs to the 'safe' class!
            # If action is 'block', it belongs to its unsafe category (e.g. 'minor', 'weapon', 'gore', 'revealing').
            if action == "allow":
                target_label = "safe"
            else:
                target_label = category if category in DEFAULT_CLASSES and category != "safe" else "weapon"

            X_list.append(emb)
            target_labels.append(target_label)

        if len(X_list) < 5:
            raise ValueError(f"Only {len(X_list)} valid image embeddings found. Need at least 5 to train.")

        # Determine classes
        classes = sorted(set(target_labels))
        if "safe" not in classes:
            classes.insert(0, "safe")

        # Map labels to integer indices
        class_to_idx = {c: i for i, c in enumerate(classes)}
        y = np.array([class_to_idx[lbl] for lbl in target_labels], dtype=np.int32)
        X = np.vstack(X_list).astype(np.float32)

        unique_y, counts_y = np.unique(y, return_counts=True)
        class_distribution = {classes[idx]: int(cnt) for idx, cnt in zip(unique_y, counts_y)}
        log.info("Class distribution for training: %s", class_distribution)

        # Train Logistic Regression probe
        if len(classes) == 1:
            raise ValueError("All selected images have the same policy outcome. Include both allowed and blocked samples.")

        clf = LogisticRegression(
            C=c_param,
            max_iter=1500,
            class_weight="balanced",
            random_state=42,
        )
        clf.fit(X, y)
        accuracy = float(clf.score(X, y))

        # Format weights for VisualCheck: (n_classes, 512) and (n_classes,)
        if len(classes) == 2:
            # Binary logistic regression in sklearn produces shape (1, 512) and (1,)
            W = np.vstack([-clf.coef_, clf.coef_]).astype(np.float32)
            b = np.array([-clf.intercept_[0], clf.intercept_[0]], dtype=np.float32)
        else:
            W = clf.coef_.astype(np.float32)
            b = clf.intercept_.astype(np.float32)

        # Save to probe.npz
        probe_path = self.model_dir / "probe.npz"
        np.savez(
            probe_path,
            W=W,
            b=b,
            classes=np.array(classes),
            dim=np.array(512),
            encoder=np.array("clip-vit-b32-bonc-img-v1"),
        )
        log.info("Saved fine-tuned probe.npz with %d classes (accuracy: %.4f)", len(classes), accuracy)

        # Update per-category threshold overrides:
        # If minors are allowed in this training configuration, set minor prompt threshold high (e.g. 1.01)
        # so zero-shot prompts will also not block it!
        minor_images = [img for img in selected_images if img["category"].lower() == "minor"]
        minor_allowed = bool(minor_images and all(img["action"].lower() == "allow" for img in minor_images))

        threshold_overrides: dict[str, float] = {}
        if minor_allowed:
            threshold_overrides["minor"] = 1.01
            log.info("Policy override active: Child & Baby images allowed (minor threshold set to 1.01)")
        else:
            threshold_overrides["minor"] = 0.85

        # Hot-reload in running VisualCheck
        if self.visual_check and hasattr(self.visual_check, "reload_probe"):
            self.visual_check.reload_probe(probe_path, threshold_overrides=threshold_overrides)

        # AFTER the hot-reload, so the report describes the probe that is now live rather
        # than the one it replaced.
        try:
            report = self.category_report(selected_images)
        except Exception:
            log.warning("could not build the per-category report", exc_info=True)
            report = {}

        duration = round(time.time() - start_time, 2)

        # Record training history in system DB
        model_name = f"probe_finetuned_{int(time.time())}"
        details = {
            "duration_seconds": duration,
            "classes": classes,
            "class_distribution": class_distribution,
            "threshold_overrides": threshold_overrides,
            "preset": preset or ("child_products_allowed" if minor_allowed else "standard_blocked"),
            # Kept with the run so the history can answer "what did that retrain do?" later.
            # Without it the only record of a retrain is its accuracy, which is the number
            # that was not answering the question in the first place.
            "category_report": report,
        }
        rec_id = self.store.record_training(
            model_name=model_name,
            action_type="fine_tune",
            status="completed",
            total_samples=len(X),
            allowed_count=action_counts.get("allow", 0),
            blocked_count=action_counts.get("block", 0),
            classes=classes,
            accuracy=accuracy,
            details=details,
        )

        # Update system setting
        self.store.set_setting(
            "active_policy_mode",
            "child_products" if minor_allowed else "standard",
        )
        self.store.set_setting("last_training_id", str(rec_id))

        return {
            "success": True,
            "training_id": rec_id,
            "model_name": model_name,
            "accuracy": round(accuracy * 100, 2),
            "samples": len(X),
            "allowed_count": action_counts.get("allow", 0),
            "blocked_count": action_counts.get("block", 0),
            "classes": classes,
            "class_distribution": class_distribution,
            "duration": duration,
            "minor_policy": "ALLOWED (Baby & Child Products Mode)" if minor_allowed else "BLOCKED (Default Policy)",
            # Per-category, through the real decision path. `accuracy` above is the probe's
            # fit to the data it was just given; this is what the Articles tab will now do.
            "category_report": report,
            "report_note": ("Refusal rates are measured on the pictures just trained on, "
                            "through the same three channels the Articles tab uses. The "
                            "probe's share is in-sample, so treat these as optimistic."),
            "message": f"Vision model fine-tuned successfully on {len(X)} samples with {round(accuracy * 100, 1)}% accuracy in {duration}s.",
        }

    def category_report(self, images: list[dict[str, Any]]) -> dict[str, Any]:
        """What the live system now does to the admin's own pictures, category by category.

        WHY THIS EXISTS. A retrain reported one number - training accuracy, 85.4% over 3490
        images - and an admin read it as "the model learned what I selected". It cannot mean
        that. It is measured on the data just fitted, it averages over every category at once,
        and it describes the probe alone while the Articles tab is decided by three channels
        together. An admin who selected 72 gym pictures, retrained, saw 85.4% and then watched
        a gym photograph publish had no way to find out that the category they cared about was
        at 59% and that 49 of the gym pictures in their search were teaching ALLOW.

        So the report answers the question actually being asked - "what will happen to these
        pictures now?" - by putting each stored embedding through score_embedding and
        decides_reject: the same code path, the same bars and the same three channels the
        Articles tab uses. Nothing here re-implements the decision, because a number that
        comes from somewhere other than the code that decides is how this went wrong.

        `contradicting` is the figure to read first: pictures the admin marked one way that
        the system treats the other way. Those are the ones that make a retrain look broken.

        The probe's share of this IS in-sample - these are the pictures it was just fitted on
        - so the refusal rates are optimistic and the caller says so. They are still the right
        numbers to show, because the admin's question is about these pictures.
        """
        vc = self.visual_check
        if vc is None or not getattr(vc, "ready", False):
            return {}

        cats: dict[str, dict[str, Any]] = {}
        for img in images:
            blob = img.get("embedding")
            if not blob:
                continue
            try:
                emb = np.frombuffer(blob, dtype=np.float32)
            except Exception:
                continue
            if emb.shape != (512,):
                continue
            try:
                res = vc.score_embedding(emb)
                refused = bool(vc.decides_reject(res))
                cause = res.refused_by(vc.config) if refused else None
            except Exception:
                log.warning("could not score a stored embedding for the report", exc_info=True)
                continue

            cat = str(img.get("category", "") or "").lower() or "uncategorised"
            action = str(img.get("action", "") or "").lower()
            c = cats.setdefault(cat, {"n": 0, "teaching_allow": 0, "teaching_block": 0,
                                      "refused": 0, "by_cause": {}, "contradicting": 0,
                                      "examples": []})
            c["n"] += 1
            c["teaching_allow" if action == "allow" else "teaching_block"] += 1
            if refused:
                c["refused"] += 1
                c["by_cause"][cause or "policy"] = c["by_cause"].get(cause or "policy", 0) + 1
            # Marked block and still publishes, or marked allow and is refused anyway.
            if (action == "block" and not refused) or (action == "allow" and refused):
                c["contradicting"] += 1
                if len(c["examples"]) < 5:
                    c["examples"].append({
                        "id": img.get("id"), "filename": img.get("filename"),
                        "marked": action, "verdict": "refused" if refused else "published",
                        "refused_by": cause,
                        "top_label": res.label, "top_score": round(res.score, 3),
                        "probe_unsafe": round(res.probe_unsafe, 3),
                    })

        for cat, c in cats.items():
            c["refused_pct"] = round(100.0 * c["refused"] / c["n"], 1) if c["n"] else 0.0
            # A category that teaches in both directions is not a bug, but it is the thing an
            # admin almost never means to do, so it is named rather than left to be inferred
            # from two counts sitting side by side.
            c["teaches_both_ways"] = bool(c["teaching_allow"] and c["teaching_block"])
        return cats

    def apply_preset(self, preset_name: str) -> dict[str, Any]:
        """Apply a business policy preset and re-train model.

        - 'child_products': Allows baby & child images (for Mamy Poko Pants, baby apparel, etc.)
        - 'standard': Restores default BONC platform policy (blocks minor portraits)
        """
        norm = preset_name.strip().lower()
        if norm in ("child_products", "baby_care", "mamy_poko"):
            # Set minor category to allow
            self.store.set_category_policy("minor", "allow")
            self.store.set_category_policy("child_products", "allow")
            return self.train_probe(preset="child_products")
        elif norm in ("standard", "default", "strict_minors"):
            # Restore minor category to block
            self.store.set_category_policy("minor", "block")
            return self.train_probe(preset="standard")
        else:
            raise ValueError(f"Unknown preset '{preset_name}'. Options: 'child_products', 'standard'")
