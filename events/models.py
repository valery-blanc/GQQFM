"""Modèles de données pour le calendrier d'événements de marché."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class EventImpact(Enum):
    CRITICAL = 3    # FOMC rate decision, NFP
    HIGH = 2        # CPI, GDP, PCE Core
    MODERATE = 1    # FOMC Minutes, ISM, PPI


class EventScope(Enum):
    MACRO = "macro"   # tous les sous-jacents (indices US)
    MICRO = "micro"   # un seul sous-jacent (earnings, FDA)


@dataclass
class MarketEvent:
    date: date
    name: str                       # nom court (ex: "FOMC", "NFP", "CPI")
    impact: EventImpact
    scope: EventScope
    symbol: str | None = None       # None pour les événements macro
