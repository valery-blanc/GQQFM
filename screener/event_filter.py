"""
Étape 4 du pipeline screener : filtre événements micro (earnings, ex-div).
Les ETFs passent toujours ce filtre (pas d'earnings).
"""

from __future__ import annotations

import logging
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


def filter_by_events(
    symbols: list[str],
    near_max_days: int,
    earnings_buffer: int = config.SCREENER_EARNINGS_BUFFER_DAYS,
) -> tuple[list[str], dict[str, date | None], dict[str, date | None]]:
    """
    Élimine les tickers avec earnings dans [today, near_max + buffer].
    Les ETFs passent toujours.

    Retourne:
        passed          : tickers retenus
        earnings_dates  : {symbol: next_earnings_date | None}
        ex_div_dates    : {symbol: next_ex_div_date | None}
    """
    cutoff = date.today() + timedelta(days=near_max_days + earnings_buffer)
    passed: list[str] = []
    earnings_dates: dict[str, date | None] = {}
    ex_div_dates: dict[str, date | None] = {}

    for sym in symbols:
        # Les ETFs n'ont pas d'earnings → toujours retenus
        if sym in ETFS:
            passed.append(sym)
            earnings_dates[sym] = None
            ex_div_dates[sym] = get_ex_div_date(sym)
            continue

        ed = get_earnings_date(sym)
        xd = get_ex_div_date(sym)
        earnings_dates[sym] = ed
        ex_div_dates[sym] = xd

        if ed is not None and date.today() <= ed <= cutoff:
            logger.debug("Éliminé %s : earnings le %s (trop proche)", sym, ed)
            continue

        passed.append(sym)

    logger.info(
        "Filtre événements : %d/%d tickers retenus",
        len(passed), len(symbols),
    )
    return passed, earnings_dates, ex_div_dates
