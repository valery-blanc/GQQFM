"""Collecte toutes les 5min les prix réels via Polygon snapshot (free tier, 15min delay)."""

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

DATA_DIR    = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH     = DATA_DIR / "tracker.db"
COMBOS_PATH = DATA_DIR / "tracked_combos.json"
API_KEY     = os.environ.get("POLYGON_API_KEY", "")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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


def init_combos_file() -> None:
    """Crée tracked_combos.json vide si absent (première exécution)."""
    if not COMBOS_PATH.exists():
        COMBOS_PATH.write_text(json.dumps({"combos": []}, indent=2))
        logger.info("tracked_combos.json initialisé dans %s", DATA_DIR)


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_ = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_ <= now <= close_


def get_underlying_price(ticker: str) -> float | None:
    """Prix spot du sous-jacent via yfinance (15min delayed, gratuit)."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        price = info.last_price
        return float(price) if price else None
    except Exception as exc:
        logger.warning("yfinance spot %s failed: %s", ticker, exc)
        return None


def get_snapshot(underlying: str, contract_symbol: str) -> dict | None:
    """Fetche le snapshot Polygon pour un contrat (free tier, 15min delayed).

    Notes free tier :
    - Préfixe O: obligatoire (ex: O:SPY260717C00720000)
    - Pas de last_quote (bid/ask) — seulement day.close comme prix
    - Pas de underlying_asset.price — appel séparé nécessaire
    """
    polygon_ticker = contract_symbol if contract_symbol.startswith("O:") else f"O:{contract_symbol}"
    url = f"{POLYGON_BASE}/v3/snapshot/options/{underlying}/{polygon_ticker}"
    try:
        resp = requests.get(url, params={"apiKey": API_KEY}, timeout=15)
        if resp.status_code != 200:
            logger.warning("Snapshot %s -> %s : %s", polygon_ticker, resp.status_code, resp.text[:120])
            return None
        results = resp.json().get("results", {})
        return results if results else None
    except Exception as exc:
        logger.warning("Snapshot %s failed: %s", polygon_ticker, exc)
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

    # Récupérer le spot une fois par sous-jacent
    underlyings = {combo["symbol"] for combo in combos}
    spot_prices: dict[str, float | None] = {
        sym: get_underlying_price(sym) for sym in underlyings
    }

    rows: list[tuple] = []
    for combo in combos:
        underlying = combo["symbol"]
        combo_id   = combo["id"]
        spot       = spot_prices.get(underlying)

        for leg in combo["legs"]:
            snap = get_snapshot(underlying, leg["contract_symbol"])
            if snap is None:
                continue

            # Free tier : pas de bid/ask — on utilise day.close comme mid
            day = snap.get("day", {})
            mid = day.get("close")

            rows.append((
                timestamp, combo_id, leg["contract_symbol"],
                None,   # bid — indisponible free tier
                None,   # ask — indisponible free tier
                mid,
                spot,
                snap.get("implied_volatility"),
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
