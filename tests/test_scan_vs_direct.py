"""
Test diagnostique : scan vs saisie directe sur le même combo.

Usage:
  python tests/test_scan_vs_direct.py                  # scan + saisie directe
  python tests/test_scan_vs_direct.py "L1 call SPY …"  # saisie directe only

Logs : préfixe DIAG_ pour grep facile.
"""

from __future__ import annotations

import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import math
import statistics
import sys
from datetime import date

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("DIAG")

# ── helpers ──────────────────────────────────────────────────────────────────

_MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]


def _combo_to_string(combo, symbol: str) -> str:
    parts = []
    for leg in combo.legs:
        d = leg.expiration
        date_str = f"{d.day:02d}{_MONTHS[d.month-1]}{d.year}"
        parts.append(
            f"{'L' if leg.direction > 0 else 'S'}{leg.quantity} "
            f"{leg.option_type} {symbol} {date_str} {leg.strike:g}"
        )
    return " | ".join(parts)


def _log_legs(combo, label: str) -> None:
    log.info(f"DIAG_LEGS_{label}:")
    for leg in combo.legs:
        d = leg.expiration
        date_str = f"{d.day:02d}{_MONTHS[d.month-1]}{d.year}"
        log.info(
            f"  DIAG_LEG  {'L' if leg.direction>0 else 'S'}{leg.quantity} "
            f"{leg.option_type} {leg.strike:g} {date_str}"
            f"  entry={leg.entry_price:.4f}$  IV={leg.implied_vol*100:.2f}%"
            f"  contrib_nd={leg.direction*leg.quantity*leg.entry_price*100:+.2f}$"
        )
    log.info(f"  DIAG_NET_DEBIT_{label}  {combo.net_debit:+.4f}$")


def _log_pnl_diag(pnl_mid: np.ndarray, spot_range: np.ndarray, label: str) -> None:
    idx_min = int(pnl_mid.argmin())
    log.info(f"DIAG_PNL_{label}:")
    log.info(f"  spot[  0]={spot_range[  0]:.2f}$  P&L={pnl_mid[  0]:+.2f}$")
    log.info(f"  spot[ 49]={spot_range[ 49]:.2f}$  P&L={pnl_mid[ 49]:+.2f}$")
    log.info(f"  spot[ 99]={spot_range[ 99]:.2f}$  P&L={pnl_mid[ 99]:+.2f}$")
    log.info(f"  spot[149]={spot_range[149]:.2f}$  P&L={pnl_mid[149]:+.2f}$")
    log.info(f"  spot[199]={spot_range[199]:.2f}$  P&L={pnl_mid[199]:+.2f}$")
    log.info(f"  DIAG_MIN_{label}  spot[{idx_min}]={spot_range[idx_min]:.2f}$  P&L={pnl_mid[idx_min]:+.2f}$")


# ── scan headless ─────────────────────────────────────────────────────────────

def run_scan_headless(symbol: str = "SPY", max_combinations: int = 1000,
                      use_american_pricer: bool = True) -> tuple[dict | None, str | None]:
    """
    Lance le scan sans Streamlit.
    Retourne (result_dict, combo_string_premier_combo).
    """
    import config
    from data.models import ScoringCriteria
    from data.provider_yfinance import YFinanceProvider
    from engine.backend import to_cpu, xp
    from engine.combinator import generate_combinations
    from engine.pnl import combinations_to_tensor, compute_pnl_batch
    from scoring.filters import filter_combinations
    from scoring.scorer import score_combinations
    from templates import ALL_TEMPLATES

    DAYS_BC    = 3
    VOL_SCN    = [0.8, 1.0, 1.2]
    RFR        = 0.045
    criteria   = ScoringCriteria(
        max_loss_pct=-10000.0,          # tout passe
        max_loss_probability_pct=100.0,
        min_max_gain_pct=0.0,           # tout passe
        min_gain_loss_ratio=0.0,
        max_net_debit=100_000.0,
        min_avg_volume=0,
    )

    log.info("DIAG_SCAN_START  symbol=%s  max_combos=%d", symbol, max_combinations)

    provider = YFinanceProvider()
    chain    = provider.get_options_chain(symbol)
    spot     = chain.underlying_price
    log.info("DIAG_SCAN_CHAIN  spot=%.2f$  contracts=%d", spot, len(chain.contracts))

    # 1 seul template pour aller vite
    template_name = "calendar_strangle"
    all_combinations = generate_combinations(
        ALL_TEMPLATES[template_name], chain,
        max_combinations=max_combinations,
        min_volume=0,
        max_net_debit=100_000.0,
    )
    log.info("DIAG_SCAN_GENERATED  n=%d", len(all_combinations))
    if not all_combinations:
        log.error("DIAG_SCAN_ERROR  Aucune combinaison générée.")
        return None, None

    spot_range = xp.linspace(
        spot * config.SPOT_RANGE_LOW,
        spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS, dtype=xp.float32,
    )
    tensor     = combinations_to_tensor(all_combinations, days_before_close=DAYS_BC)
    pnl_tensor = compute_pnl_batch(tensor, spot_range, VOL_SCN, RFR,
                                    use_american_pricer=use_american_pricer)

    net_debits  = xp.array([c.net_debit for c in all_combinations], dtype=xp.float32)
    avg_volumes = xp.array([sum(l.volume for l in c.legs) / 4 for c in all_combinations], dtype=xp.float32)

    atm_vols = [min((abs(l.strike - spot), l.implied_vol) for l in c.legs)[1] for c in all_combinations]
    atm_vol_global   = float(np.median(atm_vols)) if atm_vols else 0.20
    days_list        = [(c.close_date - chain.fetch_timestamp.date()).days for c in all_combinations]
    days_close_global = max(1, int(statistics.median(days_list)))

    valid_idx     = filter_combinations(pnl_tensor, spot_range, net_debits, avg_volumes,
                                        criteria, spot, atm_vol_global, days_close_global, RFR)
    valid_idx_cpu = to_cpu(valid_idx)
    log.info("DIAG_SCAN_FILTERED  n=%d", len(valid_idx_cpu))
    if len(valid_idx_cpu) == 0:
        log.error("DIAG_SCAN_ERROR  Aucune combinaison ne passe le filtre.")
        return None, None

    filtered_combos   = [all_combinations[i] for i in valid_idx_cpu]
    pnl_filtered      = pnl_tensor[:, valid_idx, :]
    pnl_mid_filtered  = pnl_filtered[config.VOL_MEDIAN_INDEX]   # (C_f, M)
    net_debits_f      = net_debits[valid_idx]

    scores     = score_combinations(pnl_mid_filtered, net_debits_f, spot_range,
                                    spot, atm_vol_global, days_close_global, RFR)
    scores_cpu = to_cpu(scores)

    spot_range_cpu = to_cpu(spot_range)
    pnl_mid_cpu    = to_cpu(pnl_mid_filtered)                   # (C_f, M) numpy
    safe_debits    = to_cpu(net_debits_f)
    today          = chain.fetch_timestamp.date()

    # Métriques per-combo (même logique que app.py)
    metrics = []
    for i, combo_i in enumerate(filtered_combos):
        atm_vol_i = min((abs(l.strike - spot), l.implied_vol) for l in combo_i.legs)[1]
        days_i    = max(1, (combo_i.close_date - today).days)
        range_i   = atm_vol_i * math.sqrt(days_i / 365.0) * 100
        lo_i      = spot * (1 - range_i / 100)
        hi_i      = spot * (1 + range_i / 100)
        mask_i    = (spot_range_cpu >= lo_i) & (spot_range_cpu <= hi_i)
        max_gain_real = float(pnl_mid_cpu[i][mask_i].max()) if mask_i.any() else float(pnl_mid_cpu[i].max())
        nd_raw    = float(safe_debits[i])
        nd        = abs(nd_raw) if abs(nd_raw) > 1.0 else 1e-6

        metrics.append({
            "max_loss_dollar":     float(pnl_mid_cpu[i].min()),
            "max_gain_real_dollar": max_gain_real,
            "score":               float(scores_cpu[i]),
            "nd_raw":              nd_raw,
            "nd":                  nd,
            "days_to_close":       days_i,
            "atm_vol":             atm_vol_i,
        })

    # Tri par score décroissant
    order           = sorted(range(len(filtered_combos)), key=lambda i: -metrics[i]["score"])
    filtered_combos = [filtered_combos[i] for i in order]
    metrics         = [metrics[i] for i in order]
    pnl_filtered_np = to_cpu(pnl_filtered)[:, order, :]

    # ── Premier combo ──
    combo0   = filtered_combos[0]
    m0       = metrics[0]
    pnl0_mid = pnl_filtered_np[config.VOL_MEDIAN_INDEX, 0, :]  # (M,) numpy

    log.info("DIAG_SCAN_COMBO0_START")
    _log_legs(combo0, "SCAN")
    _log_pnl_diag(pnl0_mid, spot_range_cpu, "SCAN")

    # Cohérence interne : min(pnl0_mid) vs m0["max_loss_dollar"]
    pnl0_min   = float(pnl0_mid.min())
    coherent   = abs(pnl0_min - m0["max_loss_dollar"]) < 0.01
    log.info(
        "DIAG_COHERENCE_SCAN  max_loss_metric=%.2f$  min(pnl0_mid)=%.2f$  ok=%s",
        m0["max_loss_dollar"], pnl0_min, coherent,
    )
    if not coherent:
        log.error("DIAG_INCOHERENCE_SCAN  pnl_for_combo et metrics ne correspondent PAS!")

    combo_string = _combo_to_string(combo0, symbol)
    log.info("DIAG_COMBO0_STRING  %s", combo_string)

    # ── Diagnostic tenseur : compare les entrées pour combo0 dans le batch
    #    vs ce que combinations_to_tensor([combo0]) produit ──────────────────
    tensor_solo = combinations_to_tensor([combo0], days_before_close=DAYS_BC)
    idx_in_batch = int(valid_idx_cpu[order[0]])  # position dans all_combinations

    log.info("DIAG_TENSOR_COMPARE  idx_in_batch=%d", idx_in_batch)
    keys = ["option_types","directions","quantities","strikes",
            "entry_prices","implied_vols","time_to_expiry_at_close","div_yields"]
    for k in keys:
        batch_val = tensor[k][idx_in_batch].tolist()  # shape (L,)
        solo_val  = to_cpu(tensor_solo[k][0]).tolist()
        ok = all(abs(a-b) < 1e-5 for a,b in zip(batch_val, solo_val))
        log.info("DIAG_TENSOR  %-30s  batch=%s  solo=%s  ok=%s",
                 k, [f"{v:.4f}" for v in batch_val],
                 [f"{v:.4f}" for v in solo_val], ok)
        if not ok:
            log.error("DIAG_TENSOR_MISMATCH  %s  BATCH≠SOLO — bug dans combinations_to_tensor!", k)

    # P&L solo directement depuis le tenseur du scan (extraction)
    pnl_solo_from_batch = compute_pnl_batch(
        {k: v[idx_in_batch:idx_in_batch+1] for k, v in tensor.items()},
        spot_range, VOL_SCN, RFR, use_american_pricer=True,
    )
    pnl_solo_from_batch_mid = to_cpu(pnl_solo_from_batch)[config.VOL_MEDIAN_INDEX, 0, :]
    log.info("DIAG_PNL_FROM_BATCH_SLICE (devrait = scan):")
    _log_pnl_diag(pnl_solo_from_batch_mid, spot_range_cpu, "BATCH_SLICE")

    return {
        "combo":            combo0,
        "metric":           m0,
        "pnl_mid":          pnl0_mid,
        "spot_range_cpu":   spot_range_cpu,
        "spot":             spot,
        "tensor":           tensor,
        "idx_in_batch":     idx_in_batch,
    }, combo_string


# ── direct headless ───────────────────────────────────────────────────────────

def run_direct_headless(combo_string: str, symbol: str = "SPY",
                        use_scan_prices: dict | None = None,
                        use_american_pricer: bool = True) -> dict | None:
    """
    Lance la saisie directe pour combo_string.
    Si use_scan_prices est fourni (dict {(exp,strike,type): (mid,iv)}),
    utilise ces prix exacts au lieu de re-fetcher yfinance.
    """
    import config
    from engine.backend import to_cpu, xp
    from engine.pnl import combinations_to_tensor, compute_pnl_batch
    from ui.combo_parser import parse_combo_string

    RFR    = 0.045
    VOL_SCN = [0.8, 1.0, 1.2]
    DAYS_BC = 3

    log.info("DIAG_DIRECT_START  combo=%s", combo_string)

    leg_specs = parse_combo_string(combo_string)
    if not leg_specs:
        log.error("DIAG_DIRECT_ERROR  Parsing échoué.")
        return None

    if use_scan_prices:
        # Construit les legs avec les prix exacts du scan (test de cohérence pure)
        from data.models import Combination, Leg
        from ui.combo_parser import _occ_symbol, _build_combination

        legs = []
        for spec in leg_specs:
            key = (spec["expiration"], spec["strike"], spec["option_type"])
            if key in use_scan_prices:
                entry = use_scan_prices[key]
                mid, iv = entry[0], entry[1]
                dv = entry[2] if len(entry) > 2 else 0.0
                source = "SCAN_PRICES"
            else:
                mid, iv, dv = 0.0, 0.20, 0.0
                source = "NOT_FOUND"
                log.warning("DIAG_DIRECT_LEG_MISSING  %s", key)

            log.info(
                "DIAG_DIRECT_LEG  %s%d %s %g %s  mid=%.4f$  IV=%.2f%%  div_yield=%.4f  source=%s",
                "L" if spec["direction"]>0 else "S", spec["quantity"],
                spec["option_type"], spec["strike"], spec["expiration"],
                mid, iv*100, dv, source,
            )
            legs.append(Leg(
                option_type=spec["option_type"], direction=spec["direction"],
                quantity=spec["quantity"], strike=spec["strike"],
                expiration=spec["expiration"], entry_price=mid, implied_vol=iv,
                div_yield=dv,
                contract_symbol=_occ_symbol(spec["symbol"], spec["expiration"],
                                            spec["option_type"], spec["strike"]),
            ))
        combination = _build_combination(legs)
        spot = None  # sera défini après fetch
        log.info("DIAG_DIRECT_NET_DEBIT_SCAN_PRICES  %.4f$", combination.net_debit)

        # Fetch spot seulement
        from data.provider_yfinance import YFinanceProvider
        chain = YFinanceProvider().get_options_chain(symbol)
        spot  = chain.underlying_price
    else:
        # Re-fetch complet yfinance
        from ui.combo_parser import resolve_combo_live
        resolved = resolve_combo_live(leg_specs, symbol)
        if not resolved:
            log.error("DIAG_DIRECT_ERROR  resolve_combo_live échoué.")
            return None
        combination, spot, missing, details = resolved
        if missing:
            log.warning("DIAG_DIRECT_MISSING  %s", missing)
        _log_legs(combination, "DIRECT")

    log.info("DIAG_DIRECT_SPOT  %.2f$", spot)

    spot_range = xp.linspace(
        spot * config.SPOT_RANGE_LOW,
        spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS, dtype=xp.float32,
    )
    tensor     = combinations_to_tensor([combination], days_before_close=DAYS_BC)
    pnl_tensor = compute_pnl_batch(tensor, spot_range, VOL_SCN, RFR,
                                    use_american_pricer=use_american_pricer)

    pnl_for_combo  = to_cpu(pnl_tensor)[:, 0, :]           # (V, M)
    spot_range_cpu = to_cpu(spot_range)
    pnl_mid        = pnl_for_combo[config.VOL_MEDIAN_INDEX]  # (M,)

    _log_pnl_diag(pnl_mid, spot_range_cpu, "DIRECT")
    log.info("DIAG_DIRECT_MAX_LOSS  %.2f$", float(pnl_mid.min()))

    return {
        "combination":    combination,
        "pnl_mid":        pnl_mid,
        "spot_range_cpu": spot_range_cpu,
        "spot":           spot,
        "max_loss":       float(pnl_mid.min()),
    }


# ── comparaison ───────────────────────────────────────────────────────────────

def compare(scan_res: dict, direct_res: dict) -> None:
    log.info("DIAG_COMPARE_START")
    s_loss = scan_res["metric"]["max_loss_dollar"]
    d_loss = direct_res["max_loss"]
    ratio  = d_loss / s_loss if abs(s_loss) > 0.01 else float("inf")
    log.info("DIAG_COMPARE_LOSS  scan=%.2f$  direct=%.2f$  ratio=%.1fx", s_loss, d_loss, ratio)

    for i in [0, 49, 99, 149, 199]:
        sp = scan_res["spot_range_cpu"][i]
        ds = scan_res["pnl_mid"][i]
        dd = direct_res["pnl_mid"][i] if i < len(direct_res["pnl_mid"]) else float("nan")
        log.info("DIAG_COMPARE_SPOT[%3d]  spot=%.2f$  scan=%+.2f$  direct=%+.2f$  diff=%+.2f$",
                 i, sp, ds, dd, dd - ds)

    if abs(ratio) > 2.0:
        log.error(
            "DIAG_COMPARE_MISMATCH  ratio=%.1fx — résultats incohérents. "
            "Vérifier les prix d'entrée et les TTE.", ratio,
        )
    else:
        log.info("DIAG_COMPARE_OK  Résultats cohérents (ratio < 2x).")


# ── main ──────────────────────────────────────────────────────────────────────

def run_test(symbol: str = "SPY", max_combinations: int = 1000,
             use_american_pricer: bool = True, combo_override: str | None = None) -> None:
    pricer_label = "Américain (B-S93)" if use_american_pricer else "Européen (BS)"
    log.info("\n" + "="*60)
    log.info("DIAG_RUN  symbol=%s  pricer=%s", symbol, pricer_label)
    log.info("="*60)

    scan_res, combo_string = run_scan_headless(
        symbol=symbol, max_combinations=max_combinations,
        use_american_pricer=use_american_pricer,
    )
    if not combo_string:
        log.error("Scan échoué, abandon.")
        return

    target_combo = combo_override or combo_string

    # Test B : mêmes prix → différence doit être $0
    log.info("\n--- Test B : MEMES PRIX que scan (doit être identique) ---")
    scan_prices = {
        (leg.expiration, leg.strike, leg.option_type): (leg.entry_price, leg.implied_vol, leg.div_yield)
        for leg in scan_res["combo"].legs
    }
    direct_res_B = run_direct_headless(
        target_combo, symbol=symbol,
        use_scan_prices=scan_prices,
        use_american_pricer=use_american_pricer,
    )
    if direct_res_B and scan_res:
        log.info("\n--- Comparaison B (%s, mêmes prix) ---", pricer_label)
        compare(scan_res, direct_res_B)

    # Test A : re-fetch yfinance (différence attendue = bruit de marché)
    log.info("\n--- Test A : RE-FETCH yfinance ---")
    direct_res_A = run_direct_headless(
        target_combo, symbol=symbol,
        use_american_pricer=use_american_pricer,
    )
    if direct_res_A and scan_res:
        log.info("\n--- Comparaison A (%s, re-fetch) ---", pricer_label)
        compare(scan_res, direct_res_A)


if __name__ == "__main__":
    combo_override = sys.argv[1] if len(sys.argv) > 1 else None

    run_test(symbol="SPY", max_combinations=1000, use_american_pricer=True,
             combo_override=combo_override)
    run_test(symbol="SPY", max_combinations=1000, use_american_pricer=False,
             combo_override=combo_override)
