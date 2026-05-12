"""Helpers HV30 et facteur de régime — FEAT-030-B + 030-C.

Centralise les calculs de HV30 / percentiles / regime factor, partagés
entre `validate_ranking.py` (FEAT-029), `data/provider_yfinance.py`
(FEAT-030-C) et les pipelines de scan live / backtest (FEAT-030-B).

Pas de duplication avec `screener/options_analyzer.py:compute_hv30()` —
les deux coexistent : le screener fetch lui-même via yfinance, ici on
travaille à partir de closes / bars déjà fetchés.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

import config


def compute_hv30_from_closes(closes: np.ndarray, win: int = 21) -> float:
    """HV annualisée sur `win` jours de trading (~30 calendaires).

    Args:
        closes: array 1D des prix de clôture (ordre chronologique).
        win: fenêtre rolling (21 jours de trading par défaut).

    Returns:
        Float HV annualisée (ex: 0.18 = 18%). 0.0 si données insuffisantes.
    """
    closes = np.asarray(closes, dtype=np.float64)
    closes = closes[closes > 0]
    if len(closes) < win + 1:
        return 0.0
    log_ret = np.diff(np.log(closes))
    if len(log_ret) < win:
        return 0.0
    hv = float(log_ret[-win:].std() * math.sqrt(252))
    return hv if np.isfinite(hv) and hv > 0 else 0.0


def compute_hv30_from_bars(
    bars: dict,
    as_of: date,
    win: int = 21,
) -> float:
    """Wrapper sur compute_hv30_from_closes pour les bars Polygon
    (format `{date: (close, volume)}`). Filtre les dates ≤ as_of."""
    sorted_items = sorted(
        (d, c) for d, (c, _) in bars.items() if d <= as_of and c > 0
    )
    closes = np.array([c for _, c in sorted_items], dtype=np.float64)
    return compute_hv30_from_closes(closes, win=win)


def compute_hv30_percentiles(
    closes: np.ndarray,
    win: int = 21,
    lookback: int = 90,
) -> tuple[float, float, float] | None:
    """Calcule (p10, current, p90) de la HV30 rolling sur les `lookback`
    derniers jours.

    Returns:
        Tuple (p10, current_hv, p90) ou None si données insuffisantes
        (< 30 points HV calculables, ou current_hv ≤ 0).

    Note: `lookback` est en jours-calendrier d'historique nécessaire ; on
    a besoin d'au moins `win + lookback` jours de closes valides pour
    calculer `lookback` HVs rollings.
    """
    closes = np.asarray(closes, dtype=np.float64)
    closes = closes[closes > 0]
    if len(closes) < win + 30:
        return None
    log_ret = np.diff(np.log(closes))
    if len(log_ret) < win + 30:
        return None
    hv_series = np.array([
        log_ret[i - win:i].std() * math.sqrt(252)
        for i in range(win, len(log_ret) + 1)
    ])
    hv_series = hv_series[np.isfinite(hv_series) & (hv_series > 0)]
    if len(hv_series) < 30:
        return None
    return (
        float(np.percentile(hv_series, 10)),
        float(hv_series[-1]),
        float(np.percentile(hv_series, 90)),
    )


def compute_regime_factor(hv30: float, iv_atm: float) -> float:
    """Multiplicateur scalaire du score selon HV30/IV_ATM.

    Mapping via `config.REGIME_HV_IV_THRESHOLDS` :
      hv/iv < 0.60  → 1.05 (vol chère, bon pour calendars)
      < 0.85        → 1.00 (régime normal)
      < 1.00        → 0.80 (marché trending)
      ≥ 1.00        → 0.55 (trend fort, calendars KO)

    Retourne 1.0 (neutre) si données insuffisantes (hv ou iv ≤ 0).
    """
    if hv30 <= 0 or iv_atm <= 0:
        return 1.0
    ratio = hv30 / iv_atm
    for threshold, factor in config.REGIME_HV_IV_THRESHOLDS:
        if ratio < threshold:
            return factor
    # ratio >= dernier seuil
    return config.REGIME_HV_IV_THRESHOLDS[-1][1]
