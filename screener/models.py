"""Modèles de données pour le screener de sous-jacents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from events.models import MarketEvent


@dataclass
class OptionsMetrics:
    """
    Métriques options calculées pour un sous-jacent (usage interne au screener).
    Alimenté par options_analyzer.py avant scoring.
    """
    symbol: str
    spot_price: float

    # Volatilités
    iv_atm_near: float              # IV ATM expiration near (0 si hors-séance)
    iv_atm_far: float               # IV ATM expiration far  (0 si hors-séance)
    hv30: float                     # HV annualisée 30 jours (depuis historique)
    iv_rank_proxy: float            # 0-100, calculé depuis IV/HV

    # Term structure
    term_structure_ratio: float     # iv_atm_far / iv_atm_near (1.0 = plat)

    # Liquidité (moyenne near + far)
    avg_bid_ask_spread_pct: float
    avg_volume_near: float
    avg_volume_far: float
    avg_oi_near: float
    avg_oi_far: float

    # Densité strikes
    strike_count_near: int
    strike_count_far: int
    weekly_count: int               # nb d'expirations weeklies dans near_range

    # Expirations sélectionnées
    near_expiry: date
    far_expiry: date

    # Événements
    events_in_danger_zone: list[MarketEvent] = field(default_factory=list)
    events_in_sweet_zone: list[MarketEvent] = field(default_factory=list)
    event_score_factor: float = 1.0

    # Dividendes / earnings
    next_earnings_date: date | None = None
    next_ex_div_date: date | None = None

    # Qualification
    disqualification_reason: str | None = None


@dataclass
class ScreenerResult:
    """Résultat public retourné par UnderlyingScreener.screen()."""
    symbol: str
    score: float                        # 0-100 (score composite normalisé)
    spot_price: float
    iv_rank_proxy: float                # 0-100
    term_structure_ratio: float         # iv_far / iv_near
    avg_option_spread_pct: float        # spread moyen bid-ask en %
    avg_option_volume: float            # volume moyen (near+far)/2
    avg_open_interest: float            # OI moyen (near+far)/2
    strike_count: int                   # min(near, far) strikes disponibles
    weekly_expiries_available: bool     # weekly_count > 0
    weekly_count: int                   # nb d'expirations weeklies dans near_range
    next_earnings_date: date | None
    next_ex_div_date: date | None
    events_in_near_zone: list[str]      # noms des événements en danger zone
    events_in_sweet_zone: list[str]     # noms des événements en sweet zone
    has_event_bonus: bool               # True si sweet_zone non vide
    disqualification_reason: str | None
