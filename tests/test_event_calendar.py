"""
Tests unitaires pour events/calendar.py et events/fomc_calendar.py.
Tous les tests sont mockés (aucun accès réseau).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from events.calendar import EventCalendar
from events.fomc_calendar import get_fomc_events
from events.models import EventImpact, EventScope, MarketEvent


# ── T10 : FOMC statique chargé ───────────────────────────────────────────────

def test_fomc_static_loaded():
    """T10 : les dates FOMC 2026 sont présentes dans la table statique."""
    events = get_fomc_events(date(2026, 1, 1), date(2026, 12, 31))
    decisions = [e for e in events if e.impact == EventImpact.CRITICAL]
    minutes = [e for e in events if e.impact == EventImpact.MODERATE]

    assert len(decisions) == 8, f"Attendu 8 décisions FOMC 2026, obtenu {len(decisions)}"
    assert len(minutes) == 7, f"Attendu 7 FOMC Minutes 2026, obtenu {len(minutes)}"

    dates_decisions = {e.date for e in decisions}
    assert date(2026, 3, 18) in dates_decisions
    assert date(2026, 6, 17) in dates_decisions


def test_fomc_range_filter():
    """FOMC statique respecte la plage [from_date, to_date]."""
    events = get_fomc_events(date(2026, 3, 1), date(2026, 4, 30))
    for ev in events:
        assert date(2026, 3, 1) <= ev.date <= date(2026, 4, 30)


# ── T11 : classify sweet (FOMC entre near et far) ───────────────────────────

def test_classify_sweet_fomc():
    """T11 : FOMC entre near et far → event_score_factor > 1.0."""
    # Simuler today = 2026-03-10, near = 2026-03-15, far = 2026-03-25
    # FOMC Decision le 2026-03-18 → dans sweet zone [16 mars, 25 mars]
    fomc_event = MarketEvent(
        date=date(2026, 3, 18),
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )

    cal = EventCalendar()
    cal._events = [fomc_event]
    cal._loaded = True

    with patch("events.calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 10)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = cal.classify_events_for_pair(
            near_expiry=date(2026, 3, 15),
            far_expiry=date(2026, 3, 25),
        )

    assert result["event_score_factor"] > 1.0
    assert result["has_high_in_sweet"] is True
    assert len(result["sweet_zone"]) == 1
    assert len(result["danger_zone"]) == 0


# ── T12 : classify danger (FOMC avant near expiry) ──────────────────────────

def test_classify_danger_fomc():
    """T12 : FOMC avant near expiry → event_score_factor < 1.0."""
    fomc_event = MarketEvent(
        date=date(2026, 3, 18),
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )

    cal = EventCalendar()
    cal._events = [fomc_event]
    cal._loaded = True

    with patch("events.calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 10)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = cal.classify_events_for_pair(
            near_expiry=date(2026, 3, 21),  # FOMC (18 mars) est avant near (21 mars)
            far_expiry=date(2026, 4, 17),
        )

    assert result["event_score_factor"] < 1.0
    assert result["has_critical_in_danger"] is True
    assert len(result["danger_zone"]) == 1
    assert len(result["sweet_zone"]) == 0


# ── T13 : fallback sans Finnhub ──────────────────────────────────────────────

def test_fallback_without_finnhub():
    """T13 : EventCalendar sans clé Finnhub charge uniquement FOMC, sans erreur."""
    cal = EventCalendar(finnhub_api_key=None)

    # On s'assure qu'aucune requête réseau n'est faite
    with patch("events.calendar.os.environ.get", return_value=None):
        cal.load(date(2026, 3, 1), date(2026, 4, 30))

    assert cal.is_loaded
    events = cal.get_events_in_range(date(2026, 3, 1), date(2026, 4, 30))
    # Au moins la décision FOMC du 18 mars doit être présente
    names = [ev.name for ev in events]
    assert "FOMC" in names


def test_fallback_on_finnhub_error():
    """Si Finnhub lève une exception, on utilise uniquement FOMC statiques."""
    cal = EventCalendar(finnhub_api_key="bad_key")

    with patch("events.finnhub_calendar.requests.get", side_effect=ConnectionError("réseau")):
        cal.load(date(2026, 3, 1), date(2026, 4, 30))

    assert cal.is_loaded
    events = cal.get_events_in_range(date(2026, 3, 1), date(2026, 4, 30))
    assert any(ev.name == "FOMC" for ev in events)


# ── Tests de la formule event_score_factor ───────────────────────────────────

def test_event_factor_no_events():
    """Sans événement : factor = 1.0."""
    cal = EventCalendar()
    cal._events = []
    cal._loaded = True

    with patch("events.calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 10)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = cal.classify_events_for_pair(
            near_expiry=date(2026, 3, 25),
            far_expiry=date(2026, 4, 30),
        )

    assert result["event_score_factor"] == pytest.approx(1.0)


def test_event_factor_high_in_danger():
    """HIGH en danger zone → ×0.4."""
    cpi_event = MarketEvent(
        date=date(2026, 3, 12),
        name="CPI",
        impact=EventImpact.HIGH,
        scope=EventScope.MACRO,
    )
    cal = EventCalendar()
    cal._events = [cpi_event]
    cal._loaded = True

    with patch("events.calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 10)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = cal.classify_events_for_pair(
            near_expiry=date(2026, 3, 21),
            far_expiry=date(2026, 4, 17),
        )

    assert result["event_score_factor"] == pytest.approx(0.4)


def test_event_factor_bonus_cap():
    """3 événements HIGH en sweet zone → bonus plafonné à +0.15."""
    sweet_events = [
        MarketEvent(date=date(2026, 4, 1), name="CPI", impact=EventImpact.HIGH, scope=EventScope.MACRO),
        MarketEvent(date=date(2026, 4, 3), name="NFP", impact=EventImpact.CRITICAL, scope=EventScope.MACRO),
        MarketEvent(date=date(2026, 4, 10), name="PCE Core", impact=EventImpact.HIGH, scope=EventScope.MACRO),
    ]
    cal = EventCalendar()
    cal._events = sweet_events
    cal._loaded = True

    with patch("events.calendar.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 10)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        result = cal.classify_events_for_pair(
            near_expiry=date(2026, 3, 25),
            far_expiry=date(2026, 4, 17),
        )

    # 3 × 0.05 = 0.15, mais plafonné → 1.0 + 0.15 = 1.15
    assert result["event_score_factor"] == pytest.approx(1.15)
