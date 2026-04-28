"""Replay quotidien et horaire d'une combinaison sur N jours après l'entrée."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import config
from data.models import Combination, Leg
from data.provider_polygon import PolygonHistoricalProvider
from data.provider_yfinance import _bs_price

_ET = ZoneInfo("America/New_York")
# NYSE regular session : barres horaires de 9h à 15h ET inclus (→ 9h-16h couverts)
_NYSE_OPEN_HOUR = 9
_NYSE_CLOSE_HOUR = 15

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


def _prefetch_daily_range(
    provider: PolygonHistoricalProvider,
    ticker: str,
    from_date: date,
    to_date: date,
) -> dict[date, tuple[float, int]]:
    """
    Fetche toutes les barres journalières pour [from_date, to_date] en un seul appel.
    Retourne {date: (close, volume)}.
    """
    data = provider._get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date.isoformat()}/{to_date.isoformat()}",
        params={"limit": 500},
    )
    result: dict[date, tuple[float, int]] = {}
    for bar in data.get("results", []):
        # t est en millisecondes UTC
        d = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).date()
        result[d] = (float(bar["c"]), int(bar.get("v", 0)))
    return result


def _prefetch_hourly_range(
    provider: PolygonHistoricalProvider,
    ticker: str,
    from_date: date,
    to_date: date,
) -> dict[datetime, tuple[float, int]]:
    """
    Fetche toutes les barres 1h pour [from_date, to_date] en suivant la pagination.
    Polygon retourne ~86 barres par page même avec limit=5000 — _paginated suit next_url.
    Filtre NYSE : lun-ven, 9h-15h ET (session régulière).
    Retourne {datetime_et_naive: (close, volume)}.
    """
    all_bars = provider._paginated(
        f"/v2/aggs/ticker/{ticker}/range/1/hour/{from_date.isoformat()}/{to_date.isoformat()}",
        {"limit": 5000},
    )
    result: dict[datetime, tuple[float, int]] = {}
    for bar in all_bars:
        dt_utc = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc)
        dt_et = dt_utc.astimezone(_ET)
        if dt_et.weekday() >= 5:
            continue
        if not (_NYSE_OPEN_HOUR <= dt_et.hour <= _NYSE_CLOSE_HOUR):
            continue
        dt_naive = dt_et.replace(tzinfo=None)
        result[dt_naive] = (float(bar["c"]), int(bar.get("v", 0)))
    return result


def _leg_value_hourly(
    leg: Leg,
    dt_et: datetime,
    spot_today: float,
    leg_bars: dict[datetime, tuple[float, int]],
    rate: float,
    spot_at_leg_expiry: float | None,
) -> tuple[float, str]:
    """Valeur d'un leg à l'heure dt_et. Identique à _leg_value_today mais clé datetime."""
    d = dt_et.date()
    if d >= leg.expiration:
        return _leg_intrinsic_at_expiry(leg, spot_at_leg_expiry or spot_today), "expired"

    bar = leg_bars.get(dt_et)
    if bar is not None and bar[0] > 0:
        return bar[0], "market"

    tte = max(0.0, (leg.expiration - d).days / 365.0)
    if leg.implied_vol <= 0 or tte <= 0:
        if leg.option_type == "call":
            return max(spot_today - leg.strike, 0.0), "theoretical"
        return max(leg.strike - spot_today, 0.0), "theoretical"
    return _bs_price(leg.option_type, spot_today, leg.strike, tte, leg.implied_vol, rate), "theoretical"


def _closest_bar(
    bars: dict[date, tuple[float, int]],
    d: date,
    max_lookback: int = 5,
) -> tuple[float, int] | None:
    """Retourne la barre pour le jour d, ou le jour ouvré précédent (max 5j)."""
    for delta in range(0, max_lookback + 1):
        bar = bars.get(d - timedelta(days=delta))
        if bar is not None:
            return bar
    return None


def _leg_intrinsic_at_expiry(leg: Leg, spot_at_expiry: float) -> float:
    if leg.option_type == "call":
        return max(spot_at_expiry - leg.strike, 0.0)
    return max(leg.strike - spot_at_expiry, 0.0)


def _leg_value_today(
    leg: Leg,
    today: date,
    spot_today: float,
    leg_bars: dict[date, tuple[float, int]],
    rate: float,
    spot_at_leg_expiry: float | None,
) -> tuple[float, str]:
    """
    Valeur d'un leg à la date `today` (par action). Retourne (value, mode).
    Utilise leg_bars (pré-fetché) au lieu d'appels API individuels.
    """
    if today >= leg.expiration:
        if spot_at_leg_expiry is None:
            spot_at_leg_expiry = spot_today
        return _leg_intrinsic_at_expiry(leg, spot_at_leg_expiry), "expired"

    bar = leg_bars.get(today)
    if bar is not None:
        close, volume = bar
        if close > 0 and volume > 0:
            return close, "market"

    tte = max(0.0, (leg.expiration - today).days / 365.0)
    if leg.implied_vol <= 0 or tte <= 0:
        if leg.option_type == "call":
            return max(spot_today - leg.strike, 0.0), "theoretical"
        return max(leg.strike - spot_today, 0.0), "theoretical"

    return _bs_price(
        leg.option_type, spot_today, leg.strike, tte, leg.implied_vol, rate
    ), "theoretical"


def _aggregate_mode(leg_modes: dict[str, str]) -> str:
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

    Optimisation plan payant : pré-fetche la plage de dates complète en un seul
    appel par ticker (underlying + chaque leg), puis itère sur le dict local.
    Réduit de ~110 appels API à 5 appels (1 underlying + N legs).
    """
    if provider is None:
        provider = PolygonHistoricalProvider()
    if rate is None:
        rate = config.DEFAULT_RISK_FREE_RATE
    cb = progress_callback or (lambda p, m: None)

    underlying = _extract_underlying(combination.legs[0].contract_symbol)
    net_debit = combination.net_debit if combination.net_debit > 0 else 1e-6
    last_day = as_of + timedelta(days=days_forward)

    # ── Pré-fetch en bloc ───────────────────────────────────────────────────
    n_legs = len(combination.legs)
    cb(0.0, f"Pré-fetch underlying {underlying} ({as_of} → {last_day})…")
    underlying_bars = _prefetch_daily_range(provider, underlying, as_of, last_day)

    all_leg_bars: dict[str, dict[date, tuple[float, int]]] = {}
    for i, leg in enumerate(combination.legs):
        cb(
            (i + 1) / (n_legs + 1),
            f"Pré-fetch {leg.contract_symbol} ({i+1}/{n_legs})…",
        )
        all_leg_bars[leg.contract_symbol] = _prefetch_daily_range(
            provider, leg.contract_symbol, as_of, last_day
        )

    cb(0.5, "Calcul P&L jour par jour…")

    # Spot à l'expiration de chaque leg (déjà dans underlying_bars — pas d'appel API)
    spot_at_leg_expiry: dict[date, float] = {}
    for leg in combination.legs:
        if as_of <= leg.expiration <= last_day:
            bar = _closest_bar(underlying_bars, leg.expiration)
            if bar is not None:
                spot_at_leg_expiry[leg.expiration] = bar[0]

    # ── Replay jour par jour ────────────────────────────────────────────────
    points: list[BacktestPoint] = []
    last_known_spot: float | None = None
    last_known_pnl: float | None = None

    total_steps = days_forward + 1
    for offset in range(0, total_steps):
        d = as_of + timedelta(days=offset)
        cb(0.5 + 0.5 * offset / total_steps, f"Replay D+{offset} ({d.isoformat()})")

        # Weekends et jours fériés : on ne peut pas clôturer, on saute
        if d.weekday() >= 5:
            continue

        spot_bar = _closest_bar(underlying_bars, d)
        if spot_bar is None:
            continue  # férié ou pas de données Polygon ce jour — marché fermé

        spot_today = spot_bar[0]
        last_known_spot = spot_today

        leg_values: dict[str, float] = {}
        leg_modes: dict[str, str] = {}
        pnl_dollar = 0.0

        for leg in combination.legs:
            spot_exp = spot_at_leg_expiry.get(leg.expiration)
            value, mode = _leg_value_today(
                leg, d, spot_today,
                all_leg_bars[leg.contract_symbol],
                rate, spot_exp,
            )
            leg_values[leg.contract_symbol] = value
            leg_modes[leg.contract_symbol] = mode
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


def backtest_combo_hourly(
    combination: Combination,
    as_of: date,
    days_forward: int = 30,
    provider: PolygonHistoricalProvider | None = None,
    rate: float | None = None,
    progress_callback: ProgressCb | None = None,
) -> list[BacktestPoint]:
    """
    Replay horaire du P&L de `combination` sur `days_forward` jours après `as_of`.
    Précision 1h, filtré NYSE (9h-16h ET, lun-ven).
    Réutilise BacktestPoint avec BacktestPoint.date = datetime ET naive.
    5 appels API (prefetch range 1h underlying + legs).
    """
    if provider is None:
        provider = PolygonHistoricalProvider()
    if rate is None:
        rate = config.DEFAULT_RISK_FREE_RATE
    cb = progress_callback or (lambda p, m: None)

    underlying = _extract_underlying(combination.legs[0].contract_symbol)
    net_debit = combination.net_debit if combination.net_debit > 0 else 1e-6
    last_day = as_of + timedelta(days=days_forward)
    n_legs = len(combination.legs)

    # ── Pré-fetch barres horaires ────────────────────────────────────────────
    cb(0.0, f"Pré-fetch underlying {underlying} (horaire {as_of}→{last_day})…")
    underlying_bars = _prefetch_hourly_range(provider, underlying, as_of, last_day)

    all_leg_bars: dict[str, dict[datetime, tuple[float, int]]] = {}
    for i, leg in enumerate(combination.legs):
        cb((i + 1) / (n_legs + 1), f"Pré-fetch {leg.contract_symbol} horaire ({i+1}/{n_legs})…")
        all_leg_bars[leg.contract_symbol] = _prefetch_hourly_range(
            provider, leg.contract_symbol, as_of, last_day
        )

    cb(0.5, "Calcul P&L horaire…")

    # Spot à l'expiration de chaque leg (dernière barre horaire du jour d'expiration)
    spot_at_leg_expiry: dict[date, float] = {}
    for leg in combination.legs:
        if as_of <= leg.expiration <= last_day:
            day_bars = {dt: v for dt, v in underlying_bars.items()
                        if dt.date() == leg.expiration}
            if day_bars:
                spot_at_leg_expiry[leg.expiration] = day_bars[max(day_bars)][0]

    # ── Replay heure par heure ───────────────────────────────────────────────
    points: list[BacktestPoint] = []
    all_dts = sorted(underlying_bars.keys())
    total = max(len(all_dts), 1)

    for i, dt_et in enumerate(all_dts):
        cb(0.5 + 0.5 * i / total, f"Replay {dt_et.strftime('%d/%m %Hh')}…")

        spot_today = underlying_bars[dt_et][0]
        d = dt_et.date()

        leg_values: dict[str, float] = {}
        leg_modes: dict[str, str] = {}
        pnl_dollar = 0.0

        for leg in combination.legs:
            spot_exp = spot_at_leg_expiry.get(leg.expiration)
            value, mode = _leg_value_hourly(
                leg, dt_et, spot_today,
                all_leg_bars[leg.contract_symbol],
                rate, spot_exp,
            )
            leg_values[leg.contract_symbol] = value
            leg_modes[leg.contract_symbol] = mode
            pnl_dollar += leg.direction * leg.quantity * (value - leg.entry_price) * 100

        points.append(BacktestPoint(
            date=dt_et,           # datetime ET naive pour l'axe X horaire
            spot=spot_today,
            pnl_dollar=pnl_dollar,
            pnl_pct=pnl_dollar / net_debit * 100,
            mode=_aggregate_mode(leg_modes),
            leg_values=leg_values,
            leg_modes=leg_modes,
        ))

    cb(1.0, f"Replay horaire terminé ({len(points)} barres)")
    return points
