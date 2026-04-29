"""Application Streamlit principale — Options P&L Scanner."""

import time
from datetime import date, timedelta

import numpy as np
import streamlit as st

import config
from data.provider_yfinance import YFinanceProvider
from engine.backend import to_cpu, to_xp
from engine.backend import xp
from engine.combinator import generate_combinations
from engine.pnl import combinations_to_tensor, compute_pnl_batch
from scoring.filters import filter_combinations, realistic_max_gain
from scoring.probability import compute_loss_probability
from scoring.scorer import score_combinations
from templates import ALL_TEMPLATES
from ui.components.chart import plot_pnl_profile
from ui.components.combo_detail import render_combo_detail
from ui.components.results_table import render_results_table
from ui.components.sidebar import render_sidebar

st.set_page_config(
    page_title="Options P&L Scanner",
    page_icon="📈",
    layout="wide",
)


def run_scan(params: dict, symbol: str, event_calendar=None) -> dict:
    """Exécute le pipeline complet pour un seul symbol et retourne les résultats."""
    criteria = params["criteria"]
    vol_scenarios = [params["vol_low"], 1.0, params["vol_high"]]
    rfr = params["risk_free_rate"]
    selected = params["selected_templates"]

    progress = st.progress(0, text=f"[{symbol}] Chargement des données...")
    t_start = time.perf_counter()

    # 1. Chargement des données
    provider = YFinanceProvider()
    chain = provider.get_options_chain(symbol)
    spot = chain.underlying_price

    progress.progress(15, text=f"[{symbol}] Génération des combinaisons...")

    # 2. Génération des combinaisons
    max_combinations = params.get("max_combinations", config.MAX_COMBINATIONS)
    all_combinations = []
    for tmpl_name in selected:
        template = ALL_TEMPLATES[tmpl_name]
        combos = generate_combinations(
            template, chain,
            event_calendar=event_calendar,
            max_combinations=max_combinations,
            min_volume=criteria.min_avg_volume,
            max_net_debit=criteria.max_net_debit,
            near_expiry_range=params.get("near_expiry_range"),
            far_expiry_range=params.get("far_expiry_range"),
        )
        all_combinations.extend(combos)

    if not all_combinations:
        progress.empty()
        return {"error": "Aucune combinaison générée. Vérifiez le ticker et les templates."}

    progress.progress(30, text=f"Calcul GPU ({len(all_combinations):,} combinaisons)...")

    # 3. Tenseurs GPU
    spot_range = xp.linspace(
        spot * config.SPOT_RANGE_LOW,
        spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS,
        dtype=xp.float32,
    )
    tensor = combinations_to_tensor(all_combinations,
                                    days_before_close=params.get("days_before_close", 3))

    # 4. Calcul P&L batch
    pnl_tensor = compute_pnl_batch(
        tensor, spot_range, vol_scenarios, rfr,
        use_american_pricer=params.get("use_american_pricer", True),
    )

    progress.progress(70, text="Filtrage...")

    # 5. Filtrage
    net_debits = xp.array([c.net_debit for c in all_combinations], dtype=xp.float32)
    avg_volumes = xp.array(
        [sum(l.volume for l in c.legs) / 4 for c in all_combinations],
        dtype=xp.float32,
    )

    # ATM vol : médiane des vols des contrats les plus proches du spot
    atm_vols = [
        min(
            (abs(l.strike - spot), l.implied_vol)
            for l in c.legs
        )[1]
        for c in all_combinations
    ]
    atm_vol = float(np.median(atm_vols)) if atm_vols else 0.20

    # days_to_close : médiane des close_dates
    import statistics
    days_list = [(c.close_date - chain.fetch_timestamp.date()).days for c in all_combinations]
    days_to_close = max(1, int(statistics.median(days_list)))

    valid_indices = filter_combinations(
        pnl_tensor, spot_range, net_debits, avg_volumes,
        criteria, spot, atm_vol, days_to_close, rfr,
    )
    valid_indices_cpu = to_cpu(valid_indices)

    progress.progress(85, text="Scoring...")

    # 6. Scoring
    if len(valid_indices_cpu) == 0:
        progress.empty()
        t_total = time.perf_counter() - t_start
        return {
            "combinations": [],
            "metrics": [],
            "n_tested": len(all_combinations),
            "n_found": 0,
            "gpu_time_s": t_total,
            "pnl_tensor": None,
            "spot_range": to_cpu(spot_range),
            "spot": spot,
        }

    filtered_combos = [all_combinations[i] for i in valid_indices_cpu]
    pnl_filtered = pnl_tensor[:, valid_indices, :]    # (V, C_f, M)
    pnl_mid_filtered = pnl_filtered[config.VOL_MEDIAN_INDEX]  # (C_f, M)
    net_debits_f = net_debits[valid_indices]

    event_factors = xp.array(
        [all_combinations[i].event_score_factor for i in valid_indices_cpu],
        dtype=xp.float32,
    )

    scores = score_combinations(
        pnl_mid_filtered, net_debits_f, spot_range,
        spot, atm_vol, days_to_close, rfr,
        event_score_factors=event_factors,
    )
    scores_cpu = to_cpu(scores)

    # Métriques individuelles pour le tableau
    safe_debits = to_cpu(net_debits_f)
    pnl_mid_cpu = to_cpu(pnl_mid_filtered)
    loss_probs = to_cpu(compute_loss_probability(
        pnl_mid_filtered, spot_range, spot, atm_vol, days_to_close, rfr
    ))

    import math
    spot_range_cpu = to_cpu(spot_range)
    today = chain.fetch_timestamp.date()

    metrics = []
    for i in range(len(filtered_combos)):
        combo_i = filtered_combos[i]
        max_loss = float(pnl_mid_cpu[i].min())
        max_gain = float(pnl_mid_cpu[i].max())

        # ±1σ per-combo : IV du leg le plus proche du spot + jours propres au combo
        atm_vol_i = min((abs(l.strike - spot), l.implied_vol) for l in combo_i.legs)[1]
        days_i    = max(1, (combo_i.close_date - today).days)
        range_i   = atm_vol_i * math.sqrt(days_i / 365.0) * 100
        lo_i      = spot * (1 - range_i / 100)
        hi_i      = spot * (1 + range_i / 100)
        mask_i    = (spot_range_cpu >= lo_i) & (spot_range_cpu <= hi_i)
        real_pnl  = pnl_mid_cpu[i][mask_i]
        max_gain_real = float(real_pnl.max()) if mask_i.any() else max_gain

        nd_raw = float(safe_debits[i])
        nd = abs(nd_raw) if abs(nd_raw) > 1.0 else 1e-6  # abs : évite inversion crédit

        metrics.append({
            "max_loss_pct":         max_loss      / nd * 100,
            "loss_prob_pct":        loss_probs[i] * 100,
            "max_gain_pct":         max_gain      / nd * 100,
            "max_gain_real_pct":    max_gain_real / nd * 100,
            "gain_loss_ratio":      max_gain_real / abs(max_loss) if max_loss != 0 else 0,
            "score":                float(scores_cpu[i]),
            "realistic_range_pct":  range_i,
            "max_gain_real_dollar": max_gain_real,
            "days_to_close":        days_i,
            "daily_gain_dollar":    max_gain_real / days_i,
        })

    # Tri par score décroissant
    order = sorted(range(len(filtered_combos)), key=lambda i: -metrics[i]["score"])
    filtered_combos = [filtered_combos[i] for i in order]
    metrics = [metrics[i] for i in order]
    pnl_filtered_np = to_cpu(pnl_filtered)[:, order, :]

    progress.progress(100, text="Terminé.")
    progress.empty()

    t_total = time.perf_counter() - t_start
    return {
        "combinations": filtered_combos,
        "metrics": metrics,
        "n_tested": len(all_combinations),
        "n_found": len(filtered_combos),
        "gpu_time_s": t_total,
        "pnl_tensor": pnl_filtered_np,   # (V, C_f, M) numpy
        "spot_range": to_cpu(spot_range),
        "spot": spot,
        "days_before_close": params.get("days_before_close", 3),
        "realistic_range_pct": None,  # désormais per-combo dans metrics[i]
    }


def run_multi_scan(params: dict) -> dict:
    """Lance run_scan pour chaque symbol, agrège et retourne le top 100 par score."""
    symbols = params["symbols"]
    all_entries = []
    n_tested_total = 0

    # Chargement unique du calendrier événementiel (une seule requête Finnhub)
    from events.calendar import EventCalendar
    event_calendar = EventCalendar()
    far_max = params.get("far_expiry_range", config.SCANNER_FAR_EXPIRY_RANGE)[1]
    try:
        event_calendar.load(
            from_date=date.today(),
            to_date=date.today() + timedelta(days=far_max + 7),
        )
    except Exception:
        event_calendar = None

    for symbol in symbols:
        result = run_scan(params, symbol, event_calendar=event_calendar)
        if "error" in result:
            continue
        n_tested_total += result["n_tested"]
        for j in range(result["n_found"]):
            all_entries.append({
                "symbol": symbol,
                "combo": result["combinations"][j],
                "metric": result["metrics"][j],
                "pnl": result["pnl_tensor"][:, j, :],
                "spot_range": result["spot_range"],
                "spot": result["spot"],
            })

    if not all_entries:
        return {"error": "Aucune combinaison trouvée pour les sous-jacents donnés."}

    all_entries.sort(key=lambda x: -x["metric"]["score"])
    all_entries = all_entries[:100]

    return {
        "combinations": [e["combo"] for e in all_entries],
        "metrics": [e["metric"] for e in all_entries],
        "symbols": [e["symbol"] for e in all_entries],
        "pnl_tensor": np.stack([e["pnl"] for e in all_entries], axis=1),  # (V, C, M) — WARN: diff spot ranges
        "pnl_per_combo": [e["pnl"] for e in all_entries],   # (V, M) per combo
        "spot_ranges": [e["spot_range"] for e in all_entries],
        "spots": [e["spot"] for e in all_entries],
        "n_tested": n_tested_total,
        "n_found": len(all_entries),
        "gpu_time_s": 0.0,
    }


def _find_combo_in_results(leg_specs: list[dict], results: dict | None):
    """
    Cherche dans les résultats du scan en cours le combo correspondant aux leg_specs.
    Retourne (index, results) si trouvé, None sinon.
    Correspondance : même (expiration, strike, option_type, direction, quantity) pour chaque leg.
    """
    if not results or "combinations" not in results:
        return None
    for idx, combo in enumerate(results["combinations"]):
        if len(combo.legs) != len(leg_specs):
            continue
        matched = all(
            leg.expiration.isoformat() == spec["expiration"].isoformat()
            and abs(leg.strike - spec["strike"]) < 0.01
            and leg.option_type == spec["option_type"]
            and leg.direction == spec["direction"]
            and leg.quantity == spec["quantity"]
            for leg, spec in zip(combo.legs, leg_specs)
        )
        if matched:
            return idx, results
    return None


def main():
    st.title("Options P&L Scanner")

    params = render_sidebar()

    # Routage Live / Backtest / Tracker
    if params.get("mode") == "backtest":
        from ui.page_backtest import render_backtest_page
        render_backtest_page(params)
        return

    if params.get("mode") == "tracker":
        from ui.page_tracker import render_tracker_page
        render_tracker_page()
        return

    # État de session
    if "results" not in st.session_state:
        st.session_state.results = None
    if "selected_combo_idx" not in st.session_state:
        st.session_state.selected_combo_idx = 0

    # ── Saisie directe d'un combo (FEAT-021) ───────────────────────────────
    from ui.combo_parser import parse_combo_string, resolve_combo_live, build_single_combo_results
    with st.expander("Saisir un combo directement (sans scan)", expanded=False):
        st.caption(
            "Format : `L1 call SPY 17JUL2026 715 | L2 put SPY 17JUL2026 690 | "
            "S1 call SPY 15MAY2026 745 | S2 put SPY 15MAY2026 672`  "
            "(copier depuis la page Tracker)"
        )
        combo_text = st.text_area("Combo", height=68, key="live_combo_input",
                                  placeholder="L1 call SPY 17JUL2026 715 | S1 put SPY 15MAY2026 672")
        if st.button("Analyser ce combo", key="live_analyze_combo"):
            leg_specs = parse_combo_string(combo_text)
            if not leg_specs:
                st.error("Format invalide. Exemple : L1 call SPY 17JUL2026 715 | S1 put SPY 15MAY2026 672")
            else:
                symbol = leg_specs[0]["symbol"]
                # Chercher d'abord dans les résultats du scan en cours
                existing = _find_combo_in_results(
                    leg_specs, st.session_state.get("results")
                )
                if existing is not None:
                    combo_idx, scan_results = existing
                    st.session_state.results = scan_results
                    st.session_state.selected_combo_idx = combo_idx
                    st.session_state["_combo_warnings"] = [
                        "Combo trouvé dans les résultats du scan en cours — "
                        "prix du scan utilisés directement (pas de re-fetch)."
                    ]
                    st.session_state["_combo_leg_details"] = []
                    st.session_state["_combo_net_debit"] = None
                    st.rerun()
                else:
                    with st.spinner(f"Chargement des prix {symbol} (yfinance)…"):
                        resolved = resolve_combo_live(leg_specs, symbol)
                    if resolved:
                        combination, spot, missing, details = resolved
                        result = build_single_combo_results(combination, spot, symbol, params)
                        st.session_state.results = result
                        st.session_state.selected_combo_idx = 0
                        warnings = result["metrics"][0].get("_warnings", [])
                        if missing:
                            warnings.insert(0,
                                f"⚠ {len(missing)} leg(s) non trouvé(s) dans la chaîne "
                                f"yfinance (prix=0, P&L incorrect) : {', '.join(missing)}"
                            )
                        warnings.append(
                            "Combo non présent dans le scan courant — prix re-fetchés "
                            "depuis yfinance. Relancer le scan pour des prix cohérents."
                        )
                        st.session_state["_combo_warnings"] = warnings
                        st.session_state["_combo_leg_details"] = details
                        st.session_state["_combo_net_debit"] = combination.net_debit
                        st.rerun()

    # ── Bouton Lancer le scan (FEAT-020) ────────────────────────────────────
    scan_clicked = st.button("🔍 Lancer le scan", type="primary", key="live_scan_btn")
    if scan_clicked:
        if not params["symbols"]:
            st.error("Entrez au moins un ticker.")
        elif not params["selected_templates"]:
            st.error("Sélectionnez au moins un template.")
        else:
            with st.spinner("Scan en cours..."):
                try:
                    st.session_state.results = run_multi_scan(params)
                    st.session_state.selected_combo_idx = 0
                except Exception as e:
                    st.error(f"Erreur : {e}")
                    st.session_state.results = None

    results = st.session_state.results

    # Avertissements et détails persistés depuis la saisie directe
    warnings = st.session_state.pop("_combo_warnings", [])
    details  = st.session_state.pop("_combo_leg_details", [])
    nd_info  = st.session_state.pop("_combo_net_debit", None)
    for w in warnings:
        st.warning(w) if "non trouvé" in w else st.info(w)
    if details:
        import pandas as pd
        nd_txt = f" | Net debit calculé : **{nd_info:+.2f}$**" if nd_info is not None else ""
        st.caption(f"Prix utilisés par la saisie directe (yfinance){nd_txt} — comparer avec les prix du scan ci-dessous :")
        st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)

    if results is None:
        st.info("Configurez les paramètres dans la barre latérale puis cliquez sur **Lancer le scan**.")
        return

    if "error" in results:
        st.error(results["error"])
        return

    # Résumé
    col1, col2, col3 = st.columns(3)
    col1.metric("Combinaisons testées", f"{results['n_tested']:,}")
    col2.metric("Résultats trouvés", f"{results['n_found']:,}")
    col3.metric("Temps total", f"{results['gpu_time_s']:.2f}s")

    if results["n_found"] == 0:
        st.warning("Aucune combinaison ne satisfait les critères. Essayez d'assouplir les filtres.")
        return

    st.markdown("---")

    # Graphique P&L de la combinaison sélectionnée
    idx = st.session_state.selected_combo_idx
    combo = results["combinations"][idx]
    m = results["metrics"][idx]
    pnl_for_combo = results["pnl_per_combo"][idx]   # (V, M)

    fig = plot_pnl_profile(
        combination=combo,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        loss_prob=m["loss_prob_pct"] / 100,
        max_loss_pct=m["max_loss_pct"],
        max_gain_pct=m["max_gain_pct"],
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Tableau des résultats
    selected = render_results_table(results["combinations"], results["metrics"],
                                    results.get("symbols"),
                                    realistic_range_pct=results.get("realistic_range_pct"))
    if selected is not None and selected != st.session_state.selected_combo_idx:
        st.session_state.selected_combo_idx = selected
        st.rerun()

    st.markdown("---")

    # Détails de la combinaison
    symbols = results.get("symbols")
    combo_symbol = symbols[idx] if symbols else (results.get("symbol") or None)
    render_combo_detail(
        combo, m,
        symbol=combo_symbol,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        days_before_close=results.get("days_before_close", 3),
    )


if __name__ == "__main__":
    main()
