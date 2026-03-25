"""
Chargement des événements macro depuis l'API Finnhub.
Fallback automatique sur FOMC statiques si clé API absente ou requête échouée.
"""

from __future__ import annotations

import logging
from datetime import date

import requests

from events.models import EventImpact, EventScope, MarketEvent

logger = logging.getLogger(__name__)

# Mapping nom Finnhub → (nom court, impact)
TRACKED_EVENTS: dict[str, tuple[str, EventImpact]] = {
    "Nonfarm Payrolls":             ("NFP",         EventImpact.CRITICAL),
    "Non Farm Payrolls":            ("NFP",         EventImpact.CRITICAL),
    "CPI MoM":                      ("CPI",         EventImpact.HIGH),
    "CPI YoY":                      ("CPI",         EventImpact.HIGH),
    "Core CPI MoM":                 ("Core CPI",    EventImpact.HIGH),
    "GDP Growth Rate QoQ":          ("GDP",         EventImpact.HIGH),
    "GDP Growth Rate QoQ Adv":      ("GDP Advance", EventImpact.HIGH),
    "Core PCE Price Index MoM":     ("PCE Core",    EventImpact.HIGH),
    "Core PCE Price Index YoY":     ("PCE Core",    EventImpact.HIGH),
    "ISM Manufacturing PMI":        ("ISM Mfg",     EventImpact.MODERATE),
    "ISM Services PMI":             ("ISM Svc",     EventImpact.MODERATE),
    "PPI MoM":                      ("PPI",         EventImpact.MODERATE),
}

_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
_TIMEOUT = 10  # secondes


def fetch_macro_events(
    from_date: date,
    to_date: date,
    api_key: str,
) -> list[MarketEvent]:
    """
    Récupère les événements macro depuis Finnhub pour la plage [from_date, to_date].

    Retourne une liste de MarketEvent filtrée sur TRACKED_EVENTS.
    Lève RuntimeError si la requête échoue (l'appelant gère le fallback).
    """
    params = {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "token": api_key,
    }
    response = requests.get(_FINNHUB_URL, params=params, timeout=_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    raw_events = data.get("economicCalendar", [])

    events: list[MarketEvent] = []
    seen: set[tuple[str, date]] = set()  # déduplique (nom, date)

    for item in raw_events:
        event_name = item.get("event", "")
        if event_name not in TRACKED_EVENTS:
            continue
        if item.get("country", "").upper() != "US":
            continue

        short_name, impact = TRACKED_EVENTS[event_name]
        try:
            event_date = date.fromisoformat(item["time"][:10])
        except (KeyError, ValueError):
            continue

        key = (short_name, event_date)
        if key in seen:
            continue
        seen.add(key)

        events.append(MarketEvent(
            date=event_date,
            name=short_name,
            impact=impact,
            scope=EventScope.MACRO,
        ))

    return events
