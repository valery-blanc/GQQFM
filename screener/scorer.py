"""
Score composite pour le screener de sous-jacents.
5 composantes + pénalités multiplicatives + filtres éliminatoires.
"""

from __future__ import annotations

import math
from datetime import date

import config
from events.models import EventImpact, EventScope
from screener.behavior import UnderlyingBehavior
from screener.models import OptionsMetrics, ScreenerResult


# ── filtres éliminatoires ─────────────────────────────────────────────────────

def _max_spread_pct_atm(m: OptionsMetrics) -> float:
    """Max(spread% near, spread% far). Si les deux sont à 0 (legacy / non
    renseigné), retombe sur le legacy `avg_bid_ask_spread_pct`."""
    if m.spread_pct_atm_near > 0 or m.spread_pct_atm_far > 0:
        return max(m.spread_pct_atm_near, m.spread_pct_atm_far)
    return m.avg_bid_ask_spread_pct


DISQUALIFICATION_RULES: dict[str, callable] = {
    # Spread bid/ask % ATM > 12 % sur near OU far : 4 jambes × 12 % ≈ 48 % du
    # débit perdu en frottement. Mesure ATM-ciblée (FEAT-023 § Étape 2).
    "spread_too_wide": lambda m: _max_spread_pct_atm(m) > config.SCREENER_MAX_SPREAD_PCT_ATM,
    # Volume p25 ATM near : la jambe la plus faible parmi celles potentiellement
    # utilisées doit avoir au moins SCREENER_MIN_VOLUME_P25_ATM contrats traités.
    # Skipping si p25=0 ET volume_median=0 (hors-séance — yfinance ne remonte
    # pas le volume du dernier jour).
    "no_volume_atm": lambda m: (
        m.volume_atm_median_near > 0
        and m.volume_atm_p25_near < config.SCREENER_MIN_VOLUME_P25_ATM
    ),
    # OI p25 ATM avec gestion sentinelle hors-séance (999_999 = OI indisponible).
    # Activé seulement si oi_median_near a été mesuré et n'est pas une sentinelle.
    "no_oi_atm": lambda m: (
        0 < m.oi_atm_median_near < 999_000
        and m.oi_atm_p25_near < config.SCREENER_MIN_OI_P25_ATM
    ),
    # Pas assez de strikes dans la zone ATM ±band (besoin de 4 strikes mini
    # pour qu'un combo 4 jambes ait du choix).
    "not_enough_strikes_atm": lambda m: min(
        m.strike_count_atm_near, m.strike_count_atm_far
    ) < config.SCREENER_MIN_STRIKES_ATM if (
        m.strike_count_atm_near > 0 or m.strike_count_atm_far > 0
    ) else False,
    # Conservé : densité chaîne entière (filet de sécurité pour tickers rares)
    "not_enough_strikes": lambda m: min(m.strike_count_near, m.strike_count_far) < config.SCREENER_MIN_STRIKE_COUNT,
    "iv_data_missing": lambda m: m.iv_atm_near <= 0 or m.iv_atm_far <= 0,
    # Seuls les événements MICRO (earnings, ex-div, FDA) éliminent un ticker.
    # Les événements MACRO (FOMC, NFP, CPI) affectent tout le marché — ils
    # pénalisent le score via event_score_factor mais ne disqualifient pas
    # (cf. BUG-028).
    "critical_event_in_near": lambda m: any(
        ev.impact == EventImpact.CRITICAL and ev.scope == EventScope.MICRO
        for ev in m.events_in_danger_zone
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


def _score_tradability(metrics: OptionsMetrics) -> float:
    """
    Score 0-1 du **coût d'entrée + sortie 4 jambes** en % du prix moyen ATM.

    Un combo 4 jambes paie 4 fois le spread bid-ask à l'entrée et 4 fois à la
    sortie (en réalité on capture du mid-fill, mais l'ordre de grandeur reste).
    Formule : `cost_pct ≈ 4 × spread_pct_moyen_ATM`.

    score = 1.0 quand cost_pct ≤ 5 %, décroît linéairement → 0 à cost_pct = 30 %.

    Si les champs ATM ne sont pas renseignés (legacy), retombe sur 0.5 (neutre).
    """
    if metrics.spread_pct_atm_near <= 0 and metrics.spread_pct_atm_far <= 0:
        return 0.5
    avg_spread = (metrics.spread_pct_atm_near + metrics.spread_pct_atm_far) / 2
    cost_pct = 4 * avg_spread
    if cost_pct <= 0.05:
        return 1.0
    if cost_pct >= 0.30:
        return 0.0
    return (0.30 - cost_pct) / (0.30 - 0.05)


def _score_atm_quality(metrics: OptionsMetrics) -> float:
    """
    Score 0-1 combinant tradabilité (spread 4 jambes), profondeur volume ATM p25,
    et profondeur OI ATM p25. Remplace `_score_liquidity` quand les champs ATM
    sont renseignés ; sinon retombe sur le score liquidité legacy pour compat.

    Mix : 0.50 tradability + 0.25 volume_p25_log + 0.25 oi_p25_log.
    Le volume et l'OI sont log-scales : différencie les ordres de grandeur.
    """
    has_atm = (
        metrics.spread_pct_atm_near > 0 or metrics.spread_pct_atm_far > 0
        or metrics.volume_atm_median_near > 0
    )
    if not has_atm:
        # Legacy : retombe sur le score liquidité historique
        avg_volume = (metrics.avg_volume_near + metrics.avg_volume_far) / 2
        avg_oi = (metrics.avg_oi_near + metrics.avg_oi_far) / 2
        return _score_liquidity(metrics.avg_bid_ask_spread_pct, avg_volume, avg_oi)

    tradability = _score_tradability(metrics)

    # Volume p25 score (log) — vol_min=20 (seuil disqualif), vol_max=10_000
    vol_min, vol_max = 20.0, 10_000.0
    log_vol_range = math.log(vol_max / vol_min)
    vol_p25 = max(metrics.volume_atm_p25_near, vol_min)
    volume_score = max(0.0, min(1.0, math.log(vol_p25 / vol_min) / log_vol_range))

    # OI p25 score (log) — oi_min=50, oi_max=50_000 ; sentinelle hors-séance → score neutre
    if metrics.oi_atm_p25_near >= 999_000:
        oi_score = 0.5  # neutre quand OI indisponible
    else:
        oi_min, oi_max = 50.0, 50_000.0
        log_oi_range = math.log(oi_max / oi_min)
        oi_p25 = max(metrics.oi_atm_p25_near, oi_min)
        oi_score = max(0.0, min(1.0, math.log(oi_p25 / oi_min) / log_oi_range))

    return 0.50 * tradability + 0.25 * volume_score + 0.25 * oi_score


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
    avg_strikes = (metrics.strike_count_near + metrics.strike_count_far) / 2

    # Liquidité : composante remplacée par _score_atm_quality (FEAT-023 § Étape 2).
    # Combine spread 4 jambes (tradability), volume p25 ATM, OI p25 ATM.
    # Fallback automatique sur _score_liquidity legacy si champs ATM non renseignés.
    raw_score = (
        config.SCREENER_SCORE_WEIGHT_IV_RANK        * _score_iv_rank(metrics.iv_rank_proxy)
        + config.SCREENER_SCORE_WEIGHT_TERM_STRUCTURE * _score_term_structure(metrics.term_structure_ratio)
        + config.SCREENER_SCORE_WEIGHT_LIQUIDITY      * _score_atm_quality(metrics)
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

    # Événement macro CRITICAL en danger zone (FOMC, NFP, CPI) :
    # pénalité forte mais pas éliminatoire — cf. BUG-028.
    macro_critical_in_near = any(
        ev.impact == EventImpact.CRITICAL and ev.scope == EventScope.MACRO
        for ev in metrics.events_in_danger_zone
    )
    if macro_critical_in_near:
        penalty *= config.SCREENER_PENALTY_MACRO_CRITICAL      # 0.6

    return raw_score * penalty


# ── Scoring multi-stratégie (FEAT-023 § Étape 3) ──────────────────────────────


def _score_iv_rank_calendar(iv_rank_52w: float) -> float:
    """Sweet spot calendar : IV Rank 25-60 (vol modérée). Cloche centrée 42."""
    return max(0.0, 1.0 - abs(iv_rank_52w - 42.0) / 42.0)


def _score_iv_rank_ric(iv_rank_52w: float) -> float:
    """Reverse IC : IV Rank bas (<35) = bon (vol pas overpriced, place pour exploser)."""
    if iv_rank_52w <= 15:
        return 1.0
    if iv_rank_52w >= 60:
        return 0.0
    return (60 - iv_rank_52w) / (60 - 15)


def _score_term_structure_calendar(ratio: float) -> float:
    """
    Calendar : préfère plat à léger contango (0.97-1.07). Pénalise les 2 extrêmes
    avec un floor à 0.20 — éviter qu'une mesure aberrante (ex. SPY ratio=1.53
    causé par une expiration courte post-FOMC avec IV bruitée) anéantisse
    complètement le score d'un sous-jacent par ailleurs excellent.
    """
    if 0.97 <= ratio <= 1.07:
        return 1.0
    floor = 0.20
    if ratio < 0.85 or ratio > 1.20:
        return floor
    if ratio < 0.97:
        return floor + (1.0 - floor) * (ratio - 0.85) / (0.97 - 0.85)
    return floor + (1.0 - floor) * (1.20 - ratio) / (1.20 - 1.07)


def _score_calmness(behavior: UnderlyingBehavior) -> float:
    """
    Score de "calme" du sous-jacent (calendar-friendly).
    Mix : auto-corr (mean revert), ATR bas, peu de gaps, vol qui se compresse.

    Note : pour calendar, vol qui DÉCÉLÈRE (HV20/60 < 1) est un BONUS, pas un
    malus — l'option near vendue perd sa prime plus vite. Seule la vol qui
    accélère est pénalisée.
    """
    # Mean revert : autocorr ≤ 0 = score 1, ≥ 0.3 = score 0
    autocorr_score = max(0.0, min(1.0, (0.30 - behavior.autocorr_1d) / 0.30))
    # ATR : 1 % = score 1, 4 % = score 0
    atr_score = max(0.0, min(1.0, (0.04 - behavior.atr_pct) / (0.04 - 0.01)))
    # Gaps : 0 % = score 1, 20 % = score 0
    gap_score = max(0.0, min(1.0, (0.20 - behavior.gap_rate_2pct) / 0.20))
    # Vol qui se compresse (HV20/60 ≤ 1) = bon ; vol qui accélère (>1.1) = mauvais
    if behavior.hv_ratio_20_60 <= 1.0:
        compression_score = 1.0
    elif behavior.hv_ratio_20_60 >= 1.30:
        compression_score = 0.0
    else:
        compression_score = (1.30 - behavior.hv_ratio_20_60) / 0.30
    return 0.30 * autocorr_score + 0.30 * atr_score + 0.20 * gap_score + 0.20 * compression_score


def _score_vol_acceleration(hv_ratio_20_60: float) -> float:
    """RIC : préfère vol qui accélère. ratio≥1.4 = 1, ≤1.0 = 0."""
    if hv_ratio_20_60 >= 1.4:
        return 1.0
    if hv_ratio_20_60 <= 1.0:
        return 0.0
    return (hv_ratio_20_60 - 1.0) / 0.4


def _score_atr(atr_pct: float) -> float:
    """RIC : ATR > 1.5 % bon (sous-jacent qui bouge). Plafonné à 5 %."""
    return max(0.0, min(1.0, (atr_pct - 0.005) / (0.05 - 0.005)))


def _common_penalties(metrics: OptionsMetrics) -> float:
    """Pénalités multiplicatives partagées (ex-div, macro CRITICAL, backwardation)."""
    penalty = 1.0
    if metrics.next_ex_div_date is not None:
        today = date.today()
        far_days = (metrics.far_expiry - today).days
        days_to_xd = (metrics.next_ex_div_date - today).days
        if 0 <= days_to_xd <= far_days + 7:
            penalty *= config.SCREENER_PENALTY_EX_DIV
    if metrics.term_structure_ratio > 1.20:
        penalty *= config.SCREENER_PENALTY_BACKWARDATION
    macro_crit = any(
        ev.impact == EventImpact.CRITICAL and ev.scope == EventScope.MACRO
        for ev in metrics.events_in_danger_zone
    )
    if macro_crit:
        penalty *= config.SCREENER_PENALTY_MACRO_CRITICAL
    return penalty


def compute_score_calendar(
    metrics: OptionsMetrics,
    behavior: UnderlyingBehavior | None = None,
) -> float:
    """
    Score 0-100 pour stratégies calendar / double-calendar.
    Privilégie : IV Rank modéré, term structure plat, vol stable, mean revert.

    Si `behavior` est None, retombe sur compute_score legacy (pour rétrocompat tests).
    """
    if behavior is None:
        return compute_score(metrics)

    iv_rank_input = metrics.iv_rank_52w if metrics.iv_rank_52w != 50.0 else metrics.iv_rank_proxy
    raw = (
        0.25 * _score_iv_rank_calendar(iv_rank_input)
        + 0.20 * _score_term_structure_calendar(metrics.term_structure_ratio)
        + 0.20 * _score_atm_quality(metrics)
        + 0.15 * _score_calmness(behavior)
        + 0.10 * _score_density(
            (metrics.strike_count_near + metrics.strike_count_far) / 2,
            metrics.weekly_count,
        )
        + 0.10 * _score_events(metrics.event_score_factor)
    ) * 100

    # Pénalité IV Rank trop élevé spécifique calendar : vol overpriced, mauvais
    # moment pour acheter (long vega du calendar) — IV crush probable.
    penalty = _common_penalties(metrics)
    if iv_rank_input > 85:
        penalty *= 0.3   # quasi élimination
    elif iv_rank_input > 70:
        penalty *= config.SCREENER_PENALTY_HIGH_IV_RANK  # 0.5

    return raw * penalty


def compute_score_ric(
    metrics: OptionsMetrics,
    behavior: UnderlyingBehavior | None = None,
) -> float:
    """
    Score 0-100 pour reverse iron condor.
    Privilégie : IV Rank bas, vol qui accélère, ATR élevé.
    """
    if behavior is None:
        return compute_score(metrics)

    iv_rank_input = metrics.iv_rank_52w if metrics.iv_rank_52w != 50.0 else metrics.iv_rank_proxy
    raw = (
        0.30 * _score_iv_rank_ric(iv_rank_input)
        + 0.20 * _score_vol_acceleration(behavior.hv_ratio_20_60)
        + 0.20 * _score_atm_quality(metrics)
        + 0.15 * _score_atr(behavior.atr_pct)
        + 0.10 * _score_density(
            (metrics.strike_count_near + metrics.strike_count_far) / 2,
            metrics.weekly_count,
        )
        + 0.05 * _score_events(metrics.event_score_factor)
    ) * 100
    return raw * _common_penalties(metrics)


# ── conversion OptionsMetrics → ScreenerResult ────────────────────────────────

def to_screener_result(
    metrics: OptionsMetrics,
    score: float,
    behavior: UnderlyingBehavior | None = None,
    profile: str = "calendar",
) -> ScreenerResult:
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
        iv_rank_52w=round(metrics.iv_rank_52w, 1),
        atr_pct=round(behavior.atr_pct, 4) if behavior else 0.0,
        hv_ratio_20_60=round(behavior.hv_ratio_20_60, 3) if behavior else 1.0,
        autocorr_1d=round(behavior.autocorr_1d, 3) if behavior else 0.0,
        profile=profile,
    )
