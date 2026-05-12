"""FEAT-030 — Tests pour scoring/regime.py."""

import math
from datetime import date, timedelta

import numpy as np
import pytest

import config
from scoring.regime import (
    compute_hv30_from_bars,
    compute_hv30_from_closes,
    compute_hv30_percentiles,
    compute_regime_factor,
)


class TestComputeHV30FromCloses:
    def test_insufficient_data_returns_zero(self):
        assert compute_hv30_from_closes(np.array([100.0])) == 0.0
        assert compute_hv30_from_closes(np.array([])) == 0.0

    def test_constant_prices_returns_zero(self):
        # variance nulle → std = 0 → hv = 0 → fallback 0.0
        closes = np.full(30, 100.0)
        assert compute_hv30_from_closes(closes) == 0.0

    def test_synthetic_hv_matches_manual_calc(self):
        # 22 closes avec log-returns connus → HV calculable à la main.
        rng = np.random.default_rng(42)
        log_rets = rng.normal(0.0, 0.01, size=21)   # std ≈ 0.01
        closes = np.empty(22)
        closes[0] = 100.0
        for i, r in enumerate(log_rets):
            closes[i + 1] = closes[i] * math.exp(r)
        hv = compute_hv30_from_closes(closes, win=21)
        expected = float(log_rets.std() * math.sqrt(252))
        assert abs(hv - expected) < 1e-6

    def test_filters_non_positive_closes(self):
        # Closes avec 0 ou négatif doivent être filtrés.
        rng = np.random.default_rng(1)
        good = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 25)))
        bad = np.concatenate([[0.0, -1.0], good])
        assert compute_hv30_from_closes(bad, win=21) > 0


class TestComputeHV30FromBars:
    def test_filters_dates_after_as_of(self):
        as_of = date(2025, 6, 1)
        bars = {
            as_of - timedelta(days=i): (100.0 + i * 0.5, 1000) for i in range(30)
        }
        # Add a future bar that should be ignored.
        bars[as_of + timedelta(days=1)] = (-9999.0, 0)
        hv = compute_hv30_from_bars(bars, as_of, win=21)
        assert hv > 0  # non-zero from the 30 days before

    def test_empty_returns_zero(self):
        assert compute_hv30_from_bars({}, date(2025, 1, 1)) == 0.0


class TestComputeHV30Percentiles:
    def test_insufficient_returns_none(self):
        assert compute_hv30_percentiles(np.array([100.0] * 20)) is None
        assert compute_hv30_percentiles(np.array([])) is None

    def test_returns_p10_current_p90(self):
        rng = np.random.default_rng(7)
        log_rets = rng.normal(0.0, 0.015, size=200)
        closes = 100.0 * np.exp(np.cumsum(log_rets))
        result = compute_hv30_percentiles(closes, win=21, lookback=90)
        assert result is not None
        p10, current, p90 = result
        assert 0 < p10 <= current <= p90 or p10 <= current <= p90
        # Tous finis et positifs
        assert all(math.isfinite(x) and x > 0 for x in (p10, current, p90))


class TestComputeRegimeFactor:
    def test_no_data_neutral(self):
        assert compute_regime_factor(0.0, 0.2) == 1.0
        assert compute_regime_factor(0.2, 0.0) == 1.0
        assert compute_regime_factor(-1.0, 0.2) == 1.0

    def test_thresholds_match_config(self):
        # iv = 0.20
        # hv=0.10 → ratio=0.50 < 0.60 → 1.05
        assert compute_regime_factor(0.10, 0.20) == 1.05
        # hv=0.15 → ratio=0.75 < 0.85 → 1.00
        assert compute_regime_factor(0.15, 0.20) == 1.00
        # hv=0.19 → ratio=0.95 < 1.00 → 0.80
        assert compute_regime_factor(0.19, 0.20) == 0.80
        # hv=0.25 → ratio=1.25 ≥ 1.00 → 0.55
        assert compute_regime_factor(0.25, 0.20) == 0.55

    def test_boundary_inclusive_lower(self):
        # hv/iv = 0.60 exact : selon `<` strict → tombe dans bucket suivant (1.00)
        assert compute_regime_factor(0.12, 0.20) == 1.00
        # hv/iv = 0.85 exact → bucket 0.80
        assert compute_regime_factor(0.17, 0.20) == 0.80
