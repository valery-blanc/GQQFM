"""
Étape 5 du pipeline screener : analyse détaillée des options.
Calcule IV ATM, HV30, liquidité, densité, sélection des expirations.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, timedelta

import numpy as np

import config
from events.calendar import EventCalendar
from screener.models import OptionsMetrics

logger = logging.getLogger(__name__)


# ── sélection des expirations ────────────────────────────────────────────────

def select_expirations(
    expirations: list[date],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
    event_calendar: EventCalendar,
    today: date,
) -> tuple[date | None, date | None]:
    """
    Choisit la meilleure paire (near_expiry, far_expiry) pour un calendar spread.

    Critères de tri (priorité décroissante) :
    1. event_score_factor le plus élevé
    2. near_days >= 7 préféré (Greeks stables, prime de calendar non nulle)
    3. Écart (far_days - near_days) le plus grand
    """
    valid_pairs: list[tuple[date, date, float, int, bool]] = []

    for near_exp in expirations:
        near_days = (near_exp - today).days
        if not (near_range[0] <= near_days <= near_range[1]):
            continue

        for far_exp in expirations:
            far_days = (far_exp - today).days
            if not (far_range[0] <= far_days <= far_range[1]):
                continue
            if far_exp <= near_exp:
                continue

            result = event_calendar.classify_events_for_pair(near_exp, far_exp)
            factor = result["event_score_factor"]
            spread = far_days - near_days
            near_ok = near_days >= 7

            valid_pairs.append((near_exp, far_exp, factor, spread, near_ok))

    if not valid_pairs:
        return None, None

    # Tri : factor DESC, near_ok DESC (True > False), spread DESC
    valid_pairs.sort(key=lambda x: (x[2], x[4], x[3]), reverse=True)
    return valid_pairs[0][0], valid_pairs[0][1]


# ── HV30 ─────────────────────────────────────────────────────────────────────

def compute_hv30(symbol: str) -> float:
    """
    Volatilité historique annualisée sur 21 jours de trading (~30 jours calendrier).
    Utilise les cours de clôture journaliers des 3 derniers mois.
    Retourne 0.0 si données insuffisantes.
    """
    import yfinance as yf
    try:
        hist = yf.download(
            symbol, period="3mo", interval="1d",
            progress=False, auto_adjust=True,
        )
        closes = hist["Close"].squeeze().dropna()
        if len(closes) < 22:
            return 0.0
        log_returns = np.log(closes / closes.shift(1)).dropna()
        hv = float(log_returns.tail(21).std() * math.sqrt(252))
        return hv
    except Exception as exc:
        logger.debug("HV30 %s : %s", symbol, exc)
        return 0.0


# ── IV ATM ───────────────────────────────────────────────────────────────────

def get_atm_iv(chain_df, spot: float) -> float:
    """
    Retourne l'IV ATM depuis une chaîne d'options (DataFrame yfinance).
    Prend la médiane des 3 strikes les plus proches du spot avec IV valide.
    Retourne 0.0 hors-séance (bid=ask=0 → IV yfinance = 0).
    """
    import pandas as pd
    if chain_df is None or (isinstance(chain_df, pd.DataFrame) and chain_df.empty):
        return 0.0
    try:
        df = chain_df.copy()
        df["dist"] = abs(df["strike"] - spot)
        closest = df.nsmallest(5, "dist")
        valid = closest[closest["impliedVolatility"] > 0.01]
        if valid.empty:
            return 0.0
        return float(valid["impliedVolatility"].median())
    except Exception as exc:
        logger.debug("ATM IV : %s", exc)
        return 0.0


# ── liquidité ────────────────────────────────────────────────────────────────

def compute_chain_liquidity(chain_df) -> tuple[float, float, float]:
    """
    Calcule les métriques de liquidité depuis une chaîne d'options.
    Retourne (avg_spread_pct, avg_volume, avg_open_interest).
    """
    import pandas as pd
    if chain_df is None or (isinstance(chain_df, pd.DataFrame) and chain_df.empty):
        return 0.20, 0.0, 0.0
    try:
        df = chain_df.copy()
        # Spread bid-ask en % du mid
        mid = (df["bid"] + df["ask"]) / 2
        valid_mid = mid[mid > 0]
        if valid_mid.empty:
            spread_pct = 0.20
        else:
            spread = df.loc[valid_mid.index, "ask"] - df.loc[valid_mid.index, "bid"]
            spread_pct = float((spread / valid_mid).median())

        avg_volume = float(df["volume"].fillna(0).mean())
        avg_oi = float(df["openInterest"].fillna(0).mean())
        return spread_pct, avg_volume, avg_oi
    except Exception as exc:
        logger.debug("Liquidité chaîne : %s", exc)
        return 0.20, 0.0, 0.0


# ── weeklies ─────────────────────────────────────────────────────────────────

def count_weeklies(expirations: list[date], near_range: tuple[int, int], today: date) -> int:
    """Compte le nombre d'expirations dans la fenêtre near_range."""
    return sum(
        1 for exp in expirations
        if near_range[0] <= (exp - today).days <= near_range[1]
    )


# ── analyse principale ───────────────────────────────────────────────────────

def analyze_ticker(
    symbol: str,
    spot_price: float,
    event_calendar: EventCalendar,
    near_range: tuple[int, int] = config.SCREENER_NEAR_EXPIRY_RANGE,
    far_range: tuple[int, int] = config.SCREENER_FAR_EXPIRY_RANGE,
    next_earnings_date: date | None = None,
    next_ex_div_date: date | None = None,
    request_delay: float = config.SCREENER_REQUEST_DELAY,
) -> OptionsMetrics | None:
    """
    Analyse complète d'un ticker (étape 5 du pipeline).
    Rate-limited par request_delay.
    Retourne None si les données sont insuffisantes.
    """
    import yfinance as yf

    today = date.today()

    try:
        ticker = yf.Ticker(symbol)
        expirations_str = ticker.options
        if not expirations_str:
            logger.debug("%s : aucune expiration disponible", symbol)
            return None

        expirations = [date.fromisoformat(s) for s in expirations_str]
        time.sleep(request_delay)

        # Sélection de la meilleure paire d'expirations
        near_exp, far_exp = select_expirations(
            expirations, near_range, far_range, event_calendar, today
        )
        if near_exp is None or far_exp is None:
            logger.debug("%s : aucune paire d'expirations valide", symbol)
            return None

        # Chargement des chaînes options near + far
        near_chain = ticker.option_chain(near_exp.isoformat())
        time.sleep(request_delay)
        far_chain = ticker.option_chain(far_exp.isoformat())
        time.sleep(request_delay)

        near_calls = near_chain.calls
        far_calls = far_chain.calls

        # IV ATM (calls, nearest strikes)
        iv_near = get_atm_iv(near_calls, spot_price)
        iv_far = get_atm_iv(far_calls, spot_price)

        # HV30
        hv30 = compute_hv30(symbol)
        time.sleep(request_delay)

        # IV Rank proxy : clip((IV/HV - 0.6) / 1.2 * 100, 0, 100)
        if hv30 > 0:
            iv_rank = float(np.clip((iv_near / hv30 - 0.6) / 1.2 * 100, 0.0, 100.0))
        else:
            iv_rank = 50.0  # valeur neutre si HV indisponible

        # Term structure ratio
        term_ratio = iv_far / iv_near if iv_near > 0 else 1.0

        # Liquidité
        spread_near, vol_near, oi_near = compute_chain_liquidity(near_calls)
        spread_far, vol_far, oi_far = compute_chain_liquidity(far_calls)
        avg_spread = (spread_near + spread_far) / 2

        # Densité strikes
        strike_count_near = len(near_calls["strike"].unique()) if not near_calls.empty else 0
        strike_count_far = len(far_calls["strike"].unique()) if not far_calls.empty else 0
        weekly_cnt = count_weeklies(expirations, near_range, today)

        # Classification événements
        event_info = event_calendar.classify_events_for_pair(near_exp, far_exp)

        return OptionsMetrics(
            symbol=symbol,
            spot_price=spot_price,
            iv_atm_near=iv_near,
            iv_atm_far=iv_far,
            hv30=hv30,
            iv_rank_proxy=iv_rank,
            term_structure_ratio=term_ratio,
            avg_bid_ask_spread_pct=avg_spread,
            avg_volume_near=vol_near,
            avg_volume_far=vol_far,
            avg_oi_near=oi_near,
            avg_oi_far=oi_far,
            strike_count_near=strike_count_near,
            strike_count_far=strike_count_far,
            weekly_count=weekly_cnt,
            near_expiry=near_exp,
            far_expiry=far_exp,
            events_in_danger_zone=event_info["danger_zone"],
            events_in_sweet_zone=event_info["sweet_zone"],
            event_score_factor=event_info["event_score_factor"],
            next_earnings_date=next_earnings_date,
            next_ex_div_date=next_ex_div_date,
        )

    except Exception as exc:
        logger.warning("Analyse %s échouée : %s", symbol, exc)
        return None
