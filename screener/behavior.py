"""
Métriques comportementales du sous-jacent (FEAT-023 § Étape 3).

Mesure si un ticker est "calendar-friendly" (mean revert, vol stable, faible ATR)
ou "RIC-friendly" (vol qui accélère, ATR élevé). Toutes les métriques sont
calculées en batch depuis un seul appel `yf.download` partagé avec un benchmark
pour calculer le beta.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class UnderlyingBehavior:
    """Profil comportemental d'un sous-jacent sur ~3 mois d'historique."""
    symbol: str
    autocorr_1d: float           # auto-corr lag-1 sur 60j (≤0 = mean revert)
    atr_pct: float               # ATR_20 / spot, en fraction (0.015 = 1.5 %)
    gap_rate_2pct: float         # % jours avec |gap_open| > 2 % sur 60j
    hv_ratio_20_60: float        # HV20 / HV60 (>1.2 = vol qui accélère)
    trend_strength: float        # |close[0]-close[-30]| / (ATR_20 × √30)
    beta_spy: float              # régression log-rendements sur 60j vs SPY
    range_position: float        # (close - min_30) / (max_30 - min_30), 0-1
    samples: int                 # nb de jours de données utilisés


def _empty_behavior(symbol: str) -> UnderlyingBehavior:
    return UnderlyingBehavior(
        symbol=symbol,
        autocorr_1d=0.0, atr_pct=0.02, gap_rate_2pct=0.0,
        hv_ratio_20_60=1.0, trend_strength=0.0, beta_spy=1.0,
        range_position=0.5, samples=0,
    )


def batch_compute_behavior(
    symbols: list[str],
    benchmark: str = "SPY",
    period: str = "6mo",
) -> dict[str, UnderlyingBehavior]:
    """
    Télécharge l'historique de tous les symboles + benchmark en un seul appel
    yfinance. Calcule toutes les métriques en pandas/numpy, sans nouvelle
    requête réseau par ticker.
    """
    import yfinance as yf
    if not symbols:
        return {}

    all_syms = symbols + ([benchmark] if benchmark not in symbols else [])
    try:
        data = yf.download(
            all_syms, period=period, interval="1d",
            progress=False, auto_adjust=True,
        )
    except Exception as exc:
        logger.warning("batch behavior download failed : %s", exc)
        return {sym: _empty_behavior(sym) for sym in symbols}

    # Helpers pour extraire les séries selon le format yfinance
    def _series(field: str, sym: str):
        try:
            if len(all_syms) == 1:
                return data[field].dropna()
            return data[field][sym].dropna()
        except Exception:
            return None

    bench_close = _series("Close", benchmark)
    bench_log_ret = (
        np.log(bench_close / bench_close.shift(1)).dropna()
        if bench_close is not None and len(bench_close) > 1
        else None
    )

    result: dict[str, UnderlyingBehavior] = {}
    for sym in symbols:
        try:
            close = _series("Close", sym)
            high = _series("High", sym)
            low = _series("Low", sym)
            opn = _series("Open", sym)
            if close is None or len(close) < 30:
                result[sym] = _empty_behavior(sym)
                continue

            spot = float(close.iloc[-1])

            # Log returns
            log_ret = np.log(close / close.shift(1)).dropna()
            if len(log_ret) < 22:
                result[sym] = _empty_behavior(sym)
                continue

            # Auto-corr lag-1 sur les derniers 60j (ou ce qui est dispo)
            recent_ret = log_ret.tail(60)
            if len(recent_ret) >= 10:
                autocorr = float(np.corrcoef(recent_ret[:-1], recent_ret[1:])[0, 1])
                if math.isnan(autocorr):
                    autocorr = 0.0
            else:
                autocorr = 0.0

            # ATR_20 (en %)
            if high is not None and low is not None and len(high) >= 21:
                tr = np.maximum(
                    high - low,
                    np.maximum(
                        abs(high - close.shift(1)),
                        abs(low - close.shift(1)),
                    ),
                )
                atr_20 = float(tr.tail(20).mean())
                atr_pct = atr_20 / spot if spot > 0 else 0.02
            else:
                atr_pct = float(log_ret.tail(20).std()) * math.sqrt(252) / 16  # fallback approx

            # Gap rate : |open - prev_close| / prev_close > 2 %
            if opn is not None and len(opn) >= 60:
                prev_close = close.shift(1)
                gaps = abs((opn - prev_close) / prev_close).dropna()
                recent_gaps = gaps.tail(60)
                gap_rate = float((recent_gaps > 0.02).mean()) if len(recent_gaps) > 0 else 0.0
            else:
                gap_rate = 0.0

            # HV ratios (annualisés)
            hv_20 = float(log_ret.tail(20).std() * math.sqrt(252))
            hv_60 = float(log_ret.tail(60).std() * math.sqrt(252)) if len(log_ret) >= 60 else hv_20
            hv_ratio = hv_20 / hv_60 if hv_60 > 0 else 1.0

            # Trend strength : tendance normalisée par la "diffusion" attendue
            if len(close) >= 31 and atr_pct > 0 and spot > 0:
                drift = float(close.iloc[-1] - close.iloc[-31])
                expected_diffusion = atr_pct * spot * math.sqrt(30)
                trend = abs(drift) / expected_diffusion if expected_diffusion > 0 else 0.0
            else:
                trend = 0.0

            # Beta vs SPY
            if bench_log_ret is not None and sym != benchmark:
                aligned = log_ret.align(bench_log_ret, join="inner")
                rs, rb = aligned[0].tail(60), aligned[1].tail(60)
                if len(rs) >= 20 and rb.var() > 0:
                    beta = float(np.cov(rs, rb)[0, 1] / rb.var())
                else:
                    beta = 1.0
            else:
                beta = 1.0

            # Range position (0 = bas du range 30j, 1 = haut)
            recent_close = close.tail(30)
            cmin, cmax = float(recent_close.min()), float(recent_close.max())
            range_pos = (spot - cmin) / (cmax - cmin) if cmax > cmin else 0.5

            result[sym] = UnderlyingBehavior(
                symbol=sym,
                autocorr_1d=autocorr,
                atr_pct=atr_pct,
                gap_rate_2pct=gap_rate,
                hv_ratio_20_60=hv_ratio,
                trend_strength=trend,
                beta_spy=beta,
                range_position=range_pos,
                samples=len(log_ret),
            )
        except Exception as exc:
            logger.debug("Behavior %s : %s", sym, exc)
            result[sym] = _empty_behavior(sym)

    return result
