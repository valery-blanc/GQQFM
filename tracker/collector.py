"""Collecte toutes les 30min les prix bid/ask/mid réels via Polygon snapshot."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
POLYGON_BASE = "https://api.polygon.io"

DATA_DIR  = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH   = DATA_DIR / "tracker.db"
REPO_DIR  = Path(os.environ.get("REPO_DIR", "/repo"))
COMBOS_PATH = REPO_DIR / "data" / "tracked_combos.json"
API_KEY   = os.environ.get("POLYGON_API_KEY", "")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                combo_id        TEXT    NOT NULL,
                leg_symbol      TEXT    NOT NULL,
                bid             REAL,
                ask             REAL,
                mid             REAL,
                underlying_spot REAL,
                iv              REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_combo_ts ON prices(combo_id, timestamp)"
        )


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


def get_snapshot(underlying: str, contract_symbol: str) -> dict | None:
    """Fetche le snapshot Polygon pour un contrat (données 15min delayed)."""
    url = f"{POLYGON_BASE}/v3/snapshot/options/{underlying}/{contract_symbol}"
    try:
        resp = requests.get(url, params={"apiKey": API_KEY}, timeout=15)
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", {})
        return results if results else None
    except Exception as exc:
        logger.warning("Snapshot %s failed: %s", contract_symbol, exc)
        return None


def collect_once() -> None:
    """Collecte les prix pour tous les combos trackés (appelé par le scheduler)."""
    if not is_market_open():
        logger.debug("Marché fermé — pas de collecte")
        return

    if not COMBOS_PATH.exists():
        logger.warning("tracked_combos.json introuvable : %s", COMBOS_PATH)
        return

    combos = json.loads(COMBOS_PATH.read_text()).get("combos", [])
    if not combos:
        logger.debug("Aucun combo à tracker")
        return

    timestamp = datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[tuple] = []

    for combo in combos:
        underlying = combo["symbol"]
        combo_id   = combo["id"]
        for leg in combo["legs"]:
            snap = get_snapshot(underlying, leg["contract_symbol"])
            if snap is None:
                continue
            quote = snap.get("last_quote", {})
            spot  = snap.get("underlying_asset", {}).get("price")
            rows.append((
                timestamp, combo_id, leg["contract_symbol"],
                quote.get("bid"), quote.get("ask"), quote.get("midpoint"),
                spot, snap.get("implied_volatility"),
            ))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO prices "
                "(timestamp,combo_id,leg_symbol,bid,ask,mid,underlying_spot,iv) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
        logger.info("Collecté %d mesures @ %s", len(rows), timestamp)


def pull_repo() -> None:
    """git pull pour récupérer les mises à jour de tracked_combos.json."""
    try:
        import git
        repo = git.Repo(REPO_DIR)
        repo.remotes.origin.pull()
        logger.info("git pull OK")
    except Exception as exc:
        logger.warning("git pull failed: %s", exc)
