"""Calcul centralisé des métriques per-combo pour le scoring v2 (FEAT-026 + 026b).

Sept métriques sont calculées pour chaque combo filtré, puis combinées dans
`scoring/scorer.py:score_combinations()` selon les poids `ScoreWeights` choisis
par l'utilisateur.

**FEAT-026b** : tous les pourcentages sont calculés sur `capital_required` =
`max(|net_debit|, |max_loss|)` plutôt que sur `net_debit`. Raison : le net_debit
sous-estime le capital effectivement immobilisé pour les structures avec shorts
non couverts (calendar/double calendar) — le broker exige une marge ≥ max_loss.

Métriques :
  1. max_loss_pct          — perte max / capital_required × 100
  2. max_gain_real_pct     — gain max dans la fenêtre ±1σ / capital_required × 100
  3. annualized_return_pct — max_gain_real_pct × 365 / days_to_close
  4. loss_prob             — probabilité de perte (lognormale, globale)
  5. liquidity_score       — min(volume × open_interest) sur les legs
  6. vol_dispersion_pct    — dispersion P&L à spot=courant entre les V scénarios
                             de vol, en % du capital_required (plus bas = plus robuste)
  7. slippage_pct          — Σ((ask−bid) × qty × 100) / capital_required
                             NaN si bid/ask manquants pour au moins une leg
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

import config
from data.models import Combination
from engine.backend import to_cpu, to_xp, xp
from scoring.probability import compute_loss_probability


@dataclass
class ComboMetricsBatch:
    """Métriques per-combo (arrays shape (C,))."""

    max_loss_pct: "xp.ndarray"
    max_gain_real_pct: "xp.ndarray"
    annualized_return_pct: "xp.ndarray"
    loss_prob: "xp.ndarray"
    liquidity_score: "xp.ndarray"
    vol_dispersion_pct: "xp.ndarray"
    slippage_pct: "xp.ndarray"
    days_to_close: "xp.ndarray"

    max_gain_real_dollar: "xp.ndarray"
    max_loss_dollar: "xp.ndarray"
    daily_gain_dollar: "xp.ndarray"
    realistic_range_pct: "xp.ndarray"
    atm_vol_per_combo: "xp.ndarray"
    capital_required: "xp.ndarray"


def compute_combo_metrics(
    combinations: list[Combination],
    pnl_tensor: "xp.ndarray",
    spot_range: "xp.ndarray",
    net_debits: "xp.ndarray",
    current_spot: float,
    today: date,
    risk_free_rate: float,
    atm_vol_global: float,
    days_to_close_global: int,
) -> ComboMetricsBatch:
    """Calcule les sept métriques per-combo.

    Args:
        combinations: liste des combos filtrés (list of length C).
        pnl_tensor: shape (V, C, M).
        spot_range: shape (M,).
        net_debits: shape (C,) — net_debit par combo (en dollars).
        current_spot: spot du sous-jacent au moment du calcul.
        today: date de référence pour days_to_close.
        risk_free_rate: taux sans risque (décimal).
        atm_vol_global: IV ATM globale (médiane) — utilisée pour loss_prob.
        days_to_close_global: jours médians — utilisé pour loss_prob.

    Returns:
        ComboMetricsBatch — toutes les métriques sont des arrays xp shape (C,).
    """
    pnl_mid = pnl_tensor[config.VOL_MEDIAN_INDEX]
    n_combos = pnl_mid.shape[0]

    max_loss = pnl_mid.min(axis=1)

    # FEAT-026b : capital effectivement immobilisé.
    # Pour les calendars/double calendars, les shorts génèrent une marge broker
    # ≥ max_loss, supérieure au net_debit (qui sous-estime le capital bloqué).
    capital_required = xp.maximum(xp.abs(net_debits), xp.abs(max_loss))
    capital_required = xp.where(capital_required < 1.0, xp.ones_like(capital_required),
                                capital_required)

    pnl_mid_cpu = to_cpu(pnl_mid)
    spot_range_cpu = to_cpu(spot_range)
    capital_required_cpu = to_cpu(capital_required)

    max_gain_real_arr = np.empty(n_combos, dtype=np.float32)
    days_arr = np.empty(n_combos, dtype=np.float32)
    liquidity_arr = np.empty(n_combos, dtype=np.float32)
    slippage_arr = np.empty(n_combos, dtype=np.float32)
    range_arr = np.empty(n_combos, dtype=np.float32)
    atm_vol_arr = np.empty(n_combos, dtype=np.float32)

    for i, combo in enumerate(combinations):
        atm_vol_i = min(
            (abs(leg.strike - current_spot), leg.implied_vol)
            for leg in combo.legs
        )[1]
        days_i = max(1, (combo.close_date - today).days)
        atm_vol_arr[i] = atm_vol_i
        days_arr[i] = days_i

        half = atm_vol_i * math.sqrt(days_i / 365.0)
        range_arr[i] = half * 100.0
        lo = current_spot * (1 - half)
        hi = current_spot * (1 + half)
        mask = (spot_range_cpu >= lo) & (spot_range_cpu <= hi)
        if mask.any():
            max_gain_real_arr[i] = float(pnl_mid_cpu[i][mask].max())
        else:
            max_gain_real_arr[i] = float(pnl_mid_cpu[i].max())

        liquidity_arr[i] = float(
            min(leg.volume * leg.open_interest for leg in combo.legs)
        )

        if any(leg.bid is None or leg.ask is None for leg in combo.legs):
            slippage_arr[i] = np.nan
        else:
            spread_dollar = sum(
                (leg.ask - leg.bid) * leg.quantity * 100
                for leg in combo.legs
            )
            denom = float(capital_required_cpu[i])
            slippage_arr[i] = float(spread_dollar / denom * 100.0)

    max_gain_real = to_xp(max_gain_real_arr)
    days_to_close = to_xp(days_arr)
    liquidity = to_xp(liquidity_arr)
    slippage = to_xp(slippage_arr)
    realistic_range = to_xp(range_arr)
    atm_vol_per_combo = to_xp(atm_vol_arr)

    max_loss_pct = max_loss / capital_required * 100.0
    max_gain_real_pct = max_gain_real / capital_required * 100.0
    annualized_return_pct = max_gain_real_pct * (365.0 / xp.maximum(days_to_close, 1.0))
    daily_gain_dollar = max_gain_real / xp.maximum(days_to_close, 1.0)

    loss_prob = compute_loss_probability(
        pnl_mid, spot_range, current_spot,
        atm_vol_global, days_to_close_global, risk_free_rate,
    )

    idx_spot0 = int(xp.argmin(xp.abs(spot_range - current_spot)).item())
    pnl_at_spot0 = pnl_tensor[:, :, idx_spot0]
    pnl_std = pnl_at_spot0.std(axis=0)
    vol_dispersion_pct = pnl_std / capital_required * 100.0

    return ComboMetricsBatch(
        max_loss_pct=max_loss_pct,
        max_gain_real_pct=max_gain_real_pct,
        annualized_return_pct=annualized_return_pct,
        loss_prob=loss_prob,
        liquidity_score=liquidity,
        vol_dispersion_pct=vol_dispersion_pct,
        slippage_pct=slippage,
        days_to_close=days_to_close,
        max_gain_real_dollar=max_gain_real,
        max_loss_dollar=max_loss,
        daily_gain_dollar=daily_gain_dollar,
        realistic_range_pct=realistic_range,
        atm_vol_per_combo=atm_vol_per_combo,
        capital_required=capital_required,
    )
