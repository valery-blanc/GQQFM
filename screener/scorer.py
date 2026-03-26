"""
Score composite pour le screener de sous-jacents.
5 composantes + pénalités multiplicatives + filtres éliminatoires.
"""

from __future__ import annotations

import math
from datetime import date

import config
from events.models import EventImpact
from screener.models import OptionsMetrics, ScreenerResult


# ── filtres éliminatoires ─────────────────────────────────────────────────────

DISQUALIFICATION_RULES: dict[str, callable] = {
    "spread_too_wide": lambda m: m.avg_bid_ask_spread_pct > config.SCREENER_MAX_SPREAD_PCT,
    "no_volume": lambda m: (m.avg_volume_near + m.avg_volume_far) / 2 < config.SCREENER_MIN_AVG_OPTION_VOLUME,
    # OI check désactivé quand les données OI sont indisponibles (valeur sentinelle 999_999)
    "no_open_interest": lambda m: (
        m.avg_oi_near < 999_000 and m.avg_oi_far < 999_000
        and (m.avg_oi_near + m.avg_oi_far) / 2 < config.SCREENER_MIN_AVG_OPEN_INTEREST
    ),
    "not_enough_strikes": lambda m: min(m.strike_count_near, m.strike_count_far) < config.SCREENER_MIN_STRIKE_COUNT,
    "iv_data_missing": lambda m: m.iv_atm_near <= 0 or m.iv_atm_far <= 0,
    "critical_event_in_near": lambda m: any(
        ev.impact == EventImpact.CRITICAL for ev in m.events_in_danger_zone
    ),
}


def check_disqualification(metrics: OptionsMetrics) -> str | None:
    """Retourne la raison d'élimination, ou None si le ticker est qualifié."""
    for reason, rule in DISQUALIFICATION_RULES.items():
        try:
            if rule(metrics):
                return reason
        except Exception:
            pass
    return None


# ── composantes du score ───────────────────────────────────────────────────────

def _score_iv_rank(iv_rank_proxy: float) -> float:
    """Composante 1 (poids 0.30) : IV Rank optimal autour de 45."""
    return max(0.0, 1.0 - abs(iv_rank_proxy - 45) / 55)


def _score_term_structure(ratio: float) -> float:
    """Composante 2 (poids 0.25) : term structure décroît linéairement de 1.0→0 entre 1.00 et 1.30."""
    if ratio <= 1.00:
        return 1.0
    if ratio >= 1.30:
        return 0.0
    return (1.30 - ratio) / (1.30 - 1.00)


def _score_liquidity(
    avg_spread_pct: float,
    avg_volume: float,
    avg_oi: float,
) -> float:
    """
    Composante 3 (poids 0.20) : mix spread (0.4) + volume log (0.3) + OI log (0.3).
    Formules validées — le log scale différencie les ordres de grandeur.
    """
    spread_score = max(0.0, min(1.0, 1 - avg_spread_pct / 0.10))

    vol_min, vol_max = 100.0, 50_000.0
    log_vol_range = math.log(vol_max / vol_min)
    volume_score = max(0.0, min(1.0, math.log(max(avg_volume, vol_min) / vol_min) / log_vol_range))

    oi_min, oi_max = 500.0, 100_000.0
    log_oi_range = math.log(oi_max / oi_min)
    oi_score = max(0.0, min(1.0, math.log(max(avg_oi, oi_min) / oi_min) / log_oi_range))

    return 0.4 * spread_score + 0.3 * volume_score + 0.3 * oi_score


def _score_density(avg_strike_count: float, weekly_count: int) -> float:
    """Composante 4 (poids 0.10) : densité strikes + weeklies."""
    strike_score = max(0.0, min(1.0, (avg_strike_count - 10) / (50 - 10)))
    weekly_score = max(0.0, min(1.0, weekly_count / 4))
    return 0.7 * strike_score + 0.3 * weekly_score


def _score_events(event_score_factor: float) -> float:
    """Composante 5 (poids 0.15) : profil événementiel."""
    return max(0.0, min(1.0, (event_score_factor - 0.5) / 1.0))


# ── score composite ────────────────────────────────────────────────────────────

def compute_score(metrics: OptionsMetrics) -> float:
    """
    Score composite 0-100 = somme pondérée des 5 composantes × pénalités.

    Poids : IV Rank 0.30 | Term structure 0.25 | Liquidité 0.20 | Densité 0.10 | Events 0.15
    Pénalités : ex-div ×0.3 | IV Rank>70 ×0.5 | backwardation>1.15 ×0.7
    """
    avg_volume = (metrics.avg_volume_near + metrics.avg_volume_far) / 2
    avg_oi = (metrics.avg_oi_near + metrics.avg_oi_far) / 2
    avg_strikes = (metrics.strike_count_near + metrics.strike_count_far) / 2

    raw_score = (
        config.SCREENER_SCORE_WEIGHT_IV_RANK        * _score_iv_rank(metrics.iv_rank_proxy)
        + config.SCREENER_SCORE_WEIGHT_TERM_STRUCTURE * _score_term_structure(metrics.term_structure_ratio)
        + config.SCREENER_SCORE_WEIGHT_LIQUIDITY      * _score_liquidity(metrics.avg_bid_ask_spread_pct, avg_volume, avg_oi)
        + config.SCREENER_SCORE_WEIGHT_DENSITY        * _score_density(avg_strikes, metrics.weekly_count)
        + config.SCREENER_SCORE_WEIGHT_EVENTS         * _score_events(metrics.event_score_factor)
    ) * 100

    # Pénalités multiplicatives
    penalty = 1.0

    # Ex-dividende dans la fenêtre near ou just après
    if metrics.next_ex_div_date is not None:
        today = date.today()
        far_days = (metrics.far_expiry - today).days
        days_to_xd = (metrics.next_ex_div_date - today).days
        if 0 <= days_to_xd <= far_days + 7:
            penalty *= config.SCREENER_PENALTY_EX_DIV          # 0.3

    # IV Rank trop élevé → vol overpriced, mauvais moment pour acheter
    if metrics.iv_rank_proxy > 70:
        penalty *= config.SCREENER_PENALTY_HIGH_IV_RANK        # 0.5

    # Backwardation forte (far >> near)
    if metrics.term_structure_ratio > 1.15:
        penalty *= config.SCREENER_PENALTY_BACKWARDATION       # 0.7

    return raw_score * penalty


# ── conversion OptionsMetrics → ScreenerResult ────────────────────────────────

def to_screener_result(metrics: OptionsMetrics, score: float) -> ScreenerResult:
    avg_volume = (metrics.avg_volume_near + metrics.avg_volume_far) / 2
    avg_oi = (metrics.avg_oi_near + metrics.avg_oi_far) / 2

    return ScreenerResult(
        symbol=metrics.symbol,
        score=round(score, 1),
        spot_price=metrics.spot_price,
        iv_rank_proxy=round(metrics.iv_rank_proxy, 1),
        term_structure_ratio=round(metrics.term_structure_ratio, 3),
        avg_option_spread_pct=round(metrics.avg_bid_ask_spread_pct, 3),
        avg_option_volume=round(avg_volume, 0),
        avg_open_interest=round(avg_oi, 0),
        strike_count=min(metrics.strike_count_near, metrics.strike_count_far),
        weekly_expiries_available=metrics.weekly_count > 0,
        weekly_count=metrics.weekly_count,
        next_earnings_date=metrics.next_earnings_date,
        next_ex_div_date=metrics.next_ex_div_date,
        events_in_near_zone=[ev.name for ev in metrics.events_in_danger_zone],
        events_in_sweet_zone=[ev.name for ev in metrics.events_in_sweet_zone],
        has_event_bonus=bool(metrics.events_in_sweet_zone),
        disqualification_reason=metrics.disqualification_reason,
    )
