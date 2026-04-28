"""API REST FastAPI exposant les prix collectés et le P&L calculé."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="GQQFM Tracker API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR    = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH     = DATA_DIR / "tracker.db"
REPO_DIR    = Path(os.environ.get("REPO_DIR", "/repo"))
COMBOS_PATH = REPO_DIR / "data" / "tracked_combos.json"


def _combos() -> list[dict]:
    if not COMBOS_PATH.exists():
        return []
    return json.loads(COMBOS_PATH.read_text()).get("combos", [])


def _find_combo(combo_id: str) -> dict | None:
    return next((c for c in _combos() if c["id"] == combo_id), None)


def _db_rows(combo_id: str) -> list[tuple]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT timestamp, leg_symbol, bid, ask, mid, underlying_spot, iv "
            "FROM prices WHERE combo_id = ? ORDER BY timestamp",
            (combo_id,),
        ).fetchall()


@app.get("/health")
def health():
    n_combos = len(_combos())
    n_rows = 0
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            n_rows = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    return {"status": "ok", "combos": n_combos, "total_price_rows": n_rows}


@app.get("/combos")
def list_combos():
    combos = _combos()
    # Enrichit avec le nombre de mesures en base
    result = []
    for c in combos:
        n = 0
        if DB_PATH.exists():
            with sqlite3.connect(DB_PATH) as conn:
                n = conn.execute(
                    "SELECT COUNT(DISTINCT timestamp) FROM prices WHERE combo_id=?",
                    (c["id"],)
                ).fetchone()[0]
        result.append({**c, "n_snapshots": n})
    return result


@app.get("/prices/{combo_id}")
def get_prices(combo_id: str):
    """Toutes les mesures brutes d'un combo (une ligne par leg par timestamp)."""
    rows = _db_rows(combo_id)
    return [
        {"timestamp": r[0], "leg_symbol": r[1], "bid": r[2],
         "ask": r[3], "mid": r[4], "spot": r[5], "iv": r[6]}
        for r in rows
    ]


@app.get("/pnl/{combo_id}")
def get_pnl(combo_id: str):
    """
    Série temporelle du P&L réel calculé depuis les prix collectés.
    P&L = Σ direction × qty × (current_mid − entry_price) × 100
    Retourne aussi pnl_real_bid_ask = P&L si on clôture au bid/ask réel
    (plus réaliste : on vend au bid, on rachète à l'ask).
    """
    combo = _find_combo(combo_id)
    if combo is None:
        raise HTTPException(404, "combo_id inconnu")

    rows = _db_rows(combo_id)
    if not rows:
        return []

    # Indexer les legs par symbole
    leg_by_sym = {leg["contract_symbol"]: leg for leg in combo["legs"]}
    net_debit  = combo.get("net_debit") or 1e-6

    # Grouper par timestamp
    by_ts: dict[str, dict[str, dict]] = defaultdict(dict)
    for ts, leg_sym, bid, ask, mid, spot, iv in rows:
        by_ts[ts][leg_sym] = {"bid": bid, "ask": ask, "mid": mid, "spot": spot, "iv": iv}

    result = []
    for ts in sorted(by_ts):
        leg_prices = by_ts[ts]
        if not all(sym in leg_prices for sym in leg_by_sym):
            continue  # snapshot incomplet ce créneau

        pnl_mid = 0.0
        pnl_exec = 0.0  # prix d'exécution réaliste (bid si vente, ask si achat)
        spot = None

        for sym, leg in leg_by_sym.items():
            p = leg_prices[sym]
            if spot is None:
                spot = p.get("spot")
            mid = p.get("mid") or 0.0
            bid = p.get("bid") or mid
            ask = p.get("ask") or mid
            entry = leg["entry_price"]
            d, q = leg["direction"], leg["quantity"]

            pnl_mid  += d * q * (mid - entry) * 100
            # Exécution : si direction=+1 (long) on VEND → bid ; si -1 (short) on RACHÈTE → ask
            exec_price = bid if d > 0 else ask
            pnl_exec += d * q * (exec_price - entry) * 100

        result.append({
            "timestamp":    ts,
            "pnl_dollar":   round(pnl_mid,  2),
            "pnl_pct":      round(pnl_mid  / abs(net_debit) * 100, 2),
            "pnl_exec_dollar": round(pnl_exec, 2),
            "pnl_exec_pct":    round(pnl_exec / abs(net_debit) * 100, 2),
            "spot":         spot,
        })

    return result
