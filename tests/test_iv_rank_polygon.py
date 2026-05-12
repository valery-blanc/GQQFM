"""Tests pour screener/iv_rank_polygon.py (FEAT-024)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from screener.iv_rank_polygon import (
    _sample_dates,
    compute_iv_rank_from_history,
)


def test_sample_dates_count_and_chronology():
    """52 semaines × cadence 7j = 52 dates, chronologiques, sans week-end."""
    today = date(2026, 5, 6)  # mercredi
    dates = _sample_dates(weeks_back=52, cadence_days=7, today=today)
    assert len(dates) == 52
    # Chronologique
    assert dates == sorted(dates)
    # Pas de week-end
    assert all(d.weekday() < 5 for d in dates)
    # La dernière date est ≤ today
    assert dates[-1] <= today


def test_sample_dates_handles_weekend():
    """Si today tombe un samedi/dimanche, recule au vendredi."""
    saturday = date(2026, 5, 9)
    dates = _sample_dates(weeks_back=4, cadence_days=7, today=saturday)
    assert all(d.weekday() < 5 for d in dates)


def test_iv_rank_from_history_empty():
    """Historique vide → 50.0 (neutre)."""
    assert compute_iv_rank_from_history([], current_iv=0.30) == pytest.approx(50.0)


def test_iv_rank_from_history_too_few_points():
    """< min_points (defaut 10) → 50.0.
    Note : BUG-030 a baisse min_points de 20 a 10 — test mis a jour en consequence.
    """
    history = [(date(2026, 1, i + 1), 0.20 + i * 0.01) for i in range(5)]
    assert compute_iv_rank_from_history(history, current_iv=0.25) == pytest.approx(50.0)


def test_iv_rank_from_history_at_max():
    """current_iv = max → rank = 100."""
    history = [(date(2025, m, 15), 0.10 + m * 0.01) for m in range(1, 13)] * 2  # 24 points
    max_iv = max(iv for _, iv in history)
    assert compute_iv_rank_from_history(history, current_iv=max_iv) == pytest.approx(100.0)


def test_iv_rank_from_history_at_min():
    """current_iv = min → rank = 0."""
    history = [(date(2025, m, 15), 0.10 + m * 0.01) for m in range(1, 13)] * 2
    min_iv = min(iv for _, iv in history)
    assert compute_iv_rank_from_history(history, current_iv=min_iv) == pytest.approx(0.0)


def test_iv_rank_from_history_in_middle():
    """current_iv au milieu du range → rank ≈ 50."""
    history = [(date(2025, 1, 1) + timedelta(days=i), 0.10 + i * 0.005) for i in range(30)]
    iv_min = 0.10
    iv_max = 0.10 + 29 * 0.005
    middle = (iv_min + iv_max) / 2
    rank = compute_iv_rank_from_history(history, current_iv=middle)
    assert 45.0 < rank < 55.0


def test_iv_rank_from_history_clipped():
    """current_iv hors range → clippé 0-100."""
    history = [(date(2025, 1, 1) + timedelta(days=i), 0.20) for i in range(30)]
    # Above max
    high = compute_iv_rank_from_history(history, current_iv=0.50)
    assert high == pytest.approx(50.0)  # max == min → fallback neutre
