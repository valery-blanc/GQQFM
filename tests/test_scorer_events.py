"""Tests du scorer avec facteur événementiel (FEAT-005)."""

import numpy as np
import pytest

from engine.backend import xp
from scoring.scorer import score_combinations


def make_pnl_smile(C: int = 5, M: int = 100):
    """P&L en U : centre ≈ -100$, extrêmes ≈ +500$."""
    spot_range = xp.linspace(80.0, 120.0, M, dtype=xp.float32)
    spots_np = np.linspace(80.0, 120.0, M, dtype=np.float32)
    center = 100.0
    pnl_curve = ((spots_np - center) ** 2 * 0.5 - 100).astype(np.float32)
    pnl_mid = xp.tile(xp.array(pnl_curve)[None, :], (C, 1))
    return pnl_mid, spot_range


def _to_numpy(arr):
    return np.asarray(arr.get() if hasattr(arr, "get") else arr)


class TestScorerEventBackwardCompat:
    """Test 6 — event_score_factors=None → score identique à l'actuel."""

    def test_none_factors_unchanged(self):
        """Passer None doit donner le même résultat que sans le paramètre."""
        pnl_mid, spot_range = make_pnl_smile(C=5)
        net_debits = xp.full((5,), 200.0, dtype=xp.float32)

        score_base = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
        )
        score_none = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
            event_score_factors=None,
        )

        base_np = _to_numpy(score_base)
        none_np = _to_numpy(score_none)
        np.testing.assert_allclose(base_np, none_np, rtol=1e-5)


class TestScorerEventBonus:
    """Test 7 — factor=1.15 → score multiplié par 1.15."""

    def test_factor_115_multiplies_score(self):
        pnl_mid, spot_range = make_pnl_smile(C=3)
        net_debits = xp.full((3,), 200.0, dtype=xp.float32)
        factors = xp.full((3,), 1.15, dtype=xp.float32)

        score_base = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
        )
        score_boosted = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
            event_score_factors=factors,
        )

        base_np = _to_numpy(score_base)
        boosted_np = _to_numpy(score_boosted)
        np.testing.assert_allclose(boosted_np, base_np * 1.15, rtol=1e-5)


class TestScorerEventPenalty:
    """Test 8 — factor=0.7 → score multiplié par 0.7."""

    def test_factor_07_reduces_score(self):
        pnl_mid, spot_range = make_pnl_smile(C=3)
        net_debits = xp.full((3,), 200.0, dtype=xp.float32)
        factors = xp.full((3,), 0.7, dtype=xp.float32)

        score_base = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
        )
        score_reduced = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
            event_score_factors=factors,
        )

        base_np = _to_numpy(score_base)
        reduced_np = _to_numpy(score_reduced)
        np.testing.assert_allclose(reduced_np, base_np * 0.7, rtol=1e-5)


class TestScorerEventRanking:
    """Test 9 — classement : combo_a (factor=1.15) > combo_b (factor=1.0)."""

    def test_event_bonus_improves_ranking(self):
        """
        Deux combos identiques sauf factor :
        combo_a factor=1.15 doit scorer plus haut que combo_b factor=1.0.
        """
        pnl_mid, spot_range = make_pnl_smile(C=2)
        net_debits = xp.full((2,), 200.0, dtype=xp.float32)
        # combo 0 : factor=1.15 (bonus FOMC)
        # combo 1 : factor=1.0 (neutre)
        factors = xp.array([1.15, 1.0], dtype=xp.float32)

        scores = score_combinations(
            pnl_mid, net_debits, spot_range, 100.0, 0.20, 30, 0.045,
            event_score_factors=factors,
        )

        scores_np = _to_numpy(scores)
        assert scores_np[0] > scores_np[1], (
            f"combo avec FOMC (factor=1.15) doit scorer plus haut : "
            f"{scores_np[0]:.4f} vs {scores_np[1]:.4f}"
        )
