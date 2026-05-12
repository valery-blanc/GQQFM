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
from screener.options_analyzer import compute_atm_liquidity, select_expirations
from screener.scorer import check_disqualification
from screener.models import OptionsMetrics


# ── T6 : Spread ATM-ciblé (FEAT-023 § Étape 2) ──────────────────────────────

def test_spread_disqualified_atm():
    """spread ATM > 12% → disqualifié."""
    metrics = _make_metrics(spread_pct_atm_near=0.15, spread_pct_atm_far=0.10)
    assert check_disqualification(metrics) == "spread_too_wide"


def test_spread_qualified_atm():
    """spread ATM = 5% → qualifié sur ce critère."""
    metrics = _make_metrics(spread_pct_atm_near=0.05, spread_pct_atm_far=0.04)
    reason = check_disqualification(metrics)
    assert reason != "spread_too_wide"


def test_spread_legacy_fallback():
    """Si champs ATM non renseignés, fallback sur avg_bid_ask_spread_pct legacy."""
    metrics = _make_metrics(
        spread_pct_atm_near=0.0,
        spread_pct_atm_far=0.0,
        avg_bid_ask_spread_pct=0.15,
    )
    assert check_disqualification(metrics) == "spread_too_wide"


def test_volume_p25_atm_disqualifies():
    """Volume p25 ATM near < 20 → disqualifié si volume_median > 0 (en séance)."""
    metrics = _make_metrics(
        volume_atm_median_near=500.0,  # > 0 → la règle s'active
        volume_atm_p25_near=10.0,      # < 20 → fail
    )
    assert check_disqualification(metrics) == "no_volume_atm"


def test_volume_p25_atm_off_hours_skipped():
    """Hors-séance (volume_median=0), la règle no_volume_atm est skip."""
    metrics = _make_metrics(
        volume_atm_median_near=0.0,
        volume_atm_p25_near=0.0,
    )
    reason = check_disqualification(metrics)
    assert reason != "no_volume_atm"


def test_oi_p25_atm_sentinel_skipped():
    """Sentinelle OI_UNAVAILABLE (999_999) → règle no_oi_atm désactivée."""
    metrics = _make_metrics(
        oi_atm_p25_near=999_999.0,
        oi_atm_p25_far=999_999.0,
    )
    reason = check_disqualification(metrics)
    assert reason != "no_oi_atm"


def test_strikes_atm_too_few():
    """< 4 strikes dans la zone ATM ±band → disqualifié."""
    metrics = _make_metrics(
        strike_count_atm_near=3,
        strike_count_atm_far=8,
    )
    assert check_disqualification(metrics) == "not_enough_strikes_atm"


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

def test_critical_macro_event_does_not_disqualify():
    """BUG-028 : FOMC (MACRO CRITICAL) ne disqualifie pas — pénalité score uniquement."""
    fomc = MarketEvent(
        date=date.today() + timedelta(days=5),
        name="FOMC",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MACRO,
    )
    metrics = _make_metrics(events_in_danger_zone=[fomc])
    assert check_disqualification(metrics) is None


def test_critical_micro_event_disqualifies():
    """BUG-028 : event MICRO CRITICAL (FDA) en danger zone → disqualifié."""
    fda = MarketEvent(
        date=date.today() + timedelta(days=5),
        name="FDA",
        impact=EventImpact.CRITICAL,
        scope=EventScope.MICRO,
        symbol="TEST",
    )
    metrics = _make_metrics(events_in_danger_zone=[fda])
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


# ── compute_atm_liquidity (FEAT-023 § Étape 2) ───────────────────────────────

def _make_chain(strikes_volumes_oi_spread: list[tuple]) -> pd.DataFrame:
    """Helper : construit une chaîne mock à partir de tuples (strike, vol, oi, spread_$, mid_price)."""
    rows = []
    for strike, vol, oi, spread, mid in strikes_volumes_oi_spread:
        rows.append({
            "strike": strike,
            "volume": vol,
            "openInterest": oi,
            "bid": max(mid - spread / 2, 0.01),
            "ask": mid + spread / 2,
        })
    return pd.DataFrame(rows)


def test_atm_liquidity_excludes_otm_wings():
    """
    Chaîne avec ATM (95-105) liquide + wings (50-90, 110-150) illiquides.
    compute_atm_liquidity doit ignorer les wings et retourner les stats ATM.
    """
    spot = 100.0
    # ATM (95, 100, 105) : volume élevé, spread serré
    # Wings (60, 80, 120, 140) : volume nul, spread énorme en %
    rows = [
        (60, 0, 0, 0.50, 0.10),
        (80, 0, 0, 0.20, 0.30),
        (95, 1000, 5000, 0.05, 5.00),
        (100, 2000, 10000, 0.05, 4.00),
        (105, 800, 4000, 0.05, 3.50),
        (120, 0, 0, 0.30, 0.20),
        (140, 0, 0, 0.50, 0.05),
    ]
    chain = _make_chain(rows)
    atm = compute_atm_liquidity(chain, None, spot=spot, atm_band_pct=0.10)

    assert atm.strike_count == 3
    # Volume médian ATM ≈ 1000, p25 ≥ 800 (les 3 strikes 800/1000/2000)
    assert atm.volume_median == pytest.approx(1000.0)
    assert atm.volume_p25 >= 800.0
    # Spread % ATM faible : 0.05 / ~4 ≈ 1.25 %, médiane sur 3 valeurs
    assert atm.spread_pct_median < 0.05


def test_atm_liquidity_calls_and_puts_combined():
    """
    Calls et puts sont concaténés. Un strike commun apparaît 2 fois → poids
    accru légitime (les deux côtés sont disponibles).
    """
    spot = 100.0
    calls = _make_chain([(95, 100, 500, 0.10, 3.0), (100, 200, 800, 0.10, 2.0)])
    puts = _make_chain([(95, 150, 600, 0.10, 1.5), (100, 250, 900, 0.10, 2.5)])
    atm = compute_atm_liquidity(calls, puts, spot=spot, atm_band_pct=0.10)

    # 2 strikes distincts, 4 lignes au total
    assert atm.strike_count == 2
    # Médiane des 4 volumes : (100, 150, 200, 250) → médiane = 175
    assert atm.volume_median == pytest.approx(175.0)


def test_atm_liquidity_p25_detects_weak_leg():
    """4 strikes ATM dont 2 à volume très faible → p25 capture les jambes faibles.

    Note : `vol.quantile(0.25)` (pandas linear interp) sur 4 valeurs triees
    [a, b, c, d] donne `a + 0.75 * (b - a)`. Avec une seule jambe faible
    (5, 400, 500, 600), p25 = 301.25 (interpole vers le 2e + bas). Il faut
    2 jambes faibles pour que p25 reste bas.
    """
    spot = 100.0
    rows = [
        (95, 5, 100, 0.10, 3.0),       # jambe faible #1
        (98, 20, 500, 0.10, 2.5),      # jambe faible #2 (volume bas)
        (102, 400, 1800, 0.05, 2.4),
        (105, 600, 2200, 0.05, 2.0),
    ]
    chain = _make_chain(rows)
    atm = compute_atm_liquidity(chain, None, spot=spot, atm_band_pct=0.10)
    # p25 sur (5, 20, 400, 600) ≈ 5 + 0.75 × 15 = 16.25 → < 20 disqualifie
    assert atm.volume_p25 < 50.0


def test_atm_liquidity_off_hours_oi_sentinel():
    """OI globalement à 0 (hors-séance) → sentinelle 999_999 sur p25 et median."""
    spot = 100.0
    rows = [(s, 0, 0, 0.05, 3.0) for s in (95, 100, 105)]
    chain = _make_chain(rows)
    atm = compute_atm_liquidity(chain, None, spot=spot, atm_band_pct=0.10)
    assert atm.oi_p25 >= 999_000
    assert atm.oi_median >= 999_000


def test_atm_liquidity_empty_atm_band():
    """Aucun strike dans la band → liquidité 'vide' (spread élevé pour disqualifier)."""
    spot = 100.0
    # Tous les strikes sont OTM extrêmes
    chain = _make_chain([(50, 100, 500, 0.05, 0.5), (200, 100, 500, 0.05, 0.1)])
    atm = compute_atm_liquidity(chain, None, spot=spot, atm_band_pct=0.10)
    assert atm.strike_count == 0
    assert atm.spread_pct_median == pytest.approx(0.20)


# ── _score_tradability ────────────────────────────────────────────────────────

def test_tradability_score_penalizes_wide_spread():
    """Spread ATM 15 % moyen → cost 4×15%=60% → score = 0."""
    from screener.scorer import _score_tradability
    metrics = _make_metrics(spread_pct_atm_near=0.15, spread_pct_atm_far=0.15)
    assert _score_tradability(metrics) == pytest.approx(0.0)


def test_tradability_score_rewards_tight_spread():
    """Spread ATM 1 % → cost 4 % → score = 1.0."""
    from screener.scorer import _score_tradability
    metrics = _make_metrics(spread_pct_atm_near=0.01, spread_pct_atm_far=0.01)
    assert _score_tradability(metrics) == pytest.approx(1.0)


def test_tradability_score_legacy_neutral():
    """Champs ATM non renseignés → score neutre 0.5."""
    from screener.scorer import _score_tradability
    metrics = _make_metrics(spread_pct_atm_near=0.0, spread_pct_atm_far=0.0)
    assert _score_tradability(metrics) == pytest.approx(0.5)


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
