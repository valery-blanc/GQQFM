"""Tests pour le scoring multi-stratégie (FEAT-023 § Étape 3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from screener.behavior import UnderlyingBehavior
from screener.models import OptionsMetrics
from screener.scorer import (
    _score_calmness,
    _score_iv_rank_calendar,
    _score_iv_rank_ric,
    _score_term_structure_calendar,
    _score_vol_acceleration,
    compute_score_calendar,
    compute_score_ric,
)


def _make_metrics(**overrides) -> OptionsMetrics:
    defaults = dict(
        symbol="X", spot_price=100.0,
        iv_atm_near=0.30, iv_atm_far=0.30, hv30=0.25,
        iv_rank_proxy=45.0, term_structure_ratio=1.00,
        avg_bid_ask_spread_pct=0.03,
        avg_volume_near=1000.0, avg_volume_far=800.0,
        avg_oi_near=5000.0, avg_oi_far=4000.0,
        strike_count_near=20, strike_count_far=18,
        weekly_count=3,
        near_expiry=date.today() + timedelta(days=14),
        far_expiry=date.today() + timedelta(days=45),
        events_in_danger_zone=[], events_in_sweet_zone=[],
        event_score_factor=1.0,
        spread_pct_atm_near=0.03, spread_pct_atm_far=0.03,
        volume_atm_median_near=500.0, volume_atm_p25_near=200.0,
        oi_atm_median_near=3000.0, oi_atm_p25_near=1500.0,
        strike_count_atm_near=8, strike_count_atm_far=8,
        iv_rank_52w=45.0,
    )
    defaults.update(overrides)
    return OptionsMetrics(**defaults)


def _make_behavior(**overrides) -> UnderlyingBehavior:
    defaults = dict(
        symbol="X", autocorr_1d=0.0, atr_pct=0.015, gap_rate_2pct=0.05,
        hv_ratio_20_60=1.0, trend_strength=0.5, beta_spy=1.0,
        range_position=0.5, samples=120,
    )
    defaults.update(overrides)
    return UnderlyingBehavior(**defaults)


# ── Composantes calendar ──────────────────────────────────────────────────────

def test_iv_rank_calendar_optimal_at_42():
    assert _score_iv_rank_calendar(42.0) == pytest.approx(1.0)


def test_iv_rank_calendar_zero_far():
    assert _score_iv_rank_calendar(100.0) == pytest.approx(0.0, abs=0.05)
    assert _score_iv_rank_calendar(0.0) == pytest.approx(0.0, abs=0.05)


def test_term_structure_calendar_optimal_flat():
    assert _score_term_structure_calendar(1.00) == pytest.approx(1.0)
    assert _score_term_structure_calendar(1.05) == pytest.approx(1.0)


def test_term_structure_calendar_penalizes_extremes():
    """Bord du domaine régulier : score = floor 0.20 (pas un cliff à 0)."""
    assert _score_term_structure_calendar(0.85) == pytest.approx(0.20)
    assert _score_term_structure_calendar(1.20) == pytest.approx(0.20)


def test_term_structure_calendar_floor_aberrant():
    """Mesure aberrante (ratio 1.53) → floor 0.20 (pas zéro), évite tuer un bon ticker."""
    assert _score_term_structure_calendar(1.53) == pytest.approx(0.20)
    assert _score_term_structure_calendar(0.50) == pytest.approx(0.20)


# ── Composantes RIC ───────────────────────────────────────────────────────────

def test_iv_rank_ric_low_is_good():
    assert _score_iv_rank_ric(15.0) == pytest.approx(1.0)
    assert _score_iv_rank_ric(60.0) == pytest.approx(0.0)
    assert _score_iv_rank_ric(80.0) == pytest.approx(0.0)


def test_vol_acceleration_score():
    assert _score_vol_acceleration(1.0) == pytest.approx(0.0)
    assert _score_vol_acceleration(1.4) == pytest.approx(1.0)
    assert _score_vol_acceleration(1.2) == pytest.approx(0.5)


# ── Calmness ──────────────────────────────────────────────────────────────────

def test_calmness_high_for_mean_revert_low_atr():
    b = _make_behavior(autocorr_1d=-0.1, atr_pct=0.012, gap_rate_2pct=0.02, hv_ratio_20_60=1.0)
    assert _score_calmness(b) > 0.85


def test_calmness_low_for_trend_high_atr():
    b = _make_behavior(autocorr_1d=0.4, atr_pct=0.05, gap_rate_2pct=0.25, hv_ratio_20_60=1.5)
    assert _score_calmness(b) < 0.20


# ── Score composite multi-stratégie ──────────────────────────────────────────

def test_calendar_score_prefers_low_iv_stable():
    """Un ticker IV bas + vol stable a un meilleur score calendar qu'un ticker
    IV élevé + vol erratique."""
    good = _make_metrics(iv_rank_52w=42.0, term_structure_ratio=1.02)
    good_b = _make_behavior(autocorr_1d=-0.05, atr_pct=0.013, gap_rate_2pct=0.02, hv_ratio_20_60=1.0)

    bad = _make_metrics(iv_rank_52w=85.0, term_structure_ratio=1.18)
    bad_b = _make_behavior(autocorr_1d=0.4, atr_pct=0.05, gap_rate_2pct=0.30, hv_ratio_20_60=1.5)

    assert compute_score_calendar(good, good_b) > compute_score_calendar(bad, bad_b)


def test_calendar_penalizes_high_iv_rank():
    """IV Rank > 70 → pénalité ×0.5 ; > 85 → ×0.3 (vol overpriced, mauvais pour acheter)."""
    base = _make_metrics(iv_rank_52w=42.0)
    high = _make_metrics(iv_rank_52w=75.0)
    extreme = _make_metrics(iv_rank_52w=90.0)
    b = _make_behavior()

    s_base = compute_score_calendar(base, b)
    s_high = compute_score_calendar(high, b)
    s_extreme = compute_score_calendar(extreme, b)

    # Plus l'IV Rank est haut, plus le score est bas (au-delà de 70)
    assert s_base > s_high > s_extreme


def test_calendar_rewards_vol_compression():
    """Vol qui décélère (HV20/60 < 1) est un bonus calendar, pas une pénalité."""
    metrics = _make_metrics()
    b_decel = _make_behavior(hv_ratio_20_60=0.70)   # vol qui se compresse
    b_stable = _make_behavior(hv_ratio_20_60=1.00)
    b_accel = _make_behavior(hv_ratio_20_60=1.30)   # vol qui accélère

    s_decel = compute_score_calendar(metrics, b_decel)
    s_stable = compute_score_calendar(metrics, b_stable)
    s_accel = compute_score_calendar(metrics, b_accel)

    # Vol qui décélère ≥ vol stable > vol qui accélère
    assert s_decel == pytest.approx(s_stable, rel=0.01)  # même score (compression maxée à 1)
    assert s_stable > s_accel


def test_ric_score_prefers_vol_acceleration():
    """Un ticker IV bas + vol qui accélère a un meilleur score RIC qu'un ticker
    IV haut + vol stable."""
    good = _make_metrics(iv_rank_52w=20.0)
    good_b = _make_behavior(hv_ratio_20_60=1.4, atr_pct=0.04)

    bad = _make_metrics(iv_rank_52w=70.0)
    bad_b = _make_behavior(hv_ratio_20_60=0.9, atr_pct=0.01)

    assert compute_score_ric(good, good_b) > compute_score_ric(bad, bad_b)


def test_calendar_vs_ric_inverted_preference():
    """
    Un même ticker peut avoir un score calendar élevé et un score RIC bas
    (et inversement). Démontre que les profils ne classent pas pareil.
    """
    # Ticker calendar-friendly : IV modéré, vol stable
    cal_metrics = _make_metrics(iv_rank_52w=42.0)
    cal_b = _make_behavior(autocorr_1d=-0.05, atr_pct=0.013, hv_ratio_20_60=1.0)
    cal_score_calendar = compute_score_calendar(cal_metrics, cal_b)
    cal_score_ric = compute_score_ric(cal_metrics, cal_b)

    # Ticker RIC-friendly : IV bas, ATR élevé, vol qui accélère
    ric_metrics = _make_metrics(iv_rank_52w=20.0)
    ric_b = _make_behavior(autocorr_1d=0.0, atr_pct=0.04, hv_ratio_20_60=1.4)
    ric_score_calendar = compute_score_calendar(ric_metrics, ric_b)
    ric_score_ric = compute_score_ric(ric_metrics, ric_b)

    # Calendar préfère le ticker calendar-friendly
    assert cal_score_calendar > ric_score_calendar
    # RIC préfère le ticker RIC-friendly
    assert ric_score_ric > cal_score_ric


def test_legacy_compatibility_when_behavior_none():
    """compute_score_calendar(None) retombe sur compute_score legacy."""
    from screener.scorer import compute_score
    metrics = _make_metrics()
    assert compute_score_calendar(metrics, None) == compute_score(metrics)
    assert compute_score_ric(metrics, None) == compute_score(metrics)
