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

    # ── IV Rank 52 semaines (FEAT-023 § Étape 3, approximé HV-based) ─────────
    iv_rank_52w: float = 50.0       # 0-100, par défaut neutre

    # ── Liquidité ATM ciblée (FEAT-023 § Étape 2) ────────────────────────────
    # Mesurée sur calls + puts dans la zone ATM ±SCREENER_ATM_BAND_PCT (10 %).
    # Représente ce que les templates 4 jambes utilisent réellement.
    # Valeurs par défaut = neutres pour ne pas casser les anciens tests.
    spread_pct_atm_near: float = 0.0       # spread % médian ATM near (calls+puts)
    spread_pct_atm_far: float = 0.0        # spread % médian ATM far
    spread_dollar_atm_near: float = 0.0    # spread $ médian ATM near
    spread_dollar_atm_far: float = 0.0     # spread $ médian ATM far
    volume_atm_median_near: float = 0.0    # volume médian sur ATM near
    volume_atm_median_far: float = 0.0
    volume_atm_p25_near: float = 0.0       # 25e percentile = jambe la plus faible
    volume_atm_p25_far: float = 0.0
    oi_atm_median_near: float = 0.0
    oi_atm_median_far: float = 0.0
    oi_atm_p25_near: float = 0.0
    oi_atm_p25_far: float = 0.0
    strike_count_atm_near: int = 0         # nb strikes distincts dans ATM±band
    strike_count_atm_far: int = 0
    mid_price_atm_near: float = 0.0        # mid moyen ATM (sert au score tradabilité)
    mid_price_atm_far: float = 0.0

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
    # FEAT-023 Étape 3 — métriques comportementales et stratégie
    iv_rank_52w: float = 50.0           # IV Rank approximé 52w (0-100)
    atr_pct: float = 0.0                # ATR_20 / spot
    hv_ratio_20_60: float = 1.0         # HV20/HV60 (>1.2 = vol qui accélère)
    autocorr_1d: float = 0.0            # auto-corr lag-1 (≤0 = mean revert)
    profile: str = "calendar"           # "calendar" | "ric" — score calculé selon ce profil
