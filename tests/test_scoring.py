"""Tests du module scoring — filtres et probabilité de perte."""

from datetime import date, timedelta

import numpy as np
import pytest

from data.models import ScoringCriteria
from engine.backend import xp
from scoring.filters import filter_combinations
from scoring.probability import compute_loss_probability


def make_pnl_smile(C: int = 5, M: int = 100) -> tuple:
    """
    Génère un tenseur P&L en U (smile) :
    - Centre ≈ -100$ (perte)
    - Extrêmes ≈ +500$ (profit)
    """
    spot_range = xp.linspace(80.0, 120.0, M, dtype=xp.float32)
    spots_np = np.linspace(80.0, 120.0, M, dtype=np.float32)
    center = 100.0
    pnl_curve = ((spots_np - center) ** 2 * 0.5 - 100).astype(np.float32)
    pnl_mid = xp.tile(xp.array(pnl_curve)[None, :], (C, 1))  # (C, M)
    pnl_tensor = xp.stack([pnl_mid * 0.8, pnl_mid, pnl_mid * 1.2], axis=0)  # (3, C, M)
    return pnl_tensor, spot_range, pnl_mid


class TestLossProbability:
    def test_output_shape(self):
        _, spot_range, pnl_mid = make_pnl_smile(5)
        probs = compute_loss_probability(pnl_mid, spot_range, 100.0, 0.20, 30, 0.045)
        arr = np.asarray(probs.get() if hasattr(probs, "get") else probs)
        assert arr.shape == (5,)

    def test_range(self):
        _, spot_range, pnl_mid = make_pnl_smile(10)
        probs = compute_loss_probability(pnl_mid, spot_range, 100.0, 0.20, 30, 0.045)
        arr = np.asarray(probs.get() if hasattr(probs, "get") else probs)
        assert np.all(arr >= 0.0) and np.all(arr <= 1.0)

    def test_zero_days(self):
        """0 jours jusqu'à clôture → probabilité = 0."""
        _, spot_range, pnl_mid = make_pnl_smile(3)
        probs = compute_loss_probability(pnl_mid, spot_range, 100.0, 0.20, 0, 0.045)
        arr = np.asarray(probs.get() if hasattr(probs, "get") else probs)
        assert np.all(arr == 0.0)

    def test_always_profit(self):
        """Courbe entièrement positive → proba perte ≈ 0."""
        spot_range = xp.linspace(80.0, 120.0, 100, dtype=xp.float32)
        pnl_pos = xp.ones((1, 100), dtype=xp.float32) * 500
        probs = compute_loss_probability(pnl_pos, spot_range, 100.0, 0.20, 30, 0.045)
        arr = np.asarray(probs.get() if hasattr(probs, "get") else probs)
        assert arr[0] < 0.01


class TestFilterCombinations:
    def _run_filter(self, criteria: ScoringCriteria):
        pnl_tensor, spot_range, _ = make_pnl_smile(C=10)
        net_debits = xp.full((10,), 200.0, dtype=xp.float32)
        avg_volumes = xp.full((10,), 100.0, dtype=xp.float32)
        return filter_combinations(
            pnl_tensor, spot_range, net_debits, avg_volumes,
            criteria, 100.0, 0.20, 30, 0.045,
        )

    def test_permissive_criteria_finds_results(self):
        """Des critères très larges doivent retourner toutes les combos."""
        criteria = ScoringCriteria(
            max_loss_pct=-100.0,
            max_loss_probability_pct=100.0,
            min_max_gain_pct=0.0,
            min_gain_loss_ratio=0.0,
            max_net_debit=1_000_000,
            min_avg_volume=0,
        )
        indices = self._run_filter(criteria)
        arr = np.asarray(indices.get() if hasattr(indices, "get") else indices)
        assert len(arr) == 10

    def test_strict_criteria_filters_all(self):
        """Des critères impossibles doivent retourner 0 résultats."""
        criteria = ScoringCriteria(
            max_loss_pct=0.0,      # aucune perte tolérée
            max_loss_probability_pct=0.0,
            min_max_gain_pct=10_000.0,
            min_gain_loss_ratio=1_000.0,
            max_net_debit=0.01,
        )
        indices = self._run_filter(criteria)
        arr = np.asarray(indices.get() if hasattr(indices, "get") else indices)
        assert len(arr) == 0
