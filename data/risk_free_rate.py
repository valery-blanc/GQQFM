"""Récupération du taux sans risque depuis Yahoo Finance (^IRX)."""

from __future__ import annotations

import yfinance as yf

import config


def fetch_risk_free_rate() -> tuple[float, str]:
    """
    Récupère le taux du T-bill 13 semaines (^IRX) sur Yahoo Finance.

    ^IRX est coté en pourcentage (ex: 4.50 = 4.50 %), on divise par 100
    pour obtenir un taux décimal utilisable par les pricers.

    Returns:
        (rate_decimal, source) où source vaut "live" ou "fallback".
        En cas d'erreur réseau / data manquante / valeur aberrante,
        on renvoie config.DEFAULT_RISK_FREE_RATE avec source="fallback".
    """
    try:
        hist = yf.Ticker("^IRX").history(period="5d")
        if hist.empty or "Close" not in hist.columns:
            raise ValueError("no data")
        rate_pct = float(hist["Close"].iloc[-1])
        if not (0.0 < rate_pct < 20.0):
            raise ValueError(f"implausible rate: {rate_pct}")
        return rate_pct / 100.0, "live"
    except Exception:
        return config.DEFAULT_RISK_FREE_RATE, "fallback"
