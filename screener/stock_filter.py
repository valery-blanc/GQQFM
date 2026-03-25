"""
Étape 2 du pipeline screener : filtre stock rapide.
Un seul appel yfinance batch pour tous les tickers (~5s).
Élimine les sous-jacents trop peu liquides ou trop bon marché.
"""

from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)


def fast_filter_stocks(
    symbols: list[str],
    min_price: float = config.SCREENER_MIN_PRICE,
    min_volume: int = config.SCREENER_MIN_AVG_VOLUME,
) -> tuple[list[str], dict[str, float]]:
    """
    Filtre rapide sur prix et volume via yfinance batch download.

    Retourne:
        passed   : liste de tickers qui passent le filtre
        prices   : dict {symbol: last_close} pour les tickers retenus
    """
    import yfinance as yf

    if not symbols:
        return [], {}

    try:
        data = yf.download(
            symbols,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as exc:
        logger.error("Erreur yfinance batch download : %s", exc)
        return [], {}

    # yfinance retourne MultiIndex quand plusieurs tickers
    if len(symbols) == 1:
        closes = data["Close"]
        volumes = data["Volume"]
        sym = symbols[0]
        try:
            price = float(closes.dropna().iloc[-1])
            avg_vol = float(volumes.dropna().mean())
            if price >= min_price and avg_vol >= min_volume:
                return [sym], {sym: price}
        except Exception:
            pass
        return [], {}

    passed: list[str] = []
    prices: dict[str, float] = {}

    for sym in symbols:
        try:
            sym_closes = data["Close"][sym].dropna()
            sym_volumes = data["Volume"][sym].dropna()
            if sym_closes.empty or sym_volumes.empty:
                continue
            price = float(sym_closes.iloc[-1])
            avg_vol = float(sym_volumes.mean())
            if price >= min_price and avg_vol >= min_volume:
                passed.append(sym)
                prices[sym] = price
        except Exception as exc:
            logger.debug("Filtre stock %s ignoré : %s", sym, exc)

    logger.info(
        "Filtre stock : %d/%d tickers retenus (prix≥$%.0f, vol≥%d/j)",
        len(passed), len(symbols), min_price, min_volume,
    )
    return passed, prices
