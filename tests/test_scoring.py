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
        """Des critères très larges doivent retourner toutes les combos.

        Note : `make_pnl_smile` produit une courbe en U (pertes au centre,
        gains aux extrêmes). La fenêtre ±1σ autour du spot=100 tombe sur le
        creux du smile (max_gain_real ≈ -84$ = -42% du net_debit), donc
        `min_max_gain_pct` doit être suffisamment négatif pour ne pas filtrer.
        `min_gain_loss_ratio=-1e9` même chose côté ratio (gain négatif / |loss|).
        """
        criteria = ScoringCriteria(
            max_loss_pct=-100.0,
            max_loss_probability_pct=100.0,
            min_max_gain_pct=-100.0,
            min_gain_loss_ratio=-1e9,
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


# ── FEAT-030 — Tests pour compute_term_slopes ────────────────────────────────

import math
from datetime import date, timedelta

from data.models import Leg, Combination
from scoring.metrics import compute_term_slopes


def _make_leg(strike, expiration, iv, direction=1, option_type="call"):
    return Leg(
        option_type=option_type, direction=direction, quantity=1,
        strike=strike, expiration=expiration, entry_price=1.0, implied_vol=iv,
    )


def _make_combo(legs, template="calendar"):
    return Combination(
        legs=legs,
        net_debit=100.0,
        close_date=min(l.expiration for l in legs if l.direction < 0) if any(l.direction < 0 for l in legs) else legs[0].expiration,
        template_name=template,
    )


class TestComputeTermSlopes:
    def test_k1_returns_nan(self):
        """K=1 (1 seule expiration) → NaN."""
        exp = date(2025, 6, 1)
        combo = _make_combo([
            _make_leg(100, exp, 0.20),
            _make_leg(105, exp, 0.22),
        ])
        out = compute_term_slopes([combo])
        assert math.isnan(out[0])

    def test_k2_calendar_structure_positive(self):
        """Calendar IV_near > IV_far → ratio > 1.0."""
        near = date(2025, 6, 1)
        far = date(2025, 7, 1)
        combo = _make_combo([
            _make_leg(100, near, 0.30, direction=-1),    # short near, IV élevée
            _make_leg(100, near, 0.32, direction=-1, option_type="put"),
            _make_leg(100, far, 0.20, direction=1),      # long far, IV basse
            _make_leg(100, far, 0.22, direction=1, option_type="put"),
        ])
        out = compute_term_slopes([combo])
        # near_mean=0.31, far_mean=0.21, ratio≈1.476
        assert abs(out[0] - (0.31 / 0.21)) < 1e-4
        assert out[0] > 1.0

    def test_k2_backwardation_below_one(self):
        """IV_near < IV_far → ratio < 1.0 (structure inversée)."""
        near = date(2025, 6, 1)
        far = date(2025, 7, 1)
        combo = _make_combo([
            _make_leg(100, near, 0.18, direction=-1),
            _make_leg(100, far, 0.25, direction=1),
        ])
        out = compute_term_slopes([combo])
        assert out[0] < 1.0

    def test_batch(self):
        """Plusieurs combos → array shape (C,)."""
        exp1, exp2 = date(2025, 6, 1), date(2025, 7, 1)
        combos = [
            _make_combo([_make_leg(100, exp1, 0.30, direction=-1),
                         _make_leg(100, exp2, 0.20, direction=1)]),
            _make_combo([_make_leg(100, exp1, 0.18, direction=-1),
                         _make_leg(100, exp2, 0.22, direction=1)]),
            _make_combo([_make_leg(100, exp1, 0.25)]),   # K=1
        ]
        out = compute_term_slopes(combos)
        assert len(out) == 3
        assert out[0] > 1.0      # near > far
        assert out[1] < 1.0      # near < far
        assert math.isnan(out[2])
