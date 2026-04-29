"""Parsing du format combo "L1 call SPY 17JUL2026 715 | ..." et résolution des prix."""

from __future__ import annotations

import math
import re
from datetime import date, datetime

import numpy as np
import streamlit as st

import config
from data.models import Combination, Leg


def parse_combo_string(text: str) -> list[dict] | None:
    """
    Parse le format de combo du tableau résultats.
    Retourne list[dict] (un dict par leg) ou None si format invalide.

    Format : "L1 call SPY 17JUL2026 715 | S2 put SPY 15MAY2026 672"
      L/S = long/short · chiffre = quantité · call/put · symbol · DDMMMYYYY · strike
    """
    parts = [p.strip() for p in text.strip().split("|")]
    if not parts:
        return None

    pattern = re.compile(
        r'^([LS])(\d+)\s+(call|put)\s+(\w+)\s+(\d{1,2}[A-Za-z]{3}\d{4})\s+([\d.]+)$',
        re.IGNORECASE,
    )
    leg_specs = []
    for part in parts:
        m = pattern.match(part.strip())
        if not m:
            return None
        direction_char, qty, opt_type, symbol, date_str, strike = m.groups()
        try:
            expiration = datetime.strptime(date_str.upper(), "%d%b%Y").date()
        except ValueError:
            return None
        leg_specs.append({
            "direction": 1 if direction_char.upper() == "L" else -1,
            "quantity": int(qty),
            "option_type": opt_type.lower(),
            "symbol": symbol.upper(),
            "expiration": expiration,
            "strike": float(strike),
        })
    return leg_specs or None


def _occ_symbol(symbol: str, expiration: date, option_type: str, strike: float) -> str:
    """Construit le symbole OCC : SYMBOL + YYMMDD + C/P + STRIKE×1000 sur 8 chiffres."""
    cp = "C" if option_type == "call" else "P"
    return f"{symbol}{expiration.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def _build_combination(legs: list[Leg]) -> Combination:
    close_date = min(l.expiration for l in legs)
    net_debit  = sum(l.direction * l.quantity * l.entry_price * 100 for l in legs)
    return Combination(
        legs=legs, net_debit=net_debit, close_date=close_date, template_name="manual",
    )


def _legs_from_specs(leg_specs: list[dict], contract_index: dict) -> list[Leg]:
    """Construit les Leg depuis les specs parsées et un index (expiry,strike,type) → contract."""
    legs = []
    for spec in leg_specs:
        key      = (spec["expiration"], spec["strike"], spec["option_type"])
        contract = contract_index.get(key)
        legs.append(Leg(
            option_type    = spec["option_type"],
            direction      = spec["direction"],
            quantity       = spec["quantity"],
            strike         = spec["strike"],
            expiration     = spec["expiration"],
            entry_price    = contract.mid if contract else 0.0,
            implied_vol    = (contract.implied_vol if contract and contract.implied_vol > 0
                              else 0.20),
            contract_symbol = _occ_symbol(
                spec["symbol"], spec["expiration"], spec["option_type"], spec["strike"]
            ),
        ))
    return legs


def resolve_combo_live(
    leg_specs: list[dict], symbol: str,
) -> tuple[Combination, float] | None:
    """Résout les prix depuis yfinance (mode live). Retourne (Combination, spot) ou None."""
    from data.provider_yfinance import YFinanceProvider
    try:
        chain = YFinanceProvider().get_options_chain(symbol)
        idx   = {(c.expiration, c.strike, c.option_type): c for c in chain.contracts}
        return _build_combination(_legs_from_specs(leg_specs, idx)), chain.underlying_price
    except Exception as exc:
        st.error(f"Erreur chargement yfinance ({symbol}) : {exc}")
        return None


def resolve_combo_backtest(
    leg_specs: list[dict], symbol: str, as_of: date, scan_time: str | None = None,
) -> tuple[Combination, float, object] | None:
    """
    Résout les prix depuis Polygon à la date as_of.
    Retourne (Combination, spot, provider) ou None.
    """
    from data.provider_polygon import PolygonHistoricalProvider
    try:
        provider = PolygonHistoricalProvider()
        chain    = provider.get_options_chain(
            symbol, as_of=as_of, scan_time=scan_time,
        )
        idx = {(c.expiration, c.strike, c.option_type): c for c in chain.contracts}
        return _build_combination(_legs_from_specs(leg_specs, idx)), chain.underlying_price, provider
    except Exception as exc:
        st.error(f"Erreur chargement Polygon ({symbol} @ {as_of}) : {exc}")
        return None


def build_single_combo_results(
    combination: Combination,
    spot: float,
    symbol: str,
    params: dict,
    as_of: date | None = None,
    provider=None,
) -> dict:
    """
    Calcule le P&L tensor pour un combo unique et retourne un dict résultats
    compatible avec le format attendu par app.py / page_backtest.py.
    """
    from engine.backend import xp, to_cpu
    from engine.pnl import combinations_to_tensor, compute_pnl_batch

    rfr           = params.get("risk_free_rate", config.DEFAULT_RISK_FREE_RATE)
    vol_scenarios = [params.get("vol_low", 0.8), 1.0, params.get("vol_high", 1.2)]
    days_bc       = params.get("days_before_close", 3)

    spot_range = xp.linspace(
        spot * config.SPOT_RANGE_LOW,
        spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS,
        dtype=xp.float32,
    )
    tensor     = combinations_to_tensor([combination], days_before_close=days_bc)
    pnl_tensor = compute_pnl_batch(
        tensor, spot_range, vol_scenarios, rfr,
        use_american_pricer=params.get("use_american_pricer", True),
    )
    pnl_for_combo   = to_cpu(pnl_tensor)[:, 0, :]   # (V, M)
    spot_range_cpu  = to_cpu(spot_range)
    pnl_mid         = pnl_for_combo[config.VOL_MEDIAN_INDEX]

    nd = combination.net_debit if combination.net_debit != 0 else 1e-6
    T  = max((combination.close_date - (as_of or date.today())).days, 1) / 365.0
    atm_vol = max((l.implied_vol for l in combination.legs), default=0.20)
    realistic_range_pct = atm_vol * math.sqrt(T) * 100
    lo, hi = spot * (1 - realistic_range_pct / 100), spot * (1 + realistic_range_pct / 100)
    real_mask = (spot_range_cpu >= lo) & (spot_range_cpu <= hi)

    max_loss     = float(pnl_mid.min())
    max_gain     = float(pnl_mid.max())
    max_gain_real = float(pnl_mid[real_mask].max()) if real_mask.any() else max_gain

    metric = {
        "max_loss_pct":      max_loss / nd * 100,
        "loss_prob_pct":     0.0,
        "max_gain_pct":      max_gain / nd * 100,
        "max_gain_real_pct": max_gain_real / nd * 100,
        "gain_loss_ratio":   max_gain_real / abs(max_loss) if max_loss != 0 else 0.0,
        "score":             0.0,
    }

    result = {
        "combinations":       [combination],
        "metrics":            [metric],
        "pnl_per_combo":      [pnl_for_combo],
        "spot_ranges":        [spot_range_cpu],
        "spots":              [spot],
        "symbols":            [symbol],
        "symbol":             symbol,
        "n_tested":           1,
        "n_found":            1,
        "gpu_time_s":         0.0,
        "days_before_close":  days_bc,
        "realistic_range_pct": realistic_range_pct,
    }
    if as_of is not None:
        result["as_of"] = as_of
    if provider is not None:
        result["provider"] = provider
    return result
