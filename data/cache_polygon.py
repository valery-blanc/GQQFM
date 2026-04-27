"""Cache SQLite pour les réponses Polygon.io (historique = immuable, TTL infini)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", ".polygon_cache.db")
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    conn = sqlite3.connect(_CACHE_PATH, timeout=10.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS responses ("
        "  cache_key TEXT PRIMARY KEY,"
        "  payload   TEXT NOT NULL,"
        "  created   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    return conn


def get(cache_key: str) -> Any | None:
    """Retourne le payload JSON désérialisé ou None si la clé est absente."""
    with _LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT payload FROM responses WHERE cache_key = ?", (cache_key,)
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def set(cache_key: str, payload: Any) -> None:
    """Enregistre payload (sérialisé en JSON) sous cache_key. Écrase si existe."""
    blob = json.dumps(payload, separators=(",", ":"))
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO responses (cache_key, payload) VALUES (?, ?)",
            (cache_key, blob),
        )
        conn.commit()


def make_key(path: str, params: dict) -> str:
    """Construit une clé déterministe depuis path + params triés."""
    items = sorted((k, str(v)) for k, v in params.items() if k != "apiKey")
    return path + "?" + "&".join(f"{k}={v}" for k, v in items)
