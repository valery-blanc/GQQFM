"""
Étape 4 du pipeline screener : filtre événements micro (earnings, ex-div).
Les ETFs passent toujours ce filtre (pas d'earnings).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import config
from screener.universe import ETFS

logger = logging.getLogger(__name__)


def get_earnings_date(symbol: str) -> date | None:
    """Récupère la prochaine date de publication des résultats via yfinance."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None:
            return None
        earnings = cal.get("Earnings Date")
        if earnings is None:
            return None
        # Peut être une liste de dates ou une date unique
        if hasattr(earnings, "__iter__") and not isinstance(earnings, str):
            candidates = [e.date() if hasattr(e, "date") else e for e in earnings]
            future = [d for d in candidates if d >= date.today()]
            return min(future) if future else None
        if hasattr(earnings, "date"):
            return earnings.date()
        return None
    except Exception as exc:
        logger.debug("Earnings date %s : %s", symbol, exc)
        return None


def get_ex_div_date(symbol: str) -> date | None:
    """Récupère la prochaine date ex-dividende via yfinance."""
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        ex_div = info.get("exDividendDate")
        if ex_div is None:
            return None
        if isinstance(ex_div, (int, float)):
            d = date.fromtimestamp(int(ex_div))
        elif hasattr(ex_div, "date"):
            d = ex_div.date()
        else:
            return None
        return d if d >= date.today() else None
    except Exception as exc:
        logger.debug("Ex-div date %s : %s", symbol, exc)
        return None


def _fetch_events(sym: str) -> tuple[str, date | None, date | None]:
    """Récupère earnings + ex-div pour un ticker (appelé en parallèle)."""
    if sym in ETFS:
        return sym, None, get_ex_div_date(sym)
    return sym, get_earnings_date(sym), get_ex_div_date(sym)


def filter_by_events(
    symbols: list[str],
    near_max_days: int,
    earnings_buffer: int = config.SCREENER_EARNINGS_BUFFER_DAYS,
) -> tuple[list[str], dict[str, date | None], dict[str, date | None]]:
    """
    Élimine les tickers avec earnings dans [today, near_max + buffer].
    Les ETFs passent toujours.
    Les requêtes yfinance sont parallélisées via ThreadPoolExecutor.

    Retourne:
        passed          : tickers retenus
        earnings_dates  : {symbol: next_earnings_date | None}
        ex_div_dates    : {symbol: next_ex_div_date | None}
    """
    cutoff = date.today() + timedelta(days=near_max_days + earnings_buffer)
    earnings_dates: dict[str, date | None] = {}
    ex_div_dates: dict[str, date | None] = {}

    # Fetch parallèle (chaque thread respecte son propre rate-limit yfinance)
    with ThreadPoolExecutor(max_workers=config.SCREENER_MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_events, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, ed, xd = future.result()
            earnings_dates[sym] = ed
            ex_div_dates[sym] = xd

    # Reconstruction dans l'ordre original + filtrage earnings
    passed: list[str] = []
    for sym in symbols:
        ed = earnings_dates[sym]
        if sym not in ETFS and ed is not None and date.today() <= ed <= cutoff:
            logger.debug("Éliminé %s : earnings le %s (trop proche)", sym, ed)
            continue
        passed.append(sym)

    logger.info(
        "Filtre événements : %d/%d tickers retenus",
        len(passed), len(symbols),
    )
    return passed, earnings_dates, ex_div_dates
