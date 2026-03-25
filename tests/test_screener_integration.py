"""
Test d'intégration du pipeline screener (T14).
Pipeline complet mocké sur 10 tickers fictifs.
Vérifie : top 3 correct, FOMC en sweet zone mieux classés.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from events.calendar import EventCalendar
from events.models import EventImpact, EventScope, MarketEvent
from screener.models import OptionsMetrics, ScreenerResult
from screener.scorer import check_disqualification, compute_score, to_screener_result


# ── jeu de données de test ────────────────────────────────────────────────────

TODAY = date(2026, 3, 25)
FOMC_DATE = TODAY + timedelta(days=32)  # FOMC dans la sweet zone (near=14j, far=45j)


def _make_metrics(symbol: str, **overrides) -> OptionsMetrics:
    """Crée des OptionsMetrics avec des valeurs par défaut raisonnables."""
    defaults = dict(
        symbol=symbol,
        spot_price=200.0,
        iv_atm_near=0.28,
        iv_atm_far=0.25,
        hv30=0.22,
        iv_rank_proxy=45.0,
        term_structure_ratio=0.89,
        avg_bid_ask_spread_pct=0.03,
        avg_volume_near=3000.0,
        avg_volume_far=2500.0,
        avg_oi_near=8000.0,
        avg_oi_far=6000.0,
        strike_count_near=22,
        strike_count_far=18,
        weekly_count=3,
        near_expiry=TODAY + timedelta(days=14),
        far_expiry=TODAY + timedelta(days=45),
        events_in_danger_zone=[],
        events_in_sweet_zone=[],
        event_score_factor=1.0,
        next_earnings_date=None,
        next_ex_div_date=None,
        disqualification_reason=None,
    )
    defaults.update(overrides)
    return OptionsMetrics(**defaults)


# ── T14 : pipeline complet mocké ─────────────────────────────────────────────

def test_pipeline_top3_and_fomc_bonus():
    """
    T14 : pipeline de scoring sur 10 tickers mockés.
    Vérifie :
    - le top 3 contient les tickers avec les meilleures métriques
    - les tickers avec FOMC en sweet zone sont mieux classés que leurs équivalents sans événement
    """
    fomc_event = MarketEvent(
        date=FOMC_DATE,
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )

    # 10 tickers avec caractéristiques différentes
    all_metrics = [
        # Excellent avec FOMC en sweet zone
        _make_metrics("ALPHA", iv_rank_proxy=45.0, term_structure_ratio=0.88,
                      avg_bid_ask_spread_pct=0.02, avg_volume_near=5000.0, avg_volume_far=4500.0,
                      avg_oi_near=15000.0, avg_oi_far=12000.0, weekly_count=4,
                      events_in_sweet_zone=[fomc_event], event_score_factor=1.05),

        # Excellent sans événement
        _make_metrics("BETA", iv_rank_proxy=45.0, term_structure_ratio=0.88,
                      avg_bid_ask_spread_pct=0.02, avg_volume_near=5000.0, avg_volume_far=4500.0,
                      avg_oi_near=15000.0, avg_oi_far=12000.0, weekly_count=4,
                      event_score_factor=1.0),

        # Bon
        _make_metrics("GAMMA", iv_rank_proxy=38.0, term_structure_ratio=0.92,
                      avg_bid_ask_spread_pct=0.04, avg_volume_near=2000.0, avg_volume_far=1800.0,
                      avg_oi_near=5000.0, avg_oi_far=4000.0, weekly_count=2),

        # Moyen avec high IV rank (pénalité ×0.5)
        _make_metrics("DELTA", iv_rank_proxy=80.0, term_structure_ratio=0.90,
                      avg_bid_ask_spread_pct=0.03),

        # Mauvais spread
        _make_metrics("EPSILON", avg_bid_ask_spread_pct=0.09),

        # Mauvais term structure
        _make_metrics("ZETA", term_structure_ratio=1.28),

        # Peu de volume
        _make_metrics("ETA", avg_volume_near=150.0, avg_volume_far=120.0),

        # Backwardation (pénalité ×0.7)
        _make_metrics("THETA", term_structure_ratio=1.20, iv_rank_proxy=40.0),

        # Bon mais hors-séance (IV = 0) → sera disqualifié
        _make_metrics("IOTA", iv_atm_near=0.0, iv_atm_far=0.0),

        # Standard
        _make_metrics("KAPPA", iv_rank_proxy=50.0, term_structure_ratio=0.95,
                      avg_bid_ask_spread_pct=0.05),
    ]

    # Scorer tous les tickers (sauf disqualifiés)
    results: list[ScreenerResult] = []
    for m in all_metrics:
        reason = check_disqualification(m)
        if reason:
            m.disqualification_reason = reason
            continue
        score = compute_score(m)
        results.append(to_screener_result(m, score))

    results.sort(key=lambda r: r.score, reverse=True)

    # IOTA doit être éliminé (iv_data_missing)
    symbols_in_results = [r.symbol for r in results]
    assert "IOTA" not in symbols_in_results

    # ALPHA (FOMC sweet) doit être mieux classé que BETA (identique sans FOMC)
    alpha_rank = next(i for i, r in enumerate(results) if r.symbol == "ALPHA")
    beta_rank = next(i for i, r in enumerate(results) if r.symbol == "BETA")
    assert alpha_rank < beta_rank, (
        f"ALPHA (FOMC sweet) rang {alpha_rank} doit être < BETA rang {beta_rank}"
    )

    # Top 3 doit contenir ALPHA et BETA (les deux meilleurs)
    top3 = {r.symbol for r in results[:3]}
    assert "ALPHA" in top3
    assert "BETA" in top3

    # DELTA (pénalité IV Rank) doit être après GAMMA
    delta_rank = next(i for i, r in enumerate(results) if r.symbol == "DELTA")
    gamma_rank = next(i for i, r in enumerate(results) if r.symbol == "GAMMA")
    assert gamma_rank < delta_rank, (
        f"GAMMA rang {gamma_rank} doit être < DELTA rang {delta_rank}"
    )


def test_screener_result_fields():
    """Les champs de ScreenerResult sont bien remplis."""
    metrics = _make_metrics(
        "SPY",
        events_in_sweet_zone=[
            MarketEvent(date=TODAY + timedelta(days=20), name="FOMC",
                        impact=EventImpact.CRITICAL, scope=EventScope.MACRO)
        ],
        event_score_factor=1.05,
        next_earnings_date=None,
        next_ex_div_date=TODAY + timedelta(days=60),
    )
    score = compute_score(metrics)
    result = to_screener_result(metrics, score)

    assert result.symbol == "SPY"
    assert 0 <= result.score <= 100
    assert result.has_event_bonus is True
    assert "FOMC" in result.events_in_sweet_zone
    assert result.events_in_near_zone == []
    assert result.weekly_expiries_available is True
    assert result.weekly_count == 3
    assert result.next_ex_div_date == TODAY + timedelta(days=60)
