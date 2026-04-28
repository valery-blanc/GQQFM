"""Récupération du taux sans risque depuis Yahoo Finance (^IRX)."""

from __future__ import annotations

from datetime import date, timedelta

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


def fetch_historical_risk_free_rate(as_of: date) -> tuple[float, str]:
    """
    Récupère le taux ^IRX pour une date historique spécifique.
    Remonte jusqu'à 7 jours en arrière pour gérer fériés et weekends.

    Returns:
        (rate_decimal, source_label)
    """
    try:
        hist = yf.Ticker("^IRX").history(
            start=as_of - timedelta(days=7),
            end=as_of + timedelta(days=1),
        )
        if hist.empty or "Close" not in hist.columns:
            raise ValueError("no data")
        idx = hist.index
        if hasattr(idx, "tz") and idx.tz is not None:
            idx = idx.tz_localize(None)
        mask = idx.date <= as_of
        hist_before = hist[mask]
        if hist_before.empty:
            raise ValueError("no data on or before as_of")
        rate_pct = float(hist_before["Close"].iloc[-1])
        if not (0.0 < rate_pct < 20.0):
            raise ValueError(f"implausible rate: {rate_pct}")
        return rate_pct / 100.0, f"^IRX @ {as_of.isoformat()}"
    except Exception:
        return config.DEFAULT_RISK_FREE_RATE, "fallback"
