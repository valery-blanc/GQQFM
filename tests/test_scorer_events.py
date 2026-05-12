"""Tests du scorer avec facteur événementiel (FEAT-005).

Mis a jour pour la signature FEAT-026/FEAT-030 :
  score_combinations(metrics: ComboMetricsBatch, weights, event_score_factors=None, regime_factor=1.0)

On construit un ComboMetricsBatch minimal synthetique au lieu de re-deriver
depuis un pnl_tensor — separe la logique du scorer de celle de compute_combo_metrics.
"""

import numpy as np
import pytest

from config import ScoreWeights
from engine.backend import xp
from scoring.metrics import ComboMetricsBatch
from scoring.scorer import score_combinations


def _to_numpy(arr):
    return np.asarray(arr.get() if hasattr(arr, "get") else arr)


def _make_metrics(c: int = 5) -> ComboMetricsBatch:
    """Construit un ComboMetricsBatch synthetique : C combos, valeurs variees.

    Les valeurs choisies font en sorte qu'apres normalisation min-max, chaque
    composant ait une dispersion non-nulle (sinon `_normalize` renvoie 0
    partout et le multiplicateur d'event devient sans effet observable).
    """
    rng = np.random.default_rng(42)
    # Valeurs croissantes pour chaque metrique (uniques par combo)
    ones = xp.ones((c,), dtype=xp.float32)
    lin = xp.asarray(np.linspace(1.0, 2.0, c, dtype=np.float32))

    return ComboMetricsBatch(
        max_loss_pct=lin * -10.0,
        max_gain_real_pct=lin * 30.0,
        annualized_return_pct=lin * 100.0,
        loss_prob=lin * 0.1,
        liquidity_score=lin * 1000.0,
        vol_dispersion_pct=lin * 5.0,
        slippage_pct=lin * 1.5,
        days_to_close=ones * 30.0,
        max_gain_real_dollar=lin * 200.0,
        max_loss_dollar=lin * -100.0,
        daily_gain_dollar=lin * 6.0,
        realistic_range_pct=lin * 5.0,
        atm_vol_per_combo=lin * 0.20,
        capital_required=ones * 500.0,
        # FEAT-030
        term_slope=lin * 1.1,
        tg_ratio=lin * 0.5,
    )


class TestScorerEventBackwardCompat:
    """event_score_factors=None → score identique à l'appel sans le paramètre."""

    def test_none_factors_unchanged(self):
        metrics = _make_metrics(c=5)
        weights = ScoreWeights()
        score_base = score_combinations(metrics, weights)
        score_none = score_combinations(metrics, weights, event_score_factors=None)
        np.testing.assert_allclose(_to_numpy(score_base), _to_numpy(score_none), rtol=1e-5)


class TestScorerEventBonus:
    """factor=1.15 → score multiplié par 1.15."""

    def test_factor_115_multiplies_score(self):
        metrics = _make_metrics(c=3)
        weights = ScoreWeights()
        factors = xp.full((3,), 1.15, dtype=xp.float32)
        score_base = score_combinations(metrics, weights)
        score_boosted = score_combinations(metrics, weights, event_score_factors=factors)
        np.testing.assert_allclose(
            _to_numpy(score_boosted), _to_numpy(score_base) * 1.15, rtol=1e-5,
        )


class TestScorerEventPenalty:
    """factor=0.7 → score multiplié par 0.7."""

    def test_factor_07_reduces_score(self):
        metrics = _make_metrics(c=3)
        weights = ScoreWeights()
        factors = xp.full((3,), 0.7, dtype=xp.float32)
        score_base = score_combinations(metrics, weights)
        score_reduced = score_combinations(metrics, weights, event_score_factors=factors)
        np.testing.assert_allclose(
            _to_numpy(score_reduced), _to_numpy(score_base) * 0.7, rtol=1e-5,
        )


class TestScorerEventRanking:
    """combo_a (factor=1.15) > combo_b (factor=1.0) si scores de base égaux."""

    def test_event_bonus_improves_ranking(self):
        # Deux combos IDENTIQUES (même score de base) — un avec bonus, un sans.
        # Note : avec _make_metrics les combos ont des valeurs differentes ;
        # on construit donc explicitement 2 combos avec des metrics identiques.
        ones = xp.ones((2,), dtype=xp.float32)
        metrics = ComboMetricsBatch(
            max_loss_pct=ones * -10.0,
            max_gain_real_pct=ones * 30.0,
            annualized_return_pct=ones * 100.0,
            loss_prob=ones * 0.1,
            liquidity_score=ones * 1000.0,
            vol_dispersion_pct=ones * 5.0,
            slippage_pct=ones * 1.5,
            days_to_close=ones * 30.0,
            max_gain_real_dollar=ones * 200.0,
            max_loss_dollar=ones * -100.0,
            daily_gain_dollar=ones * 6.0,
            realistic_range_pct=ones * 5.0,
            atm_vol_per_combo=ones * 0.20,
            capital_required=ones * 500.0,
            term_slope=ones * 1.1,
            tg_ratio=ones * 0.5,
        )
        weights = ScoreWeights()
        factors = xp.array([1.15, 1.0], dtype=xp.float32)
        scores = score_combinations(metrics, weights, event_score_factors=factors)
        s = _to_numpy(scores)
        assert s[0] > s[1], (
            f"combo avec FOMC (factor=1.15) doit scorer plus haut : "
            f"{s[0]:.4f} vs {s[1]:.4f}"
        )


class TestScorerRegimeFactor:
    """FEAT-030-B : regime_factor scalaire — multiplie tous les scores uniformement."""

    def test_regime_factor_scales_all(self):
        metrics = _make_metrics(c=4)
        weights = ScoreWeights()
        score_neutral = score_combinations(metrics, weights, regime_factor=1.0)
        score_penalty = score_combinations(metrics, weights, regime_factor=0.55)
        np.testing.assert_allclose(
            _to_numpy(score_penalty), _to_numpy(score_neutral) * 0.55, rtol=1e-5,
        )

    def test_regime_factor_preserves_ranking(self):
        """Un facteur scalaire ne change pas l'ordre relatif (Spearman = 1)."""
        metrics = _make_metrics(c=10)
        weights = ScoreWeights()
        s1 = _to_numpy(score_combinations(metrics, weights, regime_factor=1.0))
        s2 = _to_numpy(score_combinations(metrics, weights, regime_factor=0.55))
        # Same ranking order
        assert list(np.argsort(s1)) == list(np.argsort(s2))
