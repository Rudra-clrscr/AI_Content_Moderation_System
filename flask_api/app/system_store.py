"""System database for managing image model policies, dataset images, categories, and training runs.

Uses SQLite (data/system.db) which is persistent across restarts and requires no external database.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger("app.system_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    default_action TEXT NOT NULL DEFAULT 'allow', -- 'allow' or 'block'
    description TEXT DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'allow', -- 'allow' or 'block'
    is_selected INTEGER NOT NULL DEFAULT 1, -- 1 = selected for training, 0 = deselected
    source TEXT NOT NULL DEFAULT 'dataset', -- 'dataset' or 'uploaded'
    description TEXT DEFAULT '',
    mime_type TEXT DEFAULT 'image/jpeg',
    embedding BLOB, -- 512-dim float32 vector for instant retraining
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (category) REFERENCES category (name) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS model_training (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    action_type TEXT NOT NULL, -- 'fine_tune', 'preset', etc.
    status TEXT NOT NULL, -- 'completed', 'in_progress', 'failed'
    total_samples INTEGER NOT NULL,
    allowed_count INTEGER NOT NULL,
    blocked_count INTEGER NOT NULL,
    classes TEXT NOT NULL, -- JSON array of trained classes
    accuracy REAL,
    details TEXT, -- JSON dict of metrics
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS system_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_CATEGORIES = [
    {
        "name": "minor",
        "display_name": "Minors & Children",
        "default_action": "block",
        "description": "Child or baby as main subject. Default policy blocks minor portraits to protect child privacy.",
        "is_system": 1,
    },
    {
        "name": "safe",
        "display_name": "Safe Products & General",
        "default_action": "allow",
        "description": "General business products, office scenes, machinery, food, textiles, and incidental people.",
        "is_system": 1,
    },
    {
        "name": "weapon",
        "display_name": "Weapons & Firearms",
        "default_action": "block",
        "description": "Guns, pistols, rifles, combat knives, and violent weaponry.",
        "is_system": 1,
    },
    {
        "name": "gore",
        "display_name": "Graphic Violence & Gore",
        "default_action": "block",
        "description": "Bloody wounds, severe injuries, and graphic violence.",
        "is_system": 1,
    },
    {
        "name": "revealing",
        "display_name": "Revealing Attire & Glamour",
        "default_action": "block",
        "description": "Lingerie, revealing model poses, and non-business glamour photography.",
        "is_system": 1,
    },
    {
        "name": "physique",
        "display_name": "Physique & Gym Selfies",
        "default_action": "block",
        "description": "Shirtless bodybuilding poses and personal physique display.",
        "is_system": 1,
    },
    {
        "name": "wedding",
        "display_name": "Weddings & Ceremonies",
        "default_action": "block",
        "description": "Personal wedding ceremonies and processions (non-commercial).",
        "is_system": 1,
    },
    {
        "name": "romantic",
        "display_name": "Romantic & Dating",
        "default_action": "block",
        "description": "Intimate couple photographs, kissing, and dating scenes.",
        "is_system": 1,
    },
    {
        "name": "unrest",
        "display_name": "Violence & Civil Unrest",
        "default_action": "block",
        "description": "Riots, street confrontations, burning vehicles, and violent demonstrations.",
        "is_system": 1,
    },
    {
        "name": "manhandling",
        "display_name": "Fighting & Physical Assault",
        "default_action": "block",
        "description": "Physical fights, brawls, aggressive assaults, and forced restraint.",
        "is_system": 1,
    },
    {
        "name": "charity",
        "display_name": "Charity & NGO Solicitation",
        "default_action": "block",
        "description": "Donation appeals, fundraising posters, and charity campaigns.",
        "is_system": 1,
    },
    {
        "name": "sexual",
        "display_name": "Explicit & Nudity",
        "default_action": "block",
        "description": "Pornography, explicit sexual content, and nudity.",
        "is_system": 1,
    },
    {
        "name": "child_products",
        "display_name": "Child & Baby Products (e.g. Diapers)",
        "default_action": "allow",
        "description": "Baby care, infant clothing, diapers (Mamy Poko Pants), and pediatric trade goods.",
        "is_system": 0,
    },
]

ALL_DATASET_MAPPING = [
    ("minors", "minor", "block", "Pictures of children and baby portraits"),
    ("minors_incidental", "safe", "allow", "Children present incidentally in business/family life"),
    ("allow", "safe", "allow", "Standard B2B product photos, offices, merchandise"),
    ("BONC_Positive", "safe", "allow", "Real verified legitimate BONC listings and trades"),
    ("body_parts", "safe", "allow", "Physiotherapy, medical, anatomy, and athlete photos"),
    ("crowds_safe", "safe", "allow", "Markets, queues, trade fairs, ordinary crowds"),
    ("gym_positive", "safe", "allow", "Fitness centers, gyms, workout machinery, equipment"),
    ("gym_neg", "physique", "block", "Shirtless bodybuilding and physique display"),
    ("flag", "weapon", "block", "Weapon detection footage and firearm frames"),
    ("weapons", "weapon", "block", "Guns, rifles, pistols, combat knives, blades"),
    ("graphic_injury_gore_500/images/graphic_injury_gore", "gore", "block", "Bloody wounds, graphic violence and injuries"),
    ("graphic_injury_gore_500/images/non_graphic", "safe", "allow", "Non-graphic medical and emergency scenes"),
    ("revealing", "revealing", "block", "Lingerie, revealing poses, glamour modelling"),
    ("romance", "romantic", "block", "Intimate couple photos, kissing, dating"),
    ("unrest", "unrest", "block", "Riots, street fighting, civil unrest"),
    ("unrest_real", "unrest", "block", "Press photography of confrontations"),
    ("violence", "manhandling", "block", "Physical brawls, fighting, assaults"),
    ("threats", "manhandling", "block", "Intimidation, aggressive threats, brandishing"),
    ("weddings", "wedding", "block", "Wedding ceremonies, mandaps, processions"),
    ("weddings_commons", "wedding", "block", "Wedding and ceremony photography"),
    ("NGO", "charity", "block", "NGO appeals, donation banners, fundraising"),
    ("nudity", "sexual", "block", "Explicit nudity and sexual content"),
    ("_quarantine_explicit", "sexual", "block", "Quarantined explicit material"),
    ("_quarantine_istock", "minor", "block", "Quarantined child photography"),
]


class SystemStore:
    """Thread-safe SQLite store for system image policies, categories, and training logs."""

    def __init__(self, path: str | Path | None = None, images_dir: Path | None = None):
        if path is None:
            self.path = Path(__file__).resolve().parent.parent / "data" / "system.db"
        else:
            self.path = Path(path)
        
        self.images_dir = images_dir or (Path(__file__).resolve().parent.parent.parent / "images")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        with self._tx() as db:
            db.executescript(SCHEMA)

        self._init_categories()
        self._seed_images_if_empty()

    @contextmanager
    def _tx(self):
        with self._lock:
            db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    # ---- Category Management -----------------------------------------------------------

    def _init_categories(self) -> None:
        now = time.time()
        with self._tx() as db:
            for cat in DEFAULT_CATEGORIES:
                db.execute(
                    "INSERT OR IGNORE INTO category (name, display_name, default_action, description, is_system, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cat["name"], cat["display_name"], cat["default_action"], cat["description"], cat["is_system"], now),
                )

    def list_categories(self) -> list[dict[str, Any]]:
        with self._tx() as db:
            rows = db.execute("SELECT * FROM category ORDER BY is_system DESC, name ASC").fetchall()
            out = []
            for r in rows:
                c_name = r["name"]
                img_stats = db.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN action = 'allow' THEN 1 ELSE 0 END) as allowed, "
                    "SUM(CASE WHEN action = 'block' THEN 1 ELSE 0 END) as blocked, "
                    "SUM(CASE WHEN is_selected = 1 THEN 1 ELSE 0 END) as selected "
                    "FROM image_policy WHERE category = ?",
                    (c_name,),
                ).fetchone()
                d = dict(r)
                d["total_images"] = img_stats["total"] or 0
                d["allowed_images"] = img_stats["allowed"] or 0
                d["blocked_images"] = img_stats["blocked"] or 0
                d["selected_images"] = img_stats["selected"] or 0
                out.append(d)
            return out

    def get_category(self, name: str) -> dict[str, Any] | None:
        with self._tx() as db:
            row = db.execute("SELECT * FROM category WHERE name = ?", (name.strip().lower(),)).fetchone()
            return dict(row) if row else None

    def add_category(self, name: str, display_name: str, default_action: str = "allow", description: str = "") -> dict[str, Any]:
        norm_name = name.strip().lower().replace(" ", "_")
        norm_action = "allow" if default_action.lower() == "allow" else "block"
        now = time.time()
        with self._tx() as db:
            existing = db.execute("SELECT * FROM category WHERE name = ?", (norm_name,)).fetchone()
            if existing:
                raise ValueError(f"Category '{norm_name}' already exists.")
            db.execute(
                "INSERT INTO category (name, display_name, default_action, description, is_system, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (norm_name, display_name.strip() or norm_name.capitalize(), norm_action, description.strip(), now),
            )
        return self.get_category(norm_name)

    def update_category(self, name: str, display_name: str | None = None, default_action: str | None = None, description: str | None = None) -> dict[str, Any]:
        norm_name = name.strip().lower()
        cat = self.get_category(norm_name)
        if not cat:
            raise ValueError(f"Category '{norm_name}' not found.")
        
        updates = []
        params = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name.strip())
        if default_action is not None:
            act = "allow" if default_action.lower() == "allow" else "block"
            updates.append("default_action = ?")
            params.append(act)
        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())
        
        if updates:
            params.append(norm_name)
            with self._tx() as db:
                db.execute(f"UPDATE category SET {', '.join(updates)} WHERE name = ?", params)
        return self.get_category(norm_name)

    def set_category_policy(self, name: str, action: str) -> int:
        """Bulk update all images in a category to either 'allow' or 'block'."""
        norm_name = name.strip().lower()
        norm_action = "allow" if action.lower() == "allow" else "block"
        now = time.time()
        with self._tx() as db:
            cur = db.execute(
                "UPDATE image_policy SET action = ?, updated_at = ? WHERE category = ?",
                (norm_action, now, norm_name),
            )
            db.execute("UPDATE category SET default_action = ? WHERE name = ?", (norm_action, norm_name))
            return cur.rowcount

    # ---- Image Seeding & Storage -------------------------------------------------------


    def load_all_dataset_images(self, images_dir: Path | None = None) -> dict[str, int]:
        """Scan and load every image from images/ into image_policy table without duplicating."""
        root = Path(images_dir) if images_dir else self.images_dir
        if not root.is_dir():
            log.warning("images directory %s not found", root)
            return {"inserted": 0, "total": 0}

        self._init_categories()
        now = time.time()
        exts = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tif", ".tiff"}

        with self._tx() as db:
            existing_paths = set(
                row[0] for row in db.execute("SELECT file_path FROM image_policy").fetchall()
            )

        to_insert = []
        for folder_rel, category, action, desc in ALL_DATASET_MAPPING:
            folder = root / folder_rel
            if not folder.is_dir():
                continue
            files = sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts and not f.name.startswith(".")])
            for p in files:
                rel_path = str(p.relative_to(root.parent)).replace("\\", "/")
                if rel_path in existing_paths:
                    continue
                existing_paths.add(rel_path)
                mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                to_insert.append((
                    p.name,
                    rel_path,
                    category,
                    action,
                    1,
                    "dataset",
                    desc,
                    mime,
                    now,
                    now,
                ))

        if to_insert:
            with self._tx() as db:
                db.executemany(
                    "INSERT INTO image_policy (filename, file_path, category, action, is_selected, source, description, mime_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    to_insert,
                )
            log.info("Inserted %d new dataset images into image_policy", len(to_insert))

        with self._tx() as db:
            total = db.execute("SELECT COUNT(*) FROM image_policy").fetchone()[0]

        return {"inserted": len(to_insert), "total": total}

    def _seed_images_if_empty(self) -> None:
        """Seed representative stored images from images/ on first run."""
        with self._tx() as db:
            count = db.execute("SELECT COUNT(*) FROM image_policy").fetchone()[0]
            if count > 0:
                return

        if not self.images_dir.is_dir():
            log.warning("images directory %s not found for initial seeding", self.images_dir)
            return

        seed_mapping = [
            ("minors", "minor", "block", 25),
            ("minors_incidental", "safe", "allow", 25),
            ("allow", "safe", "allow", 25),
            ("BONC_Positive", "safe", "allow", 20),
            ("weapons", "weapon", "block", 20),
            ("graphic_injury_gore_500/images/graphic_injury_gore", "gore", "block", 20),
            ("revealing", "revealing", "block", 20),
            ("gym_neg", "physique", "block", 15),
            ("gym_positive", "safe", "allow", 15),
            ("weddings", "wedding", "block", 15),
        ]

        now = time.time()
        to_insert = []
        for folder_name, category, action, limit in seed_mapping:
            folder = self.images_dir / folder_name
            if not folder.is_dir():
                continue
            
            exts = {".jpg", ".jpeg", ".png", ".webp"}
            found_files = sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts and not f.name.startswith(".")])
            selected_files = found_files[:limit]

            for p in selected_files:
                rel_path = str(p.relative_to(self.images_dir.parent)).replace("\\", "/")
                mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                to_insert.append((
                    p.name,
                    rel_path,
                    category,
                    action,
                    1, # selected
                    "dataset",
                    f"Sample policy image from {folder_name}",
                    mime,
                    now,
                    now,
                ))

        if to_insert:
            with self._tx() as db:
                db.executemany(
                    "INSERT INTO image_policy (filename, file_path, category, action, is_selected, source, description, mime_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    to_insert,
                )
            log.info("Seeded %d images into system image_policy database", len(to_insert))

    # ---- Image Querying & Updates -----------------------------------------------------

    def list_images(
        self,
        category: str | None = None,
        action: str | None = None,
        is_selected: int | None = None,
        search: str | None = None,
        page: int = 1,
        limit: int = 24,
    ) -> dict[str, Any]:
        clauses = []
        params = []

        if category and category.lower() != "all":
            clauses.append("category = ?")
            params.append(category.strip().lower())
        if action and action.lower() != "all":
            clauses.append("action = ?")
            params.append(action.strip().lower())
        if is_selected is not None:
            clauses.append("is_selected = ?")
            params.append(int(is_selected))
        if search and search.strip():
            clauses.append("(filename LIKE ? OR description LIKE ?)")
            pat = f"%{search.strip()}%"
            params.extend([pat, pat])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._tx() as db:
            total = db.execute(f"SELECT COUNT(*) FROM image_policy {where_sql}", params).fetchone()[0]
            offset = max(0, (page - 1) * limit)
            rows = db.execute(
                f"SELECT id, filename, file_path, category, action, is_selected, source, description, mime_type, "
                f"(embedding IS NOT NULL) as has_embedding, created_at, updated_at "
                f"FROM image_policy {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            items = [dict(r) for r in rows]

            # Global statistics
            stats = db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN action = 'allow' THEN 1 ELSE 0 END) as allowed, "
                "SUM(CASE WHEN action = 'block' THEN 1 ELSE 0 END) as blocked, "
                "SUM(CASE WHEN is_selected = 1 THEN 1 ELSE 0 END) as selected, "
                "SUM(CASE WHEN is_selected = 0 THEN 1 ELSE 0 END) as deselected "
                "FROM image_policy"
            ).fetchone()

        total_pages = max(1, (total + limit - 1) // limit)
        return {
            "items": items,
            "pagination": {"page": page, "limit": limit, "total": total, "total_pages": total_pages},
            "stats": {
                "total": stats["total"] or 0,
                "allowed": stats["allowed"] or 0,
                "blocked": stats["blocked"] or 0,
                "selected": stats["selected"] or 0,
                "deselected": stats["deselected"] or 0,
            },
        }

    def get_image(self, image_id: int) -> dict[str, Any] | None:
        with self._tx() as db:
            row = db.execute(
                "SELECT id, filename, file_path, category, action, is_selected, source, description, mime_type, "
                "(embedding IS NOT NULL) as has_embedding, created_at, updated_at "
                "FROM image_policy WHERE id = ?",
                (image_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_image(
        self,
        filename: str,
        file_path: str,
        category: str,
        action: str = "allow",
        is_selected: int = 1,
        source: str = "uploaded",
        description: str = "",
        mime_type: str = "image/jpeg",
        embedding: bytes | None = None,
    ) -> dict[str, Any]:
        norm_cat = category.strip().lower()
        norm_act = "allow" if action.lower() == "allow" else "block"
        now = time.time()
        with self._tx() as db:
            # Ensure category exists
            cat_row = db.execute("SELECT * FROM category WHERE name = ?", (norm_cat,)).fetchone()
            if not cat_row:
                db.execute(
                    "INSERT INTO category (name, display_name, default_action, created_at) VALUES (?, ?, ?, ?)",
                    (norm_cat, norm_cat.capitalize(), norm_act, now),
                )

            cur = db.execute(
                "INSERT INTO image_policy (filename, file_path, category, action, is_selected, source, description, mime_type, embedding, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filename, file_path, norm_cat, norm_act, int(is_selected), source, description, mime_type, embedding, now, now),
            )
            image_id = cur.lastrowid
        return self.get_image(image_id)

    def update_image(
        self,
        image_id: int,
        action: str | None = None,
        is_selected: int | None = None,
        category: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        updates = ["updated_at = ?"]
        params: list[Any] = [now]

        if action is not None:
            updates.append("action = ?")
            params.append("allow" if action.lower() == "allow" else "block")
        if is_selected is not None:
            updates.append("is_selected = ?")
            params.append(1 if is_selected else 0)
        if category is not None:
            updates.append("category = ?")
            params.append(category.strip().lower())
        if description is not None:
            updates.append("description = ?")
            params.append(description.strip())

        params.append(image_id)
        with self._tx() as db:
            db.execute(f"UPDATE image_policy SET {', '.join(updates)} WHERE id = ?", params)
        return self.get_image(image_id)

    def batch_update_images(self, updates: list[dict[str, Any]]) -> int:
        """Batch update image records (e.g. action or is_selected)."""
        now = time.time()
        count = 0
        with self._tx() as db:
            for item in updates:
                iid = item.get("id")
                if not iid:
                    continue
                fields = ["updated_at = ?"]
                params: list[Any] = [now]
                if "action" in item:
                    fields.append("action = ?")
                    params.append("allow" if str(item["action"]).lower() == "allow" else "block")
                if "is_selected" in item:
                    fields.append("is_selected = ?")
                    params.append(1 if item["is_selected"] else 0)
                if "category" in item:
                    fields.append("category = ?")
                    params.append(str(item["category"]).strip().lower())

                params.append(iid)
                cur = db.execute(f"UPDATE image_policy SET {', '.join(fields)} WHERE id = ?", params)
                count += cur.rowcount
        return count

    def set_selection_bulk(self, is_selected: bool, category: str | None = None) -> int:
        """Select or deselect all images, optionally filtered by category."""
        now = time.time()
        sel = 1 if is_selected else 0
        with self._tx() as db:
            if category and category.lower() != "all":
                cur = db.execute(
                    "UPDATE image_policy SET is_selected = ?, updated_at = ? WHERE category = ?",
                    (sel, now, category.strip().lower()),
                )
            else:
                cur = db.execute("UPDATE image_policy SET is_selected = ?, updated_at = ?", (sel, now))
            return cur.rowcount

    def get_selected_for_training(self) -> list[dict[str, Any]]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT id, filename, file_path, category, action, embedding FROM image_policy "
                "WHERE is_selected = 1 ORDER BY category ASC, id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def save_embedding(self, image_id: int, embedding_bytes: bytes) -> None:
        with self._tx() as db:
            db.execute("UPDATE image_policy SET embedding = ? WHERE id = ?", (embedding_bytes, image_id))

    # ---- Model Training Records & System Settings ------------------------------------

    def record_training(
        self,
        model_name: str,
        action_type: str,
        status: str,
        total_samples: int,
        allowed_count: int,
        blocked_count: int,
        classes: list[str],
        accuracy: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        now = time.time()
        with self._tx() as db:
            cur = db.execute(
                "INSERT INTO model_training (model_name, action_type, status, total_samples, allowed_count, blocked_count, classes, accuracy, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model_name,
                    action_type,
                    status,
                    total_samples,
                    allowed_count,
                    blocked_count,
                    json.dumps(classes),
                    accuracy,
                    json.dumps(details or {}),
                    now,
                ),
            )
            return cur.lastrowid

    def list_training_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._tx() as db:
            rows = db.execute("SELECT * FROM model_training ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["classes"] = json.loads(d["classes"])
                except Exception:
                    pass
                try:
                    d["details"] = json.loads(d["details"])
                except Exception:
                    pass
                out.append(d)
            return out

    def get_setting(self, key: str, default: str = "") -> str:
        with self._tx() as db:
            row = db.execute("SELECT value FROM system_setting WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO system_setting (key, value) VALUES (?, ?)", (key, str(value)))
