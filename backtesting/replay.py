"""Replay quotidien d'une combinaison sur N jours après l'entrée."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import config
from data.models import Combination, Leg
from data.provider_polygon import PolygonHistoricalProvider
from data.provider_yfinance import _bs_price

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float, str], None]


@dataclass
class BacktestPoint:
    """Un point de la courbe de backtest."""
    date: date
    spot: float
    pnl_dollar: float
    pnl_pct: float          # P&L / net_debit en %
    mode: str               # "market" | "theoretical" | "mixed" | "expired" | "no_data"
    leg_values: dict[str, float] = field(default_factory=dict)
    leg_modes: dict[str, str] = field(default_factory=dict)


def _extract_underlying(contract_symbol: str) -> str:
    """
    Extrait le ticker du sous-jacent depuis un contract_symbol Polygon.
    Format OCC : "O:ROOT" + YYMMDD(6) + C/P(1) + Strike(8) → suffixe = 15 chars.
    """
    if contract_symbol.startswith("O:"):
        return contract_symbol[2:-15]
    return contract_symbol[:-15]


def _leg_intrinsic_at_expiry(leg: Leg, spot_at_expiry: float) -> float:
    """Valeur intrinsèque d'un leg expiré (par action, en dollars)."""
    if leg.option_type == "call":
        return max(spot_at_expiry - leg.strike, 0.0)
    return max(leg.strike - spot_at_expiry, 0.0)


def _leg_value_today(
    leg: Leg,
    today: date,
    spot_today: float,
    provider: PolygonHistoricalProvider,
    rate: float,
    spot_at_leg_expiry: float | None,
) -> tuple[float, str]:
    """
    Valeur d'un leg à la date `today` (par action). Retourne (value, mode).

    - Si today >= leg.expiration : valeur intrinsèque au spot du jour d'expiration
    - Sinon : tente le close EOD du contrat sur Polygon (mode "market")
    - Fallback : reprice Black-Scholes scalaire avec IV figée à l'entrée (mode "theoretical")
    """
    if today >= leg.expiration:
        if spot_at_leg_expiry is None:
            spot_at_leg_expiry = spot_today
        return _leg_intrinsic_at_expiry(leg, spot_at_leg_expiry), "expired"

    bar = provider.get_contract_close(leg.contract_symbol, today)
    if bar is not None:
        close, volume = bar
        if close > 0 and volume > 0:
            return close, "market"

    tte = max(0.0, (leg.expiration - today).days / 365.0)
    if leg.implied_vol <= 0 or tte <= 0:
        # Dégénéré : retombe sur intrinsèque au spot du jour
        if leg.option_type == "call":
            return max(spot_today - leg.strike, 0.0), "theoretical"
        return max(leg.strike - spot_today, 0.0), "theoretical"

    return _bs_price(
        leg.option_type, spot_today, leg.strike, tte, leg.implied_vol, rate
    ), "theoretical"


def _aggregate_mode(leg_modes: dict[str, str]) -> str:
    """Détermine le mode global du jour à partir des modes des legs."""
    modes = set(leg_modes.values())
    if modes == {"expired"}:
        return "expired"
    if modes <= {"market", "expired"}:
        return "market"
    if modes <= {"theoretical", "expired"}:
        return "theoretical"
    return "mixed"


def backtest_combo(
    combination: Combination,
    as_of: date,
    days_forward: int = 30,
    provider: PolygonHistoricalProvider | None = None,
    rate: float | None = None,
    progress_callback: ProgressCb | None = None,
) -> list[BacktestPoint]:
    """
    Replay quotidien du P&L de `combination` sur `days_forward` jours après `as_of`.

    Parameters
    ----------
    combination : Combination
        La combinaison à backtester. Les contract_symbol des legs doivent être
        au format Polygon (préfixe "O:") car on requête /v2/aggs avec.
    as_of : date
        Date d'entrée (= jour du scan). Le P&L au jour `as_of` vaut 0.
    days_forward : int
        Nombre de jours calendaires à replayer (défaut 30).
    provider : PolygonHistoricalProvider | None
        Provider Polygon (réutilisable pour bénéficier du cache). Créé si None.
    rate : float | None
        Taux sans risque pour le BS reprice. Défaut config.DEFAULT_RISK_FREE_RATE.

    Returns
    -------
    list[BacktestPoint]
        Une entrée par jour calendaire dans [as_of, as_of + days_forward].
        Les jours non-trading sont marqués mode="no_data" et reportent le
        spot/P&L du dernier jour trading connu.
    """
    if provider is None:
        provider = PolygonHistoricalProvider()
    if rate is None:
        rate = config.DEFAULT_RISK_FREE_RATE
    cb = progress_callback or (lambda p, m: None)

    underlying = _extract_underlying(combination.legs[0].contract_symbol)
    net_debit = combination.net_debit if combination.net_debit > 0 else 1e-6

    # Pré-calcul du spot à l'expiration de chaque leg si l'expiration tombe
    # dans la fenêtre du backtest. Évite des appels redondants.
    last_day = as_of + timedelta(days=days_forward)
    spot_at_leg_expiry: dict[date, float] = {}
    for leg in combination.legs:
        if as_of <= leg.expiration <= last_day:
            try:
                spot_at_leg_expiry[leg.expiration] = provider.get_underlying_close(
                    underlying, leg.expiration
                )
            except RuntimeError:
                logger.warning("No spot bar at leg expiry %s", leg.expiration)

    # Replay jour par jour
    points: list[BacktestPoint] = []
    last_known_spot: float | None = None
    last_known_pnl: float | None = None

    total_steps = days_forward + 1
    for offset in range(0, total_steps):
        d = as_of + timedelta(days=offset)
        cb(offset / total_steps, f"Replay D+{offset} ({d.isoformat()})")

        # Skip weekends (marché US fermé) → carry-forward, aucun appel API
        if d.weekday() >= 5 and last_known_spot is not None:
            points.append(BacktestPoint(
                date=d, spot=last_known_spot,
                pnl_dollar=last_known_pnl or 0.0,
                pnl_pct=(last_known_pnl or 0.0) / net_debit * 100,
                mode="no_data",
            ))
            continue

        # Spot du jour (carry-forward sur jours non-trading / fériés)
        try:
            spot_today = provider.get_underlying_close(underlying, d)
            last_known_spot = spot_today
        except RuntimeError:
            if last_known_spot is None:
                continue   # pas même un point de départ
            points.append(BacktestPoint(
                date=d, spot=last_known_spot,
                pnl_dollar=last_known_pnl or 0.0,
                pnl_pct=(last_known_pnl or 0.0) / net_debit * 100,
                mode="no_data",
            ))
            continue

        # Valeur de chaque leg
        leg_values: dict[str, float] = {}
        leg_modes: dict[str, str] = {}
        pnl_dollar = 0.0

        for leg in combination.legs:
            spot_exp = spot_at_leg_expiry.get(leg.expiration)
            value, mode = _leg_value_today(leg, d, spot_today, provider, rate, spot_exp)
            leg_values[leg.contract_symbol] = value
            leg_modes[leg.contract_symbol] = mode
            # P&L = direction × qty × (value - entry_price) × 100
            pnl_dollar += leg.direction * leg.quantity * (value - leg.entry_price) * 100

        last_known_pnl = pnl_dollar

        points.append(BacktestPoint(
            date=d,
            spot=spot_today,
            pnl_dollar=pnl_dollar,
            pnl_pct=pnl_dollar / net_debit * 100,
            mode=_aggregate_mode(leg_modes),
            leg_values=leg_values,
            leg_modes=leg_modes,
        ))

    cb(1.0, f"Replay terminé ({len(points)} points)")
    return points
