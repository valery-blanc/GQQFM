"""
EventCalendar — source unique d'événements de volatilité.
Utilisé par le screener (FEAT-004) et le scanner (merge ultérieur).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import config
from events.fomc_calendar import get_fomc_events
from events.models import EventImpact, MarketEvent

logger = logging.getLogger(__name__)


class EventCalendar:
    """
    Calendrier unifié d'événements macro + micro.

    Usage typique :
        cal = EventCalendar(finnhub_api_key="xxx")
        cal.load(date.today(), date.today() + timedelta(days=90))
        info = cal.classify_events_for_pair(near_expiry, far_expiry)
    """

    def __init__(self, finnhub_api_key: str | None = None) -> None:
        # Priorité : paramètre > variable d'env > config.py
        self._api_key = (
            finnhub_api_key
            or os.environ.get("FINNHUB_API_KEY")
            or config.FINNHUB_API_KEY
        )
        self._events: list[MarketEvent] = []
        self._loaded = False

    # ── chargement ──────────────────────────────────────────────────────────

    def load(self, from_date: date, to_date: date) -> None:
        """
        Charge les événements pour la plage [from_date, to_date].

        1. FOMC statiques (toujours chargés).
        2. Finnhub (si clé API disponible) — fallback silencieux si erreur.
        Déduplique les événements (même nom + même date).
        """
        fomc_events = get_fomc_events(from_date, to_date)
        finnhub_events: list[MarketEvent] = []

        if self._api_key:
            try:
                from events.finnhub_calendar import fetch_macro_events
                finnhub_events = fetch_macro_events(from_date, to_date, self._api_key)
                logger.debug("Finnhub : %d événements chargés", len(finnhub_events))
            except Exception as exc:
                logger.warning("Finnhub indisponible (%s) — FOMC statiques uniquement", exc)

        # Fusion avec déduplication sur (name, date)
        merged: dict[tuple[str, date], MarketEvent] = {}
        for ev in fomc_events + finnhub_events:
            key = (ev.name, ev.date)
            if key not in merged:
                merged[key] = ev

        self._events = sorted(merged.values(), key=lambda e: e.date)
        self._loaded = True

    # ── accès ────────────────────────────────────────────────────────────────

    def get_events_in_range(
        self,
        start: date,
        end: date,
        min_impact: EventImpact = EventImpact.MODERATE,
    ) -> list[MarketEvent]:
        """Retourne les événements dans [start, end] avec impact >= min_impact."""
        return [
            ev for ev in self._events
            if start <= ev.date <= end and ev.impact.value >= min_impact.value
        ]

    def classify_events_for_pair(
        self,
        near_expiry: date,
        far_expiry: date,
    ) -> dict:
        """
        Classifie les événements pour une paire d'expirations (near, far).

        Danger zone : [today, near_expiry]   → pénalités multiplicatives
        Sweet zone  : [near_expiry+1, far_expiry] → bonus additifs

        Retourne:
            danger_zone          : list[MarketEvent]
            sweet_zone           : list[MarketEvent]
            has_critical_in_danger : bool
            has_high_in_sweet    : bool
            event_score_factor   : float
        """
        today = date.today()
        danger_events = self.get_events_in_range(today, near_expiry)
        sweet_events = self.get_events_in_range(
            near_expiry + timedelta(days=1), far_expiry
        )

        factor = 1.0

        # Pénalités danger zone (multiplicatives, composées)
        for ev in danger_events:
            if ev.impact in (EventImpact.CRITICAL, EventImpact.HIGH):
                factor *= config.EVENT_PENALTY_CRITICAL_IN_NEAR   # 0.4
            elif ev.impact == EventImpact.MODERATE:
                factor *= config.EVENT_PENALTY_MODERATE_IN_NEAR   # 0.7

        # Bonus sweet zone (additifs, plafonnés)
        sweet_bonus = 0.0
        for ev in sweet_events:
            if ev.impact in (EventImpact.CRITICAL, EventImpact.HIGH):
                sweet_bonus += config.EVENT_BONUS_HIGH_IN_SWEET    # 0.05
            elif ev.impact == EventImpact.MODERATE:
                sweet_bonus += config.EVENT_BONUS_MODERATE_IN_SWEET  # 0.02

        factor += min(sweet_bonus, config.EVENT_BONUS_CAP)         # plafond +0.15

        return {
            "danger_zone": danger_events,
            "sweet_zone": sweet_events,
            "has_critical_in_danger": any(
                ev.impact == EventImpact.CRITICAL for ev in danger_events
            ),
            "has_high_in_sweet": any(
                ev.impact in (EventImpact.CRITICAL, EventImpact.HIGH)
                for ev in sweet_events
            ),
            "event_score_factor": factor,
        }

    @property
    def is_loaded(self) -> bool:
        return self._loaded
