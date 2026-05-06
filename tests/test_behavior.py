"""Tests pour screener/behavior.py — métriques comportementales (FEAT-023 § Étape 3)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from screener.behavior import (
    UnderlyingBehavior,
    _empty_behavior,
    batch_compute_behavior,
)


def test_empty_behavior_defaults():
    """Empty behavior : valeurs neutres exploitable par les scorers."""
    b = _empty_behavior("FOO")
    assert b.symbol == "FOO"
    assert b.atr_pct == 0.02
    assert b.hv_ratio_20_60 == 1.0
    assert b.autocorr_1d == 0.0
    assert b.beta_spy == 1.0
    assert b.range_position == 0.5
    assert b.samples == 0


def test_batch_behavior_short_history():
    """Mock yfinance avec moins de 30 jours → empty_behavior."""
    from unittest.mock import patch

    short_data = pd.DataFrame({
        ("Close", "FOO"): pd.Series([100.0, 101.0]),
        ("Close", "SPY"): pd.Series([400.0, 401.0]),
        ("High", "FOO"): pd.Series([101.0, 102.0]),
        ("Low", "FOO"): pd.Series([99.0, 100.5]),
        ("Open", "FOO"): pd.Series([100.5, 100.5]),
    })
    with patch("yfinance.download", return_value=short_data):
        result = batch_compute_behavior(["FOO"])
    assert result["FOO"].samples == 0


def test_batch_behavior_synthetic_mean_revert():
    """Série synthétique mean-revert (rendements anti-corrélés) → autocorr < 0."""
    from unittest.mock import patch

    np.random.seed(42)
    # Construit une série dont les rendements sont anti-corrélés
    n = 120
    closes_foo = [100.0]
    sign = 1
    for _ in range(n):
        # Mean revert : alterne hausse/baisse
        ret = sign * 0.01 + np.random.normal(0, 0.001)
        closes_foo.append(closes_foo[-1] * (1 + ret))
        sign *= -1
    closes_spy = [400.0 * (1 + np.random.normal(0, 0.005)) for _ in range(n + 1)]

    df = pd.DataFrame({
        ("Close", "FOO"): closes_foo,
        ("High", "FOO"): [c * 1.005 for c in closes_foo],
        ("Low", "FOO"): [c * 0.995 for c in closes_foo],
        ("Open", "FOO"): closes_foo,
        ("Close", "SPY"): closes_spy,
        ("High", "SPY"): [c * 1.005 for c in closes_spy],
        ("Low", "SPY"): [c * 0.995 for c in closes_spy],
        ("Open", "SPY"): closes_spy,
    })
    with patch("yfinance.download", return_value=df):
        result = batch_compute_behavior(["FOO"])
    assert result["FOO"].samples > 50
    assert result["FOO"].autocorr_1d < -0.3, f"autocorr={result['FOO'].autocorr_1d}"


def test_batch_behavior_atr_pct_positive():
    """ATR % calculé est strictement positif sur une série avec range non nul."""
    from unittest.mock import patch

    np.random.seed(0)
    n = 80
    closes = 100 + np.cumsum(np.random.normal(0, 0.5, n))
    df = pd.DataFrame({
        ("Close", "X"): closes,
        ("High", "X"): closes + 1.0,
        ("Low", "X"): closes - 1.0,
        ("Open", "X"): closes,
        ("Close", "SPY"): 400 + np.cumsum(np.random.normal(0, 0.5, n)),
        ("High", "SPY"): 400 + np.cumsum(np.random.normal(0, 0.5, n)) + 1.0,
        ("Low", "SPY"): 400 + np.cumsum(np.random.normal(0, 0.5, n)) - 1.0,
        ("Open", "SPY"): 400 + np.cumsum(np.random.normal(0, 0.5, n)),
    })
    with patch("yfinance.download", return_value=df):
        result = batch_compute_behavior(["X"])
    assert result["X"].atr_pct > 0
    assert result["X"].atr_pct < 0.10  # < 10 % pour un série brownien standard
