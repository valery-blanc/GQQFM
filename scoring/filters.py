"""Filtrage GPU des combinaisons selon les critères de scoring."""

import math

import config
from data.models import ScoringCriteria
from engine.backend import xp
from scoring.probability import compute_loss_probability


def realistic_max_gain(
    pnl_mid: "xp.ndarray",   # (C, M)
    spot_range: "xp.ndarray",
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
) -> "xp.ndarray":
    """
    Gain max dans le range ±1σ de mouvement attendu (loi log-normale).
    range = atm_vol × √(days_to_close / 365)

    Ex: SPY IV=15%, DTE=14j → ±2.9% | TSLA IV=60%, DTE=14j → ±11.8%

    Si aucun spot ne tombe dans le range (DTE très court), fallback sur max absolu.
    """
    T = max(days_to_close, 1) / 365.0
    half_range = atm_vol * math.sqrt(T)
    lo = current_spot * (1.0 - half_range)
    hi = current_spot * (1.0 + half_range)
    mask = (spot_range >= lo) & (spot_range <= hi)
    if not mask.any():
        return pnl_mid.max(axis=1)
    return pnl_mid[:, mask].max(axis=1)


def filter_combinations(
    pnl_tensor: "xp.ndarray",    # shape (V, C, M)
    spot_range: "xp.ndarray",    # shape (M,)
    net_debits: "xp.ndarray",    # shape (C,)
    avg_volumes: "xp.ndarray",   # shape (C,)
    criteria: ScoringCriteria,
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
) -> "xp.ndarray":
    """
    Filtre les combinaisons satisfaisant tous les critères.

    Toutes les opérations sont effectuées sur GPU (ou CPU si pas de GPU).
    Retourne un array d'indices des combinaisons valides.
    """
    # Scénario médian : index VOL_MEDIAN_INDEX (toujours 1 = vol × 1.0)
    pnl_mid = pnl_tensor[config.VOL_MEDIAN_INDEX]   # (C, M)

    safe_debits = xp.where(net_debits == 0, xp.ones_like(net_debits) * 1e-6, net_debits)

    max_loss_abs = pnl_mid.min(axis=1)    # (C,) — valeurs négatives
    max_gain_abs = pnl_mid.max(axis=1)    # (C,) — valeurs positives
    # Gain réaliste : max P&L dans le range ±1σ de mouvement attendu
    max_gain_real = realistic_max_gain(pnl_mid, spot_range, current_spot, atm_vol, days_to_close)

    max_loss_pct = max_loss_abs / safe_debits * 100.0
    max_gain_real_pct = max_gain_real / safe_debits * 100.0

    loss_prob = compute_loss_probability(
        pnl_mid, spot_range, current_spot, atm_vol, days_to_close, risk_free_rate
    )

    safe_loss = xp.where(max_loss_abs == 0, xp.ones_like(max_loss_abs) * -1e-6, max_loss_abs)
    gain_loss_ratio_real = max_gain_real / xp.abs(safe_loss)

    mask = (
        (max_loss_pct >= criteria.max_loss_pct) &
        (loss_prob <= criteria.max_loss_probability_pct / 100.0) &
        (max_gain_real_pct >= criteria.min_max_gain_pct) &
        (gain_loss_ratio_real >= criteria.min_gain_loss_ratio) &
        (net_debits <= criteria.max_net_debit) &
        (avg_volumes >= criteria.min_avg_volume)
    )

    return xp.where(mask)[0]
