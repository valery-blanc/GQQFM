"""
Vrai IV Rank 52w via Polygon historique (FEAT-024).

Approche :
1. Sample weekly (52 dates/an) sur les ATM calls ~30 DTE
2. Pour chaque date : récupère le spot, trouve le strike ATM, fetch le close
   du contrat, inverse l'IV via Black-Scholes
3. Cache local en parquet, refresh incrémental
4. IV Rank = position de current_iv dans le min/max de l'historique reconstruit

Fallback automatique sur l'approximation HV-based (FEAT-023) si Polygon
indisponible ou si l'historique a moins de 30 points (premier run, ticker récent).
"""

from __future__ import annotations

import logging
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, wait as _wait, FIRST_COMPLETED
from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "data",
    "iv_history_cache.parquet",
)


# ── cache local parquet ──────────────────────────────────────────────────────


def _load_cache() -> pd.DataFrame:
    """Charge le cache. Retourne un DataFrame vide si fichier absent ou corrompu."""
    if not os.path.exists(CACHE_PATH):
        return pd.DataFrame(columns=["symbol", "sample_date", "iv_atm", "dte", "strike", "contract_ticker"])
    try:
        df = pd.read_parquet(CACHE_PATH)
        df["sample_date"] = pd.to_datetime(df["sample_date"]).dt.date
        return df
    except Exception as exc:
        logger.warning("IV cache corrompu (%s) — reset", exc)
        return pd.DataFrame(columns=["symbol", "sample_date", "iv_atm", "dte", "strike", "contract_ticker"])


def _save_cache(df: pd.DataFrame) -> None:
    """Sauve le cache en parquet (atomic via tmp)."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, CACHE_PATH)
    except Exception as exc:
        logger.warning("IV cache write failed : %s", exc)


# ── sampling dates ───────────────────────────────────────────────────────────


def _sample_dates(weeks_back: int, cadence_days: int, today: date) -> list[date]:
    """Génère les dates d'échantillon, en évitant les week-ends (recule vendredi)."""
    n = max(1, (weeks_back * 7) // cadence_days)
    dates = []
    for i in range(n):
        d = today - timedelta(days=cadence_days * i)
        # Évite week-end : reculer au vendredi
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        dates.append(d)
    return list(reversed(dates))   # ordre chronologique


# ── fetch IV ATM à une date donnée ───────────────────────────────────────────


def _fetch_iv_atm_at_date(
    symbol: str,
    sample_date: date,
    polygon,
    target_dte: int = 30,
    dte_window: int = 7,
    rfr: float | None = None,
) -> dict | None:
    """
    Pour une date d'échantillon donnée, récupère l'IV ATM en :
    1. Récupérant le spot à cette date
    2. Listant les contrats call expirant dans [date+target_dte-window, date+target_dte+window]
    3. Choisissant le strike le plus proche du spot
    4. Fetching le close de ce contrat à sample_date
    5. Inversant l'IV via Black-Scholes

    rfr : taux sans risque pré-calculé (évite appel yfinance dans le thread).
          Si None, utilise config.DEFAULT_RISK_FREE_RATE.

    Retourne un dict {symbol, sample_date, iv_atm, dte, strike, contract_ticker}
    ou None si données indisponibles.
    """
    from data.provider_yfinance import _implied_vol

    try:
        spot = polygon.get_underlying_close(symbol, sample_date)
        if spot <= 0:
            return None

        expiry_min = sample_date + timedelta(days=target_dte - dte_window)
        expiry_max = sample_date + timedelta(days=target_dte + dte_window)
        contracts = polygon.list_contracts(symbol, sample_date, expiry_min, expiry_max)
        # Filtre : calls uniquement, strike numérique
        calls = [c for c in contracts if c.get("contract_type") == "call"]
        if not calls:
            return None

        # Essayer les 3 strikes ATM les plus proches sur la même date.
        # Pas de retry sur dates adjacentes : chaque delta supplémentaire
        # génère de nouveaux appels HTTP en cache-miss sur les runs suivants,
        # ce qui multiplie la charge et provoque des blocages réseau (BUG-030 bis).
        calls_with_strike = [(c, abs(float(c.get("strike_price", 0)) - spot)) for c in calls]
        calls_with_strike.sort(key=lambda x: x[1])

        rate = rfr if rfr is not None else config.DEFAULT_RISK_FREE_RATE

        for atm_call, _ in calls_with_strike[:3]:
            strike = float(atm_call["strike_price"])
            contract_ticker = atm_call["ticker"]
            expiry = date.fromisoformat(atm_call["expiration_date"])
            dte = (expiry - sample_date).days

            bar = polygon.get_contract_close(contract_ticker, sample_date)
            if bar is None:
                continue
            price, _ = bar
            if price <= 0:
                continue
            tte = max(dte / 365.0, 1 / 365.0)
            iv = _implied_vol("call", price, spot, strike, tte, rate)
            if 0.01 < iv < 3.0:
                return {
                    "symbol": symbol,
                    "sample_date": sample_date,
                    "iv_atm": iv,
                    "dte": dte,
                    "strike": strike,
                    "contract_ticker": contract_ticker,
                }

        return None
    except Exception as exc:
        logger.debug("IV history %s @ %s : %s", symbol, sample_date, exc)
        return None


# ── batch fetch + cache ──────────────────────────────────────────────────────


def fetch_or_load_iv_history(
    symbols: Iterable[str],
    polygon,
    weeks_back: int = 52,
    cadence_days: int = 7,
    target_dte: int = 30,
    today: date | None = None,
    progress_callback=None,
) -> dict[str, list[tuple[date, float]]]:
    """
    Pour chaque symbole, retourne l'historique IV ATM (date, iv) sur weeks_back.
    Charge depuis cache local et fetch incrémental les dates manquantes.

    Args:
        progress_callback : fonction(pct: float, msg: str) appelée pendant fetch.
    """
    today = today or date.today()
    sample_dates = _sample_dates(weeks_back, cadence_days, today)
    cache = _load_cache()

    # Identifier les couples (symbol, date) déjà cachés
    cached_keys = set(zip(cache["symbol"], cache["sample_date"])) if not cache.empty else set()

    # Fetch les manquants — parallèle pour accélérer le premier run
    new_rows: list[dict] = []
    symbols_list = list(symbols)
    to_fetch = [
        (sym, d)
        for sym in symbols_list for d in sample_dates
        if (sym, d) not in cached_keys
    ]
    total = len(to_fetch)
    fetched_count = 0

    # RFR : un seul appel live pour toute la période — évite 52+ appels yfinance
    # dans les workers (source de blocage sur Windows). Impact IV Rank < 1pt.
    shared_rfr: float = config.DEFAULT_RISK_FREE_RATE
    if to_fetch:
        try:
            from data.risk_free_rate import fetch_risk_free_rate
            shared_rfr, _ = fetch_risk_free_rate()
            logger.info("IV Rank : RFR live = %.3f (partagé pour toutes les dates)", shared_rfr)
        except Exception:
            logger.debug("IV Rank : RFR live indisponible, fallback %.3f", shared_rfr)

    if total > 0:
        # Utilise wait() au lieu de as_completed() pour détecter les workers bloqués.
        # Sur Windows, requests.get(timeout=X) ne se déclenche pas fiablement sur
        # les connexions TCP half-open → as_completed() se bloque indéfiniment.
        # Si aucun futur ne complète en 15s, on abandonne les workers restants
        # (BUG-030 quater) et on continue avec les données déjà cachées.
        #
        # Les paires qui ont terminé (succès ou échec confirmé) sont toutes sauvées
        # en parquet — y compris les échecs (iv_atm=NaN) — pour ne plus les re-tenter
        # au prochain run. Seules les paires abandonnées (timeout) sont re-tentées.
        executor = ThreadPoolExecutor(max_workers=20)
        try:
            fut_map = {
                executor.submit(
                    _fetch_iv_atm_at_date, sym, d, polygon, target_dte, 7, shared_rfr,
                ): (sym, d)
                for sym, d in to_fetch
            }
            # Checkpoint fréquent : toutes les 20 rows pour limiter la perte en cas de crash
            save_threshold = 20
            # Budget temps total : évite de bloquer le screener > 2 min sur les HTTP calls.
            # Les paires abandonnées seront retentées au prochain run (pas mises en NaN).
            MAX_TOTAL_SECONDS = 120
            pending = set(fut_map)
            start_ts = _time.monotonic()
            last_progress_ts = start_ts
            # Paires dont le futur a terminé (succès OU échec confirmé)
            completed_pairs: set[tuple[str, date]] = set()

            while pending:
                elapsed = _time.monotonic() - start_ts
                if elapsed >= MAX_TOTAL_SECONDS:
                    logger.warning(
                        "IV history : budget temps %.0fs épuisé — %d paires abandonnées",
                        elapsed, len(pending),
                    )
                    break

                remaining = MAX_TOTAL_SECONDS - elapsed
                done, pending = _wait(pending, timeout=min(12, remaining), return_when=FIRST_COMPLETED)

                if not done:
                    # Aucun résultat dans le délai → workers bloqués
                    idle_s = _time.monotonic() - last_progress_ts
                    logger.warning(
                        "IV history : aucun résultat depuis %.0fs — "
                        "%d futurs abandonnés (TCP half-open Windows)",
                        idle_s, len(pending),
                    )
                    break

                last_progress_ts = _time.monotonic()
                batch_rows: list[dict] = []
                for future in done:
                    sym, d = fut_map[future]
                    fetched_count += 1
                    completed_pairs.add((sym, d))
                    try:
                        row = future.result()
                    except Exception as exc:
                        logger.debug("IV history future %s @ %s : %s", sym, d, exc)
                        row = None

                    if row is not None:
                        batch_rows.append(row)
                    else:
                        # Échec confirmé : cacher NaN pour ne plus re-tenter
                        batch_rows.append({
                            "symbol": sym,
                            "sample_date": d,
                            "iv_atm": float("nan"),
                            "dte": 0,
                            "strike": 0.0,
                            "contract_ticker": "",
                        })

                    if progress_callback:
                        pct = fetched_count / total
                        progress_callback(pct, f"IV history {fetched_count}/{total}")

                new_rows.extend(batch_rows)
                # Checkpoint toutes les save_threshold rows
                if len(new_rows) >= save_threshold:
                    cache_to_save = pd.concat(
                        [cache, pd.DataFrame(new_rows)], ignore_index=True
                    ).drop_duplicates(subset=["symbol", "sample_date"], keep="last")
                    _save_cache(cache_to_save)
                    cache = cache_to_save
                    new_rows = []
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # Sauvegarde finale (rows restants non encore flushés)
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        cache = pd.concat([cache, new_df], ignore_index=True)
        cache = cache.drop_duplicates(subset=["symbol", "sample_date"], keep="last")
        _save_cache(cache)
    valid_count = int(cache["iv_atm"].notna().sum()) if not cache.empty else 0
    logger.info(
        "IV history cache : %d lignes totales dont %d IV valides",
        len(cache), valid_count,
    )

    # Construit le dict de retour : symbol → [(date, iv), ...]
    # Les lignes NaN (échecs cachés) sont exclues du résultat.
    result: dict[str, list[tuple[date, float]]] = {}
    if cache.empty:
        return {sym: [] for sym in symbols_list}

    cache_dates = set(sample_dates)
    for sym in symbols_list:
        sym_df = cache[
            (cache["symbol"] == sym)
            & (cache["sample_date"].isin(cache_dates))
            & cache["iv_atm"].notna()
            & (cache["iv_atm"] > 0)
        ]
        if sym_df.empty:
            result[sym] = []
            continue
        sym_df = sym_df.sort_values("sample_date")
        result[sym] = list(zip(sym_df["sample_date"].tolist(), sym_df["iv_atm"].astype(float).tolist()))

    return result


# ── compute IV Rank ──────────────────────────────────────────────────────────


def compute_iv_rank_from_history(
    iv_history: list[tuple[date, float]],
    current_iv: float,
    min_points: int = 10,
) -> float:
    """IV Rank = position de current_iv dans [min_history, max_history]."""
    if len(iv_history) < min_points or current_iv <= 0:
        return 50.0
    ivs = [iv for _, iv in iv_history if iv > 0]
    if len(ivs) < min_points:
        return 50.0
    iv_min = min(ivs)
    iv_max = max(ivs)
    if iv_max <= iv_min:
        return 50.0
    rank = (current_iv - iv_min) / (iv_max - iv_min) * 100
    return float(max(0.0, min(100.0, rank)))


def batch_compute_iv_rank_polygon(
    symbols: list[str],
    current_iv_map: dict[str, float],
    polygon,
    progress_callback=None,
) -> dict[str, float]:
    """
    Calcule un vrai IV Rank 52w pour tous les symboles en utilisant Polygon.
    Cache local, refresh incrémental.
    Retourne {symbol: rank 0-100}.
    """
    history = fetch_or_load_iv_history(
        symbols, polygon, progress_callback=progress_callback
    )
    return {
        sym: compute_iv_rank_from_history(history.get(sym, []), current_iv_map.get(sym, 0.0))
        for sym in symbols
    }
