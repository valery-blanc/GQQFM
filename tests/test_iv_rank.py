"""Tests pour screener/iv_rank.py (FEAT-023 § Étape 3)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from screener.iv_rank import compute_iv_rank_52w_from_history


def test_iv_rank_insufficient_history():
    """Moins de 200 jours → fallback 50.0 (neutre)."""
    closes = pd.Series([100.0] * 50)
    rank = compute_iv_rank_52w_from_history(closes, current_iv=0.30, current_hv30=0.25)
    assert rank == pytest.approx(50.0)


def test_iv_rank_zero_inputs():
    """IV ou HV nuls → 50.0 (neutre)."""
    closes = pd.Series([100.0] * 252)
    assert compute_iv_rank_52w_from_history(closes, 0.0, 0.25) == pytest.approx(50.0)
    assert compute_iv_rank_52w_from_history(closes, 0.30, 0.0) == pytest.approx(50.0)


def test_iv_rank_at_max():
    """current_iv au max historique reconstruit → rank ≈ 100."""
    np.random.seed(42)
    n = 260
    # Volatilité croissante à la fin (HV monte)
    rets = np.concatenate([
        np.random.normal(0, 0.005, n - 30),     # vol basse
        np.random.normal(0, 0.030, 30),         # vol haute (current)
    ])
    closes = pd.Series(100 * np.cumprod(1 + rets))
    # HV30 actuelle = vol haute → annualisée
    current_hv30 = float(np.std(rets[-21:])) * math.sqrt(252)
    # Si current_iv = current_hv30 × ratio, position ≈ haut du range
    current_iv = current_hv30 * 1.0
    rank = compute_iv_rank_52w_from_history(closes, current_iv, current_hv30)
    assert rank > 70.0, f"rank={rank}"


def test_iv_rank_at_min():
    """current_iv en bas du range historique reconstruit → rank ≈ 0."""
    np.random.seed(42)
    n = 260
    # Volatilité décroissante : haute au début, basse à la fin
    rets = np.concatenate([
        np.random.normal(0, 0.030, n - 30),
        np.random.normal(0, 0.005, 30),
    ])
    closes = pd.Series(100 * np.cumprod(1 + rets))
    current_hv30 = float(np.std(rets[-21:])) * math.sqrt(252)
    current_iv = current_hv30 * 1.0
    rank = compute_iv_rank_52w_from_history(closes, current_iv, current_hv30)
    assert rank < 30.0, f"rank={rank}"


def test_iv_rank_clipped_0_100():
    """Rank toujours dans [0, 100]."""
    np.random.seed(0)
    closes = pd.Series(100 * np.cumprod(1 + np.random.normal(0, 0.01, 260)))
    for iv in [0.05, 0.20, 0.50, 1.0]:
        rank = compute_iv_rank_52w_from_history(closes, current_iv=iv, current_hv30=0.20)
        assert 0.0 <= rank <= 100.0
