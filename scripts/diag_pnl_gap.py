"""Diagnostic d'ecart P&L theorique vs replay observe pour un combo precis.

Combo de reference : Double Calendar QQQ ratiote 2/3
- L2 call QQQ 29MAY2026 644
- L3 put  QQQ 29MAY2026 622
- S2 call QQQ 29APR2026 660
- S3 put  QQQ 29APR2026 610

Entree      : 2026-04-15 12h00 ET
Observation : 2026-04-24 13h30 ET

Reproduit le P&L observe via backtest_combo_hourly (resolution 5min)
puis compare aux 3 methodes de calcul theorique :
  A : IV figee entree, TTE = close_date - 3j   (methode actuelle de la courbe)
  B : IV figee entree, TTE = today (24/04)
  C : IV refetched a 24/04, TTE = today (24/04)

Decomposition :
  A -> B = effet "date d'evaluation differente" (theta non encore realise)
  B -> C = effet "vega non capture" (IV figee)
  C -> obs = residu (smile + pricer americain BJS vs BS replay + autres)

Usage : python -m scripts.diag_pnl_gap
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

# Permettre l'execution depuis racine projet sans installation
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.replay import backtest_combo_hourly
from data.models import Combination, Leg
from data.provider_polygon import PolygonHistoricalProvider
from data.provider_yfinance import _implied_vol
from engine.backend import xp, to_cpu
from engine.pnl import combinations_to_tensor, compute_pnl_batch
from ui.combo_parser import _occ_symbol


COMBO_TEXT = (
    "L2 call QQQ 29MAY2026 644 | "
    "L3 put QQQ 29MAY2026 622 | "
    "S2 call QQQ 29APR2026 660 | "
    "S3 put QQQ 29APR2026 610"
)

LEG_SPECS = [
    {"direction": +1, "quantity": 2, "option_type": "call", "symbol": "QQQ",
     "expiration": date(2026, 5, 29), "strike": 644.0},
    {"direction": +1, "quantity": 3, "option_type": "put",  "symbol": "QQQ",
     "expiration": date(2026, 5, 29), "strike": 622.0},
    {"direction": -1, "quantity": 2, "option_type": "call", "symbol": "QQQ",
     "expiration": date(2026, 4, 29), "strike": 660.0},
    {"direction": -1, "quantity": 3, "option_type": "put",  "symbol": "QQQ",
     "expiration": date(2026, 4, 29), "strike": 610.0},
]

ENTRY_DATE = date(2026, 4, 15)
ENTRY_TIME = "12:00"
OBS_DATE   = date(2026, 4, 24)
OBS_TIME   = "13:30"


def _fetch_legs_at(
    provider: PolygonHistoricalProvider,
    as_of: date,
    scan_time: str,
    spot: float,
    rate: float,
) -> list[Leg]:
    """Construit 4 Legs avec mid + IV recalculee au timestamp donne."""
    legs: list[Leg] = []
    for spec in LEG_SPECS:
        occ = _occ_symbol(spec["symbol"], spec["expiration"],
                          spec["option_type"], spec["strike"])
        bar = provider.get_contract_close(f"O:{occ}", as_of, scan_time)
        mid = bar[0] if (bar and bar[0] > 0) else 0.0
        tte = max(0.0, (spec["expiration"] - as_of).days / 365.0)

        iv = 0.20
        if mid > 0 and tte > 0:
            iv_raw = _implied_vol(spec["option_type"], mid, spot,
                                  spec["strike"], tte, rate)
            if 0.01 <= iv_raw <= 5.0:
                iv = iv_raw

        legs.append(Leg(
            option_type=spec["option_type"],
            direction=spec["direction"],
            quantity=spec["quantity"],
            strike=spec["strike"],
            expiration=spec["expiration"],
            entry_price=mid,
            implied_vol=iv,
            div_yield=0.0,
            contract_symbol=f"O:{occ}",
        ))
    return legs


def _build_combo(legs: list[Leg]) -> Combination:
    short_exps = [l.expiration for l in legs if l.direction < 0]
    close_date = min(short_exps) if short_exps else min(l.expiration for l in legs)
    net_debit  = sum(l.direction * l.quantity * l.entry_price * 100 for l in legs)
    return Combination(
        legs=legs, net_debit=net_debit, close_date=close_date,
        template_name="diag_manual",
    )


def _theoretical_pnl_at_spot(
    combo: Combination,
    spot_eval: float,
    days_before_close: int,
    rate: float,
) -> float:
    """P&L theorique en $ a un spot donne (vol_factor=1.0).
    Utilise compute_pnl_batch (Bjerksund-Stensland americain par defaut)."""
    spot_range = xp.array([spot_eval], dtype=xp.float32)
    tensor = combinations_to_tensor([combo], days_before_close=days_before_close)
    pnl = compute_pnl_batch(tensor, spot_range, [1.0], rate, use_american_pricer=True)
    return float(to_cpu(pnl)[0, 0, 0])


def _format_leg_label(leg: Leg) -> str:
    sign = "L" if leg.direction > 0 else "S"
    return f"{sign}{leg.quantity} {leg.option_type:4s} {leg.strike:6.0f} {leg.expiration}"


def main() -> None:
    print("=" * 72)
    print("DIAGNOSTIC ECART P&L THEORIQUE vs REPLAY OBSERVE")
    print("=" * 72)
    print(f"Combo  : {COMBO_TEXT}")
    print(f"Entree : {ENTRY_DATE} {ENTRY_TIME} ET")
    print(f"Observ.: {OBS_DATE} {OBS_TIME} ET")
    print()

    provider = PolygonHistoricalProvider()
    rate_entry = provider.get_risk_free_rate(ENTRY_DATE)
    rate_obs   = provider.get_risk_free_rate(OBS_DATE)
    print(f"^IRX entree  : {rate_entry*100:.3f}%")
    print(f"^IRX observ. : {rate_obs*100:.3f}%")

    spot_entry = provider.get_underlying_close("QQQ", ENTRY_DATE, ENTRY_TIME)
    spot_obs   = provider.get_underlying_close("QQQ", OBS_DATE,   OBS_TIME)
    print(f"Spot entree  : ${spot_entry:.2f}")
    print(f"Spot observ. : ${spot_obs:.2f}")
    print()

    # -- Legs a l'entree ---------------------------------------------------
    legs_entry = _fetch_legs_at(provider, ENTRY_DATE, ENTRY_TIME, spot_entry, rate_entry)
    combo_entry = _build_combo(legs_entry)
    print(f"Net debit (entree) : {combo_entry.net_debit:+.2f} $")
    print(f"close_date (= min short expi) : {combo_entry.close_date}")
    print()

    # -- Legs a l'observation (pour fetch IV 24/04 utilisee par methode C) -
    legs_obs = _fetch_legs_at(provider, OBS_DATE, OBS_TIME, spot_obs, rate_obs)

    # -- P&L observe : reproduire le replay 5min ---------------------------
    # Lancer backtest_combo_hourly sur 10 jours et extraire le point a 24/04 13h30 ET
    print("Reproduction du replay 5min via backtest_combo_hourly...")
    points = backtest_combo_hourly(
        combo_entry,
        as_of=ENTRY_DATE,
        days_forward=12,
        provider=provider,
        rate=rate_entry,
        resolution="5min",
    )
    target_dt = datetime(OBS_DATE.year, OBS_DATE.month, OBS_DATE.day, 13, 30)
    point_obs = min(points, key=lambda p: abs((p.date - target_dt).total_seconds()))
    print(f"  Point trouve : {point_obs.date} (mode {point_obs.mode})")
    print(f"  Spot replay  : ${point_obs.spot:.2f}")
    pnl_obs_dollar = point_obs.pnl_dollar
    pct_obs = point_obs.pnl_pct
    print()

    # Combo methode C : IV recalculee depuis les VALEURS DU REPLAY 5min
    # (et non les bars 1-min qui peuvent etre absentes pour legs illiquides).
    # Pour chaque leg, on prend point_obs.leg_values[symbol] et on inverse BS
    # pour retrouver l'IV implicite a 24/04 13h30 -> usage par methode C.
    legs_C = []
    iv_obs_real: dict[str, float] = {}
    for le in legs_entry:
        sym = le.contract_symbol
        v_market = point_obs.leg_values.get(sym, 0.0)
        tte = max(0.0, (le.expiration - OBS_DATE).days / 365.0)
        iv_C = le.implied_vol  # fallback : IV entree si invertion impossible
        if v_market > 0 and tte > 0:
            iv_raw = _implied_vol(le.option_type, v_market, point_obs.spot,
                                  le.strike, tte, rate_obs)
            if 0.01 <= iv_raw <= 5.0:
                iv_C = iv_raw
        iv_obs_real[sym] = iv_C
        legs_C.append(replace(le, implied_vol=iv_C))
    combo_C = _build_combo(legs_C)

    nd_abs = abs(combo_entry.net_debit) if abs(combo_entry.net_debit) > 1.0 else 1.0

    # -- Methode A : days_before_close=3, IV figee entree -------------------
    pnl_A_dollar = _theoretical_pnl_at_spot(
        combo_entry, point_obs.spot, days_before_close=3, rate=rate_entry,
    )
    pct_A = pnl_A_dollar / nd_abs * 100

    # -- Methode B : days_before_close = (close_date - obs_date).days, IV figee
    days_bc_today = max(0, (combo_entry.close_date - OBS_DATE).days)
    pnl_B_dollar = _theoretical_pnl_at_spot(
        combo_entry, point_obs.spot, days_before_close=days_bc_today, rate=rate_entry,
    )
    pct_B = pnl_B_dollar / nd_abs * 100

    # -- Methode C : days_before_close = today, IV refetched 24/04 ----------
    pnl_C_dollar = _theoretical_pnl_at_spot(
        combo_C, point_obs.spot, days_before_close=days_bc_today, rate=rate_obs,
    )
    pct_C = pnl_C_dollar / nd_abs * 100

    # -- Affichage ----------------------------------------------------------
    print(f"{'Leg':<25} {'entry $':>10} {'IV ent.':>9}   {'$ 24/04':>10} {'IV 24/04':>9}")
    print("-" * 72)
    for le in legs_entry:
        sym = le.contract_symbol
        v_market = point_obs.leg_values.get(sym, 0.0)
        iv_C = iv_obs_real[sym]
        print(f"{_format_leg_label(le):<25} {le.entry_price:>10.3f} "
              f"{le.implied_vol*100:>7.1f}%   {v_market:>10.3f} "
              f"{iv_C*100:>8.1f}%")

    # Affichage detaille des leg values dans le replay (pour comprendre)
    print()
    print("Leg values dans le replay (mode utilise par leg) :")
    for leg in combo_entry.legs:
        sym = leg.contract_symbol
        v = point_obs.leg_values.get(sym, 0.0)
        m = point_obs.leg_modes.get(sym, "?")
        print(f"  {_format_leg_label(leg):<25} value=${v:>8.3f}  mode={m}")
    print()

    print(f"P&L observe (replay 5min, mode={point_obs.mode}) : "
          f"{pct_obs:+7.2f} %  ({pnl_obs_dollar:+8.2f} $)")
    print(f"P&L theorique A (IV ent., expi-3j=26/04)         : "
          f"{pct_A:+7.2f} %  ({pnl_A_dollar:+8.2f} $)")
    print(f"P&L theorique B (IV ent., today=24/04)           : "
          f"{pct_B:+7.2f} %  ({pnl_B_dollar:+8.2f} $)")
    print(f"P&L theorique C (IV 24/04, today=24/04)          : "
          f"{pct_C:+7.2f} %  ({pnl_C_dollar:+8.2f} $)")
    print()

    # -- Decomposition de l'ecart -----------------------------------------
    delta_total   = pct_obs - pct_A
    delta_theta   = pct_B - pct_A   # A -> B : changement de date d'evaluation
    delta_vega    = pct_C - pct_B   # B -> C : refetch IV
    delta_residue = pct_obs - pct_C # C -> obs : smile + pricer + autres

    print("Decomposition de l'ecart total (obs - A) :")
    print(f"  Total                              : {delta_total:+7.2f} pts")
    print(f"  Theta non capture (A -> B)         : {delta_theta:+7.2f} pts")
    print(f"  Vega non capture  (B -> C)         : {delta_vega:+7.2f} pts")
    print(f"  Residu (C -> obs)                  : {delta_residue:+7.2f} pts")
    print(f"     (smile, pricer americain BJS vs BS, mid Polygon, etc.)")


if __name__ == "__main__":
    main()
