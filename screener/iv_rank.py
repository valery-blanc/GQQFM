"""
IV Rank approximé sur 252 jours (FEAT-023 § Étape 3).

yfinance ne fournit pas l'historique de l'IV ATM. Approximation : reconstruire
une série IV historique en partant de la HV20 sliding et en appliquant un
facteur d'ajustement constant `IV/HV` calibré sur le ratio actuel. C'est
imparfait mais bien meilleur que `IV/HV30` instantané pour évaluer la position
relative de l'IV courante.

Limites :
- Ne capture pas les chocs IV idiosyncratiques (events spécifiques).
- Sous-estime IV en période de stress (HV "rattrape" toujours en retard).
- Calibrer sur 1 an minimum ; tickers récents (IPO < 1 an) → fallback proxy.

Pour un vrai IV Rank, il faudra une source payante (Polygon, Tradier, ORATS).
À documenter comme évolution V3 (FEAT-024 future).
"""

from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)


def compute_iv_rank_52w_from_history(
    closes,
    current_iv: float,
    current_hv30: float,
    window: int = 21,
    history_min: int = 200,
) -> float:
    """
    IV Rank approximé sur ~252 jours.

    Args:
        closes        : pd.Series des cours de clôture sur ≥ 252 jours
        current_iv    : IV ATM courante (réelle, depuis yfinance)
        current_hv30  : HV30 courante (calculée depuis closes)
        window        : taille de la fenêtre HV (21 jours = ~30j calendrier)
        history_min   : minimum de jours nécessaires pour calculer le rank

    Returns:
        IV Rank 0-100 ou 50.0 (neutre) si données insuffisantes.

    Méthode : pour chaque jour t dans l'historique, calculer HV(t,t-window).
    Multiplier par `current_iv / current_hv30` pour obtenir une IV "estimée"
    historique. Calculer le rank percentile de current_iv parmi cette série.
    """
    try:
        import pandas as pd

        if closes is None or len(closes) < history_min:
            return 50.0
        if current_iv <= 0 or current_hv30 <= 0:
            return 50.0

        log_ret = np.log(closes / closes.shift(1)).dropna()
        if len(log_ret) < history_min:
            return 50.0

        # HV sliding sur `window` jours, annualisée
        rolling_std = log_ret.rolling(window=window).std()
        hv_series = rolling_std * math.sqrt(252)
        hv_series = hv_series.dropna()
        if len(hv_series) < history_min - window:
            return 50.0

        # Reconstruction IV historique : HV × (current_iv / current_hv30)
        adjustment = current_iv / current_hv30
        iv_estimated = hv_series * adjustment

        iv_min = float(iv_estimated.min())
        iv_max = float(iv_estimated.max())
        if iv_max <= iv_min:
            return 50.0

        # Position de current_iv dans le range
        rank = (current_iv - iv_min) / (iv_max - iv_min) * 100
        return float(max(0.0, min(100.0, rank)))
    except Exception as exc:
        logger.debug("compute_iv_rank_52w : %s", exc)
        return 50.0


def batch_compute_iv_rank_52w(
    symbols: list[str],
    current_iv_map: dict[str, float],
    current_hv30_map: dict[str, float],
) -> dict[str, float]:
    """
    Calcule l'IV Rank 52w pour une liste de symboles depuis 1 an d'historique.
    Un seul appel yfinance batch.
    """
    import yfinance as yf
    if not symbols:
        return {}

    try:
        data = yf.download(
            symbols, period="1y", interval="1d",
            progress=False, auto_adjust=True,
        )
    except Exception as exc:
        logger.warning("batch IV Rank download failed : %s", exc)
        return {sym: 50.0 for sym in symbols}

    result: dict[str, float] = {}
    for sym in symbols:
        try:
            if len(symbols) == 1:
                closes = data["Close"].dropna()
            else:
                closes = data["Close"][sym].dropna()
            iv = current_iv_map.get(sym, 0.0)
            hv = current_hv30_map.get(sym, 0.0)
            result[sym] = compute_iv_rank_52w_from_history(closes, iv, hv)
        except Exception as exc:
            logger.debug("IV Rank %s : %s", sym, exc)
            result[sym] = 50.0
    return result
