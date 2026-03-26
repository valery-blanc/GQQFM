"""Tests de _select_event_pairs — algorithme 4 étapes (FEAT-006)."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from engine.combinator import _select_event_pairs


TODAY = date.today()


def make_expirations(days_list: list[int]) -> list[date]:
    return [TODAY + timedelta(days=d) for d in sorted(days_list)]


def make_calendar(critical_days: list[int] = None, moderate_days: list[int] = None):
    """
    Crée un mock EventCalendar.

    critical_days : liste de jours (depuis today) où un event CRITICAL tombe.
    moderate_days : liste de jours (depuis today) où un event MODERATE tombe.
    """
    critical_dates = {TODAY + timedelta(days=d) for d in (critical_days or [])}
    moderate_dates = {TODAY + timedelta(days=d) for d in (moderate_days or [])}

    def classify(near, far):
        danger_events = []
        sweet_events = []

        for ev_date in critical_dates:
            mock_ev = MagicMock()
            mock_ev.name = "NFP"
            mock_ev.date = ev_date
            mock_ev.impact = MagicMock()
            mock_ev.impact.name = "CRITICAL"
            if TODAY <= ev_date <= near:
                danger_events.append(mock_ev)
            elif near < ev_date <= far:
                sweet_events.append(mock_ev)

        for ev_date in moderate_dates:
            mock_ev = MagicMock()
            mock_ev.name = "CPI"
            mock_ev.date = ev_date
            mock_ev.impact = MagicMock()
            mock_ev.impact.name = "HIGH"
            if TODAY <= ev_date <= near:
                danger_events.append(mock_ev)
            elif near < ev_date <= far:
                sweet_events.append(mock_ev)

        has_critical_in_danger = any(
            ev.impact.name == "CRITICAL" for ev in danger_events
        )
        factor = 1.15 if sweet_events else 0.7 if has_critical_in_danger else 1.0

        return {
            "danger_zone": danger_events,
            "sweet_zone": sweet_events,
            "has_critical_in_danger": has_critical_in_danger,
            "event_score_factor": factor,
        }

    cal = MagicMock()
    cal.classify_events_for_pair.side_effect = classify
    return cal


NEAR_RANGE = (5, 21)
FAR_RANGE = (25, 70)


# ── Test 1 — Étape 1 suffit (cas normal) ─────────────────────────────────────

def test_step1_normal_fomc_sweet_zone():
    """FOMC en sweet zone → sélection normale à l'étape 1, pas de warning."""
    exps = make_expirations([5, 12, 19, 33, 47])
    cal = make_calendar(moderate_days=[25])  # CPI at day 25, between near=19 and far=33

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    assert len(results) > 0
    for near, far, factor, sweet_names, warning in results:
        assert warning is None
        # near must be in normal range
        assert 5 <= (near - TODAY).days <= 21
        assert 25 <= (far - TODAY).days <= 70


# ── Test 2 — Étape 1 suffit, near juste avant le CRITICAL ────────────────────

def test_step1_nfp_just_after_near():
    """NFP day 8 → near=5j et near=7j valides (NFP hors danger), near=8j exclu."""
    exps = make_expirations([5, 7, 8, 10, 33, 47])
    cal = make_calendar(critical_days=[8])

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    assert len(results) > 0
    # All returned near expirations must be day 5 or 7 (not day 8 or 10)
    for near, far, factor, sweet_names, warning in results:
        near_days = (near - TODAY).days
        assert near_days in (5, 7), f"near={near_days}j should be 5 or 7"
        assert warning is None


# ── Test 3 — Étape 2 nécessaire (extension near) ─────────────────────────────

def test_step2_near_extension():
    """Toutes les near normales bloquées par NFP → fallback near=4j avec warning."""
    exps = make_expirations([4, 10, 15, 33, 47])
    cal = make_calendar(critical_days=[8])
    # near=10j: NFP day 8 IN danger → blocked
    # near=15j: NFP day 8 IN danger → blocked
    # near=4j: NFP day 8 NOT in [today, today+4] → safe, but below normal range

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    assert len(results) > 0
    for near, far, factor, sweet_names, warning in results:
        near_days = (near - TODAY).days
        assert near_days == 4
        assert warning is not None
        assert "4j" in warning or "court" in warning.lower()


# ── Test 4 — Étape 4 nécessaire (dernier recours) ────────────────────────────

def test_step4_last_resort():
    """Très peu d'expirations, NFP bloque tout → dernier recours avec warning explicite."""
    exps = make_expirations([10, 33])
    cal = make_calendar(critical_days=[8])
    # near=10j: NFP day 8 IN danger → blocked everywhere
    # No near below 5j, no far beyond 70j → step 2 and 3 fail

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    assert len(results) > 0
    for near, far, factor, sweet_names, warning in results:
        assert warning is not None
        assert "⚠" in warning
        assert "NFP" in warning or "CRITICAL" in warning


# ── Test 5 — Aucun événement ──────────────────────────────────────────────────

def test_no_events():
    """Aucun événement → toutes les paires normales à factor=1.0, pas de warning."""
    exps = make_expirations([5, 12, 33, 47])
    cal = make_calendar()

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    assert len(results) > 0
    for near, far, factor, sweet_names, warning in results:
        assert warning is None
        assert factor == 1.0
        assert (near - TODAY).days >= 5
        assert (far - TODAY).days >= 25


# ── Test 6 — Rétro-compatibilité (event_calendar=None géré par generate_combinations) ──

def test_step1_returns_5_tuples():
    """Vérification que _select_event_pairs retourne bien des 5-tuples."""
    exps = make_expirations([7, 14, 30, 50])
    cal = make_calendar()

    results = _select_event_pairs(exps, NEAR_RANGE, FAR_RANGE, cal)

    for item in results:
        assert len(item) == 5
        near, far, factor, sweet_names, warning = item
        assert isinstance(near, date)
        assert isinstance(far, date)
        assert isinstance(factor, float)
        assert isinstance(sweet_names, list)
        assert warning is None or isinstance(warning, str)
