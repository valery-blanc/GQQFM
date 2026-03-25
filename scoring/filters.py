"""Filtrage GPU des combinaisons selon les critères de scoring."""

import config
from data.models import ScoringCriteria
from engine.backend import xp
from scoring.probability import compute_loss_probability


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

    # max_loss et max_gain en % du capital
    max_loss_abs = pnl_mid.min(axis=1)    # (C,) — valeurs négatives
    max_gain_abs = pnl_mid.max(axis=1)    # (C,) — valeurs positives

    max_loss_pct = max_loss_abs / safe_debits * 100.0
    max_gain_pct = max_gain_abs / safe_debits * 100.0

    # Probabilité de perte
    loss_prob = compute_loss_probability(
        pnl_mid, spot_range, current_spot, atm_vol, days_to_close, risk_free_rate
    )

    # Ratio gain/perte
    safe_loss = xp.where(max_loss_abs == 0, xp.ones_like(max_loss_abs) * -1e-6, max_loss_abs)
    gain_loss_ratio = max_gain_abs / xp.abs(safe_loss)

    mask = (
        (max_loss_pct >= criteria.max_loss_pct) &
        (loss_prob <= criteria.max_loss_probability_pct / 100.0) &
        (max_gain_pct >= criteria.min_max_gain_pct) &
        (gain_loss_ratio >= criteria.min_gain_loss_ratio) &
        (net_debits <= criteria.max_net_debit) &
        (avg_volumes >= criteria.min_avg_volume)
    )

    return xp.where(mask)[0]
