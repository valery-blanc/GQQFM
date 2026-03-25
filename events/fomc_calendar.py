"""
Table statique des réunions FOMC.
À mettre à jour annuellement (dates publiées par la Fed en début d'année).
Source : https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""

from datetime import date

from events.models import EventImpact, EventScope, MarketEvent

# Décisions de taux (impact CRITICAL)
FOMC_DECISIONS_2026: list[str] = [
    "2026-01-28",
    "2026-03-18",
    "2026-05-06",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-11-04",
    "2026-12-16",
]

# Publication des minutes (impact MODERATE, ~3 semaines après la décision)
FOMC_MINUTES_2026: list[str] = [
    "2026-02-19",
    "2026-04-09",
    "2026-05-27",
    "2026-07-08",
    "2026-08-19",
    "2026-10-07",
    "2026-11-25",
]


def get_fomc_events(from_date: date, to_date: date) -> list[MarketEvent]:
    """Retourne les événements FOMC statiques dans la plage [from_date, to_date]."""
    events: list[MarketEvent] = []

    for ds in FOMC_DECISIONS_2026:
        d = date.fromisoformat(ds)
        if from_date <= d <= to_date:
            events.append(MarketEvent(
                date=d,
                name="FOMC",
                impact=EventImpact.CRITICAL,
                scope=EventScope.MACRO,
            ))

    for ds in FOMC_MINUTES_2026:
        d = date.fromisoformat(ds)
        if from_date <= d <= to_date:
            events.append(MarketEvent(
                date=d,
                name="FOMC Minutes",
                impact=EventImpact.MODERATE,
                scope=EventScope.MACRO,
            ))

    return events
