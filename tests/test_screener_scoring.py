"""
Tests unitaires pour screener/scorer.py.
Vérifie les 5 composantes du score et les pénalités multiplicatives.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from screener.scorer import (
    _score_density,
    _score_events,
    _score_iv_rank,
    _score_liquidity,
    _score_term_structure,
    check_disqualification,
    compute_score,
)
from screener.models import OptionsMetrics
from events.models import EventImpact, EventScope, MarketEvent


# ── helpers ──────────────────────────────────────────────────────────────────

def make_metrics(**overrides) -> OptionsMetrics:
    """Crée des OptionsMetrics valides avec des valeurs par défaut."""
    defaults = dict(
        symbol="TEST",
        spot_price=100.0,
        iv_atm_near=0.30,
        iv_atm_far=0.28,
        hv30=0.25,
        iv_rank_proxy=45.0,
        term_structure_ratio=0.93,
        avg_bid_ask_spread_pct=0.03,
        avg_volume_near=2000.0,
        avg_volume_far=1500.0,
        avg_oi_near=5000.0,
        avg_oi_far=4000.0,
        strike_count_near=25,
        strike_count_far=20,
        weekly_count=3,
        near_expiry=date.today() + timedelta(days=14),
        far_expiry=date.today() + timedelta(days=45),
        events_in_danger_zone=[],
        events_in_sweet_zone=[],
        event_score_factor=1.0,
        next_earnings_date=None,
        next_ex_div_date=None,
        disqualification_reason=None,
    )
    defaults.update(overrides)
    return OptionsMetrics(**defaults)


# ── T1 : IV Rank proxy ───────────────────────────────────────────────────────

def test_iv_rank_optimal():
    """T1 : IV Rank = 45 → score = 1.0 (optimal)."""
    assert _score_iv_rank(45.0) == pytest.approx(1.0)


def test_iv_rank_zero():
    """T1 : IV Rank = 0 → score ~ 0.18 (abs(0-45)/55 = 0.818 → 1-0.818 ≈ 0.18)."""
    score = _score_iv_rank(0.0)
    assert score == pytest.approx(1.0 - 45.0 / 55.0)


def test_iv_rank_100():
    """T1 : IV Rank = 100 → score = 0.0 (abs(100-45)/55 = 1.0)."""
    assert _score_iv_rank(100.0) == pytest.approx(0.0)


def test_iv_rank_clipped():
    """IV Rank ne peut pas être négatif."""
    assert _score_iv_rank(150.0) == 0.0


# ── T2 : Term structure ───────────────────────────────────────────────────────

def test_term_structure_ideal():
    """T2 : ratio ≤ 1.00 → score = 1.0."""
    assert _score_term_structure(0.85) == pytest.approx(1.0)
    assert _score_term_structure(1.00) == pytest.approx(1.0)


def test_term_structure_mid():
    """T2 : ratio = 1.15 → score = 0.5."""
    assert _score_term_structure(1.15) == pytest.approx(0.5)


def test_term_structure_bad():
    """T2 : ratio ≥ 1.30 → score = 0.0."""
    assert _score_term_structure(1.30) == pytest.approx(0.0)
    assert _score_term_structure(1.50) == 0.0


# ── T3 : Pénalités multiplicatives ───────────────────────────────────────────

def test_penalty_ex_div_and_high_iv_rank():
    """T3 : ex-div proche + IV Rank = 80 → score × 0.3 × 0.5 = score × 0.15."""
    metrics = make_metrics(
        iv_rank_proxy=80.0,
        next_ex_div_date=date.today() + timedelta(days=20),
    )
    base_score = compute_score(make_metrics(iv_rank_proxy=80.0))  # sans ex-div
    penalized_score = compute_score(metrics)

    # Ex-div penalty = 0.3, IV Rank > 70 penalty = 0.5 → total ×0.15
    assert penalized_score == pytest.approx(base_score * 0.3, rel=0.05)


def test_penalty_backwardation():
    """Backwardation > 1.15 → pénalité ×0.7."""
    no_backwardation = make_metrics(term_structure_ratio=1.10)
    with_backwardation = make_metrics(term_structure_ratio=1.20)

    score_normal = compute_score(no_backwardation)
    score_back = compute_score(with_backwardation)

    # Le score avec backwardation doit être inférieur, avec la pénalité ×0.7
    # (et aussi composante term structure plus basse)
    assert score_back < score_normal


# ── T4 : Classement correct ───────────────────────────────────────────────────

def test_ranking_order():
    """T4 : ticker avec métriques supérieures doit avoir un score plus élevé."""
    good = make_metrics(
        iv_rank_proxy=45.0,
        term_structure_ratio=0.90,
        avg_bid_ask_spread_pct=0.02,
        avg_volume_near=5000.0,
        avg_volume_far=4000.0,
        avg_oi_near=20000.0,
        avg_oi_far=15000.0,
        weekly_count=4,
        event_score_factor=1.10,
    )
    bad = make_metrics(
        iv_rank_proxy=95.0,
        term_structure_ratio=1.25,
        avg_bid_ask_spread_pct=0.09,
        avg_volume_near=200.0,
        avg_volume_far=150.0,
        avg_oi_near=600.0,
        avg_oi_far=500.0,
        weekly_count=0,
        event_score_factor=0.7,
    )
    assert compute_score(good) > compute_score(bad)


def test_score_range():
    """Le score doit toujours être entre 0 et 100."""
    for iv_rank in [0, 10, 45, 70, 100]:
        m = make_metrics(iv_rank_proxy=float(iv_rank))
        score = compute_score(m)
        assert 0.0 <= score <= 100.0, f"Score hors plage pour IV Rank={iv_rank}: {score}"


# ── T5 : Score événementiel ───────────────────────────────────────────────────

def test_event_score_factor_115():
    """T5 : factor = 1.15 → composante events = 0.65."""
    assert _score_events(1.15) == pytest.approx(0.65)


def test_event_score_factor_10():
    """T5 : factor = 1.0 (pas d'événement) → composante events = 0.50."""
    assert _score_events(1.0) == pytest.approx(0.50)


def test_event_score_factor_04():
    """T5 : factor = 0.4 → composante events = 0.0 (clippé)."""
    assert _score_events(0.4) == pytest.approx(0.0)


def test_event_bonus_in_score():
    """Un événement en sweet zone améliore le score composé."""
    no_event = make_metrics(event_score_factor=1.0)
    with_event = make_metrics(event_score_factor=1.10)
    assert compute_score(with_event) > compute_score(no_event)


# ── Tests filtres éliminatoires ───────────────────────────────────────────────

def test_no_disqualification_for_good_metrics():
    """Métriques correctes → aucune disqualification."""
    metrics = make_metrics(
        iv_atm_near=0.30,
        iv_atm_far=0.28,
        avg_bid_ask_spread_pct=0.03,
        avg_volume_near=500.0,
        avg_volume_far=400.0,
        avg_oi_near=2000.0,
        avg_oi_far=1500.0,
        strike_count_near=15,
        strike_count_far=12,
    )
    assert check_disqualification(metrics) is None


def test_disqualification_iv_missing():
    """IV = 0 → disqualifié."""
    metrics = make_metrics(iv_atm_near=0.0)
    assert check_disqualification(metrics) == "iv_data_missing"


def test_disqualification_critical_in_danger():
    """FOMC en danger zone → disqualifié."""
    fomc = MarketEvent(
        date=date.today() + timedelta(days=5),
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )
    metrics = make_metrics(events_in_danger_zone=[fomc])
    assert check_disqualification(metrics) == "critical_event_in_near"
