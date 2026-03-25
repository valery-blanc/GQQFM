"""
Tests unitaires pour screener/event_filter.py, screener/stock_filter.py,
et screener/options_analyzer.py (select_expirations).
Tous les tests sont mockés — aucun accès réseau.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from events.calendar import EventCalendar
from events.models import EventImpact, EventScope, MarketEvent
from screener.event_filter import filter_by_events
from screener.options_analyzer import select_expirations
from screener.scorer import check_disqualification
from screener.models import OptionsMetrics


# ── T6 : Spread ──────────────────────────────────────────────────────────────

def test_spread_disqualified():
    """T6 : spread > 10% → disqualifié."""
    metrics = _make_metrics(avg_bid_ask_spread_pct=0.15)
    assert check_disqualification(metrics) == "spread_too_wide"


def test_spread_qualified():
    """T6 : spread = 5% → qualifié (si autres critères OK)."""
    metrics = _make_metrics(avg_bid_ask_spread_pct=0.05)
    reason = check_disqualification(metrics)
    assert reason not in (None, "spread_too_wide") or reason is None


# ── T7 : Earnings dans la fenêtre ────────────────────────────────────────────

def test_earnings_too_close_eliminated():
    """T7 : earnings dans 10 jours → éliminé si near_max = 21 jours."""
    symbols = ["AAPL"]
    earnings_10j = date.today() + timedelta(days=10)

    with patch("screener.event_filter.get_earnings_date", return_value=earnings_10j), \
         patch("screener.event_filter.get_ex_div_date", return_value=None):
        passed, _, _ = filter_by_events(symbols, near_max_days=21, earnings_buffer=2)

    assert "AAPL" not in passed


def test_earnings_far_enough_kept():
    """T7 : earnings dans 80 jours → conservé."""
    symbols = ["AAPL"]
    earnings_80j = date.today() + timedelta(days=80)

    with patch("screener.event_filter.get_earnings_date", return_value=earnings_80j), \
         patch("screener.event_filter.get_ex_div_date", return_value=None):
        passed, _, _ = filter_by_events(symbols, near_max_days=21, earnings_buffer=2)

    assert "AAPL" in passed


def test_etf_always_passes():
    """ETFs passent toujours le filtre événements (pas d'earnings)."""
    symbols = ["SPY"]

    with patch("screener.event_filter.get_ex_div_date", return_value=None):
        passed, earnings, _ = filter_by_events(symbols, near_max_days=21)

    assert "SPY" in passed
    assert earnings["SPY"] is None


# ── T8 : CRITICAL event en near zone ─────────────────────────────────────────

def test_critical_event_near_disqualified():
    """T8 : FOMC dans 5 jours → disqualifié si dans la danger zone."""
    fomc = MarketEvent(
        date=date.today() + timedelta(days=5),
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )
    metrics = _make_metrics(events_in_danger_zone=[fomc])
    assert check_disqualification(metrics) == "critical_event_in_near"


def test_high_event_near_not_disqualified():
    """HIGH (pas CRITICAL) dans danger zone → pas disqualifié (mais pénalité factor)."""
    cpi = MarketEvent(
        date=date.today() + timedelta(days=5),
        name="CPI",
        impact=EventImpact.HIGH,
        scope=EventScope.MACRO,
    )
    metrics = _make_metrics(events_in_danger_zone=[cpi])
    reason = check_disqualification(metrics)
    assert reason != "critical_event_in_near"


# ── T9 : select_expirations avec FOMC en sweet zone ──────────────────────────

def test_select_expirations_prefers_fomc_sweet():
    """T9 : FOMC j30 → la paire optimale capture FOMC dans la sweet zone."""
    today = date(2026, 3, 25)
    fomc_date = today + timedelta(days=30)  # FOMC dans 30 jours

    fomc_event = MarketEvent(
        date=fomc_date,
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )

    cal = EventCalendar()
    cal._events = [fomc_event]
    cal._loaded = True

    # Expirations simulées : 7j, 14j, 21j (near range), 35j, 45j, 60j (far range)
    expirations = [
        today + timedelta(days=d)
        for d in [7, 14, 21, 35, 45, 60]
    ]
    near_range = (5, 21)
    far_range = (25, 70)

    with patch("screener.options_analyzer.date") as mock_date:
        mock_date.today.return_value = today
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        near_exp, far_exp = select_expirations(
            expirations, near_range, far_range, cal, today
        )

    assert near_exp is not None
    assert far_exp is not None

    # Le FOMC (j30) doit être dans la sweet zone [near+1, far]
    near_days = (near_exp - today).days
    far_days = (far_exp - today).days
    fomc_day = (fomc_date - today).days

    assert near_days < fomc_day <= far_days, (
        f"FOMC (j{fomc_day}) doit être dans sweet zone [j{near_days+1}, j{far_days}]"
    )


def test_select_expirations_tiebreak_spread():
    """Sans événement (tous factor=1.0), préférer le plus grand écart far-near."""
    today = date(2026, 3, 25)
    expirations = [
        today + timedelta(days=d)
        for d in [7, 14, 28, 45, 60]
    ]

    cal = EventCalendar()
    cal._events = []
    cal._loaded = True

    near_exp, far_exp = select_expirations(
        expirations, (5, 21), (25, 70), cal, today
    )

    spread = (far_exp - near_exp).days
    # L'écart maximum possible : near=7j, far=60j → écart=53j
    # near=14j, far=60j → 46j ; near=7j, far=45j → 38j
    max_possible_near = today + timedelta(days=7)
    max_possible_far = today + timedelta(days=60)
    max_spread = (max_possible_far - max_possible_near).days

    assert spread == max_spread, f"Écart attendu {max_spread}j, obtenu {spread}j"


def test_select_expirations_no_valid_pair():
    """Si aucune paire valide, retourne (None, None)."""
    today = date(2026, 3, 25)
    # Seulement des expirations dans la near_range, pas de far
    expirations = [today + timedelta(days=7), today + timedelta(days=14)]

    cal = EventCalendar()
    cal._events = []
    cal._loaded = True

    near_exp, far_exp = select_expirations(
        expirations, (5, 21), (25, 70), cal, today
    )

    assert near_exp is None
    assert far_exp is None


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_metrics(**overrides) -> OptionsMetrics:
    defaults = dict(
        symbol="TEST",
        spot_price=100.0,
        iv_atm_near=0.30,
        iv_atm_far=0.28,
        hv30=0.25,
        iv_rank_proxy=45.0,
        term_structure_ratio=0.93,
        avg_bid_ask_spread_pct=0.03,
        avg_volume_near=500.0,
        avg_volume_far=400.0,
        avg_oi_near=2000.0,
        avg_oi_far=1500.0,
        strike_count_near=15,
        strike_count_far=12,
        weekly_count=2,
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
