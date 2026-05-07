"""Score composite v2 (FEAT-026) — classement multi-critères des combinaisons.

Sept composants additifs normalisés min-max sur la population filtrée :
  Score = w1 × norm(max_gain_real_pct)
        + w2 × norm(annualized_return_pct)
        + w3 × (1 − norm(loss_prob))
        + w4 × (1 − norm(|max_loss_pct|))
        + w5 × norm(liquidity_score)
        + w6 × norm(vol_robustness)
        + w7 × (1 − norm(slippage_pct))    # NaN remplacé par médiane

Le multiplicateur événementiel (FEAT-005) reste appliqué en sortie :
  Score_final = Score × event_score_factor
"""

from __future__ import annotations

import config
from engine.backend import xp
from scoring.metrics import ComboMetricsBatch


def _normalize(arr: "xp.ndarray") -> "xp.ndarray":
    """Min-max → [0, 1]. Retourne 0 si toutes valeurs égales (range nul)."""
    mn = arr.min()
    mx = arr.max()
    rng = mx - mn
    if float(rng) < 1e-10:
        return xp.zeros_like(arr)
    return (arr - mn) / rng


def _fillna_with_median(arr: "xp.ndarray") -> "xp.ndarray":
    """Remplace NaN par la médiane des valeurs non-NaN.

    Si toutes les valeurs sont NaN → retourne 0 (composant neutre pour tous).
    """
    nan_mask = xp.isnan(arr)
    if not bool(nan_mask.any()):
        return arr
    valid = arr[~nan_mask]
    if valid.size == 0:
        return xp.zeros_like(arr)
    median = xp.median(valid)
    return xp.where(nan_mask, median, arr)


def score_combinations(
    metrics: ComboMetricsBatch,
    weights: config.ScoreWeights,
    event_score_factors: "xp.ndarray | None" = None,
) -> "xp.ndarray":
    """Score composite v2, shape (C,), valeurs ∈ [0, 1] (avant facteur event).

    Args:
        metrics: arrays per-combo calculés par scoring/metrics.py.
        weights: poids du score (modifiables UI). Renormalisés à somme=1
            si l'utilisateur a modifié les sliders.
        event_score_factors: shape (C,) — multiplicateur événementiel par combo
            (FEAT-005). Si None → 1.0 partout (rétrocompatible).

    Returns:
        Array shape (C,) du score composite final, prêt à être trié.
    """
    w = weights.normalized()

    s_gain = _normalize(metrics.max_gain_real_pct)
    s_ann = _normalize(metrics.annualized_return_pct)
    s_lp = 1.0 - _normalize(metrics.loss_prob)
    s_ml = 1.0 - _normalize(xp.abs(metrics.max_loss_pct))
    s_liq = _normalize(metrics.liquidity_score)
    s_robv = 1.0 - _normalize(metrics.vol_dispersion_pct)
    s_slip = 1.0 - _normalize(_fillna_with_median(metrics.slippage_pct))

    score = (
        w.w_gain_real * s_gain
        + w.w_annualized * s_ann
        + w.w_loss_prob * s_lp
        + w.w_max_loss * s_ml
        + w.w_liquidity * s_liq
        + w.w_robustness * s_robv
        + w.w_slippage * s_slip
    )

    if event_score_factors is not None:
        score = score * event_score_factors

    return score
