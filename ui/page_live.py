"""Page Live — scan en temps réel + vue grille/unique des résultats."""

from __future__ import annotations

import statistics
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
from scoring.metrics import compute_combo_metrics
from scoring.probability import compute_loss_probability
from scoring.scorer import score_combinations
from templates import ALL_TEMPLATES
from ui.components.chart import plot_pnl_profile, plot_pnl_mini
from ui.components.combo_detail import render_combo_detail
from ui.components.results_table import render_results_table

_GRID_ROWS = 4
_GRID_COLS = 6
_PAGE_SIZE = _GRID_ROWS * _GRID_COLS  # 24


def run_scan(params: dict, symbol: str, event_calendar=None) -> dict:
    """Pipeline complet pour un seul symbol."""
    criteria = params["criteria"]
    vol_scenarios = [params["vol_low"], 1.0, params["vol_high"]]
    rfr = params["risk_free_rate"]
    selected = params["selected_templates"]

    progress = st.progress(0, text=f"[{symbol}] Chargement des données...")
    t_start = time.perf_counter()

    provider = YFinanceProvider()
    chain = provider.get_options_chain(symbol)
    spot = chain.underlying_price

    progress.progress(15, text=f"[{symbol}] Génération des combinaisons...")

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

    spot_range = xp.linspace(
        spot * config.SPOT_RANGE_LOW,
        spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS,
        dtype=xp.float32,
    )
    tensor = combinations_to_tensor(all_combinations,
                                    days_before_close=params.get("days_before_close", 3))

    pnl_tensor = compute_pnl_batch(
        tensor, spot_range, vol_scenarios, rfr,
        use_american_pricer=params.get("use_american_pricer", True),
    )

    progress.progress(70, text="Filtrage...")

    net_debits = xp.array([c.net_debit for c in all_combinations], dtype=xp.float32)
    avg_volumes = xp.array(
        [sum(l.volume for l in c.legs) / 4 for c in all_combinations],
        dtype=xp.float32,
    )

    atm_vols = [
        min((abs(l.strike - spot), l.implied_vol) for l in c.legs)[1]
        for c in all_combinations
    ]
    atm_vol = float(np.median(atm_vols)) if atm_vols else 0.20
    days_list = [(c.close_date - chain.fetch_timestamp.date()).days for c in all_combinations]
    days_to_close = max(1, int(statistics.median(days_list)))

    valid_indices = filter_combinations(
        pnl_tensor, spot_range, net_debits, avg_volumes,
        criteria, spot, atm_vol, days_to_close, rfr,
    )
    valid_indices_cpu = to_cpu(valid_indices)

    progress.progress(85, text="Scoring...")

    if len(valid_indices_cpu) == 0:
        progress.empty()
        t_total = time.perf_counter() - t_start
        return {
            "combinations": [], "metrics": [], "n_tested": len(all_combinations),
            "n_found": 0, "gpu_time_s": t_total,
            "pnl_tensor": None, "spot_range": to_cpu(spot_range), "spot": spot,
        }

    filtered_combos = [all_combinations[i] for i in valid_indices_cpu]
    pnl_filtered = pnl_tensor[:, valid_indices, :]
    pnl_mid_filtered = pnl_filtered[config.VOL_MEDIAN_INDEX]
    net_debits_f = net_debits[valid_indices]

    event_factors = xp.array(
        [all_combinations[i].event_score_factor for i in valid_indices_cpu],
        dtype=xp.float32,
    )

    today = chain.fetch_timestamp.date()
    weights = params.get("score_weights") or config.SCORE_WEIGHTS_DEFAULT

    metrics_batch = compute_combo_metrics(
        filtered_combos, pnl_filtered, spot_range, net_debits_f,
        current_spot=spot, today=today, risk_free_rate=rfr,
        atm_vol_global=atm_vol, days_to_close_global=days_to_close,
    )

    scores = score_combinations(metrics_batch, weights, event_score_factors=event_factors)
    scores_cpu = to_cpu(scores)

    safe_debits = to_cpu(net_debits_f)
    pnl_mid_cpu = to_cpu(pnl_mid_filtered)
    max_loss_pct_cpu = to_cpu(metrics_batch.max_loss_pct)
    max_gain_real_pct_cpu = to_cpu(metrics_batch.max_gain_real_pct)
    annualized_pct_cpu = to_cpu(metrics_batch.annualized_return_pct)
    loss_prob_cpu = to_cpu(metrics_batch.loss_prob)
    liquidity_cpu = to_cpu(metrics_batch.liquidity_score)
    vol_disp_cpu = to_cpu(metrics_batch.vol_dispersion_pct)
    slippage_cpu = to_cpu(metrics_batch.slippage_pct)
    days_close_cpu = to_cpu(metrics_batch.days_to_close)
    max_gain_real_dollar_cpu = to_cpu(metrics_batch.max_gain_real_dollar)
    max_loss_dollar_cpu = to_cpu(metrics_batch.max_loss_dollar)
    daily_gain_cpu = to_cpu(metrics_batch.daily_gain_dollar)
    realistic_range_cpu = to_cpu(metrics_batch.realistic_range_pct)
    atm_vol_per_cpu = to_cpu(metrics_batch.atm_vol_per_combo)
    capital_required_cpu = to_cpu(metrics_batch.capital_required)

    metrics = []
    for i in range(len(filtered_combos)):
        max_loss_d = float(max_loss_dollar_cpu[i])
        max_gain_d = float(pnl_mid_cpu[i].max())
        max_gain_real_d = float(max_gain_real_dollar_cpu[i])
        cap_req = float(capital_required_cpu[i])
        metrics.append({
            "max_loss_pct":          float(max_loss_pct_cpu[i]),
            "loss_prob_pct":         float(loss_prob_cpu[i]) * 100,
            "max_gain_pct":          max_gain_d / cap_req * 100,
            "max_gain_real_pct":     float(max_gain_real_pct_cpu[i]),
            "annualized_return_pct": float(annualized_pct_cpu[i]),
            "liquidity_score":       float(liquidity_cpu[i]),
            "vol_dispersion_pct":    float(vol_disp_cpu[i]),
            "slippage_pct":          float(slippage_cpu[i]),
            "gain_loss_ratio":       max_gain_real_d / abs(max_loss_d) if max_loss_d != 0 else 0,
            "score":                 float(scores_cpu[i]),
            "realistic_range_pct":   float(realistic_range_cpu[i]),
            "max_gain_real_dollar":  max_gain_real_d,
            "capital_required":      cap_req,
            "days_to_close":         int(days_close_cpu[i]),
            "daily_gain_dollar":     float(daily_gain_cpu[i]),
            "_atm_vol_pct":          f"{float(atm_vol_per_cpu[i])*100:.1f}%",
            "_nd_raw":               float(safe_debits[i]),
            "_nd_used":              cap_req,
            "_max_loss_dollar":      max_loss_d,
        })

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
        "pnl_per_combo": [pnl_filtered_np[:, i, :] for i in range(len(filtered_combos))],
        "spot_ranges": [to_cpu(spot_range)] * len(filtered_combos),
        "spots": [spot] * len(filtered_combos),
        "pnl_tensor": pnl_filtered_np,
        "spot_range": to_cpu(spot_range),
        "spot": spot,
        "days_before_close": params.get("days_before_close", 3),
        "realistic_range_pct": None,
    }


def run_multi_scan(params: dict) -> dict:
    """Lance run_scan pour chaque symbol, agrège et retourne le top 100 par score."""
    symbols = params["symbols"]
    all_entries = []
    n_tested_total = 0

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
                "pnl": result["pnl_per_combo"][j],
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
        "pnl_per_combo": [e["pnl"] for e in all_entries],
        "spot_ranges": [e["spot_range"] for e in all_entries],
        "spots": [e["spot"] for e in all_entries],
        "pnl_tensor": np.stack([e["pnl"] for e in all_entries], axis=1),
        "n_tested": n_tested_total,
        "n_found": len(all_entries),
        "gpu_time_s": 0.0,
    }


def _combo_title_std(combo, symbol: str | None) -> str:
    """Retourne le nom standard du combo : 'L3 call SPY 17JUL2026 720 | ...'."""
    ticker_part = f" {symbol}" if symbol else ""
    return " | ".join(
        f"{'L' if l.direction == 1 else 'S'}{l.quantity}"
        f" {l.option_type}{ticker_part}"
        f" {l.expiration.strftime('%d%b%Y').upper()} {l.strike:g}"
        for l in combo.legs
    )


def _render_grid(results: dict, tab_suffix: str, params: dict) -> None:
    """Affiche les résultats en vue grille 4×6. Sélection par clic sur le graphe."""
    selected_key = "live_selected_idx" if tab_suffix == "live" else "bt_selected_idx"
    if selected_key not in st.session_state:
        st.session_state[selected_key] = 0

    n_total = results["n_found"]
    page_key = f"grid_page_{tab_suffix}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    page = int(st.session_state[page_key])
    n_pages = max(1, (n_total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(page, n_pages - 1)
    st.session_state[page_key] = page

    start = page * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, n_total)

    col_prev, col_info, col_next = st.columns([1, 5, 1])
    with col_prev:
        if page > 0 and st.button("◀ Préc.", key=f"prev_{tab_suffix}"):
            st.session_state[page_key] = page - 1
            st.rerun()
    with col_info:
        st.caption(f"Résultats {start+1}–{end} sur {n_total} (page {page+1}/{n_pages})")
    with col_next:
        if end < n_total and st.button("Suiv. ▶", key=f"next_{tab_suffix}"):
            st.session_state[page_key] = page + 1
            st.rerun()

    symbols_list = results.get("symbols") or [None] * n_total

    current_selected = st.session_state.get(selected_key, 0)
    newly_selected: int | None = None

    for row in range(_GRID_ROWS):
        cols = st.columns(_GRID_COLS)
        for col_idx in range(_GRID_COLS):
            item_pos = row * _GRID_COLS + col_idx
            combo_idx = start + item_pos
            if combo_idx >= end:
                break
            with cols[col_idx]:
                combo = results["combinations"][combo_idx]
                m = results["metrics"][combo_idx]
                pnl = results["pnl_per_combo"][combo_idx]
                spot = results["spots"][combo_idx]
                symbol = symbols_list[combo_idx]
                is_selected = (combo_idx == current_selected)

                title_std = _combo_title_std(combo, symbol)
                fig = plot_pnl_mini(
                    combo, pnl, results["spot_ranges"][combo_idx], spot, symbol,
                    title=title_std,
                )

                with st.container(border=is_selected):
                    st.plotly_chart(
                        fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"mini_{tab_suffix}_{combo_idx}",
                    )
                    btn_label = (
                        f"{'▶ ' if is_selected else ''}#{combo_idx+1} · "
                        f"{m['score']:.2f} · ±1σ ${m['max_gain_real_dollar']:+.0f}"
                    )
                    if st.button(btn_label, key=f"sel_{tab_suffix}_{combo_idx}",
                                 use_container_width=True,
                                 type="primary" if is_selected else "secondary"):
                        newly_selected = combo_idx

    # Rerun hors de tout contexte colonne/container pour fiabilité
    if newly_selected is not None and newly_selected != current_selected:
        st.session_state[selected_key] = newly_selected
        st.rerun()


def _render_grid_details_compact(results: dict, selected_idx_key: str,
                                 days_before_close: int, as_of=None) -> None:
    """Détails du combo sélectionné sans le grand graphe P&L — utilisé sous la grille."""
    idx = st.session_state.get(selected_idx_key, 0)
    idx = min(idx, results["n_found"] - 1)
    combo = results["combinations"][idx]
    m = results["metrics"][idx]
    pnl_for_combo = results["pnl_per_combo"][idx]

    symbols_list = results.get("symbols")
    combo_symbol = symbols_list[idx] if symbols_list else results.get("symbol")

    title_std = _combo_title_std(combo, combo_symbol)
    st.code(title_std, language=None)

    render_combo_detail(
        combo, m,
        symbol=combo_symbol,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        days_before_close=days_before_close,
        **({"as_of": as_of} if as_of is not None else {}),
    )


def _render_grid_details(results: dict, selected_idx_key: str,
                         days_before_close: int, as_of=None) -> None:
    """Affiche graphe + détails du combo sélectionné — utilisé sous la grille."""
    idx = st.session_state.get(selected_idx_key, 0)
    idx = min(idx, results["n_found"] - 1)
    combo = results["combinations"][idx]
    m = results["metrics"][idx]
    pnl_for_combo = results["pnl_per_combo"][idx]

    symbols_list = results.get("symbols")
    combo_symbol = symbols_list[idx] if symbols_list else results.get("symbol")

    title_std = _combo_title_std(combo, combo_symbol)
    st.code(title_std, language=None)

    fig = plot_pnl_profile(
        combination=combo,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        loss_prob=m["loss_prob_pct"] / 100,
        max_loss_pct=m["max_loss_pct"],
        max_gain_pct=m["max_gain_pct"],
        symbol=combo_symbol,
    )
    st.plotly_chart(fig, use_container_width=True)

    render_combo_detail(
        combo, m,
        symbol=combo_symbol,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        days_before_close=days_before_close,
        **({"as_of": as_of} if as_of is not None else {}),
    )


def _render_single(results: dict, selected_idx_key: str, params: dict,
                   days_before_close: int, as_of=None) -> None:
    """Affiche le graphe principal + table + détails pour le combo sélectionné."""
    idx = st.session_state.get(selected_idx_key, 0)
    combo = results["combinations"][idx]
    m = results["metrics"][idx]
    pnl_for_combo = results["pnl_per_combo"][idx]

    symbols_list = results.get("symbols")
    combo_symbol = symbols_list[idx] if symbols_list else results.get("symbol")
    ticker_part = f" {combo_symbol}" if combo_symbol else ""
    combo_name_std = " | ".join(
        f"{'L' if leg.direction == 1 else 'S'}{leg.quantity} "
        f"{leg.option_type}{ticker_part} "
        f"{leg.expiration.strftime('%d%b%Y').upper()} "
        f"{leg.strike:g}"
        for leg in combo.legs
    )
    st.code(combo_name_std, language=None)

    fig = plot_pnl_profile(
        combination=combo,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        loss_prob=m["loss_prob_pct"] / 100,
        max_loss_pct=m["max_loss_pct"],
        max_gain_pct=m["max_gain_pct"],
        symbol=combo_symbol,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    top_spot = results["spots"][0] if results.get("spots") else results.get("spot")
    selected = render_results_table(
        results["combinations"], results["metrics"],
        results.get("symbols"),
        realistic_range_pct=results.get("realistic_range_pct"),
        spot=top_spot,
    )
    if selected is not None and selected != st.session_state.get(selected_idx_key, 0):
        st.session_state[selected_idx_key] = selected
        st.rerun()

    st.markdown("---")

    render_combo_detail(
        combo, m,
        symbol=combo_symbol,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        days_before_close=days_before_close,
        **({"as_of": as_of} if as_of is not None else {}),
    )


def render_live_page(base_params: dict) -> None:
    """Page Live complète."""
    # ── Injection ticker depuis screener ───────────────────────────────────
    if "live_symbols_input" not in st.session_state:
        st.session_state["live_symbols_input"] = "SPY"

    # ── Ticker input ────────────────────────────────────────────────────────
    raw = st.text_input(
        "Sous-jacent(s)",
        key="live_symbols_input",
        help="Un ou plusieurs tickers séparés par des virgules : SPY,AAPL,NVDA",
        placeholder="SPY",
    )
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    params = {**base_params, "symbols": symbols, "mode": "live"}

    # ── Saisie directe d'un combo ──────────────────────────────────────────
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
                with st.spinner(f"Chargement des prix {symbol} (yfinance)…"):
                    resolved = resolve_combo_live(leg_specs, symbol)
                if resolved:
                    combination, spot, missing, details = resolved
                    result = build_single_combo_results(combination, spot, symbol, params)
                    st.session_state.live_results = result
                    st.session_state.live_selected_idx = 0
                    st.session_state.view_mode_live = "Vue unique"
                    st.session_state.grid_page_live = 0
                    warnings = result["metrics"][0].get("_warnings", [])
                    if missing:
                        warnings.insert(0,
                            f"⚠ {len(missing)} leg(s) non trouvé(s) dans la chaîne "
                            f"yfinance (prix=0, P&L faussé) : {', '.join(missing)}"
                        )
                    st.session_state["_live_warnings"] = warnings
                    st.session_state["_live_leg_details"] = details
                    st.session_state["_live_net_debit"] = combination.net_debit
                    st.rerun()

    # ── Bouton Scan ─────────────────────────────────────────────────────────
    scan_clicked = st.button("🔍 Lancer le scan", type="primary", key="live_scan_btn")
    if scan_clicked:
        if not symbols:
            st.error("Entrez au moins un ticker.")
        elif not params["selected_templates"]:
            st.error("Sélectionnez au moins un template dans la page Paramètres.")
        else:
            with st.spinner("Scan en cours..."):
                try:
                    st.session_state.live_results = run_multi_scan(params)
                    st.session_state.live_selected_idx = 0
                    st.session_state.grid_page_live = 0
                    if "view_mode_live" not in st.session_state:
                        st.session_state.view_mode_live = "Grille"
                except Exception as e:
                    st.error(f"Erreur : {e}")
                    st.session_state.live_results = None

    results = st.session_state.get("live_results")

    # ── Warnings saisie directe ────────────────────────────────────────────
    warnings = st.session_state.pop("_live_warnings", [])
    details  = st.session_state.pop("_live_leg_details", [])
    nd_info  = st.session_state.pop("_live_net_debit", None)
    for w in warnings:
        st.warning(w) if "non trouvé" in w else st.info(w)
    if details:
        import pandas as pd
        res = st.session_state.get("live_results") or {}
        m0  = (res.get("metrics") or [{}])[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net debit brut ($)", f"{m0.get('_nd_raw', nd_info or 0):+.4f}" if nd_info is not None else "—")
        c2.metric("Net debit utilisé ($)", f"{m0.get('_nd_used', 0):+.4f}")
        c3.metric("Perte max ($)", f"{m0.get('_max_loss_dollar', 0):+.2f}")
        c4.metric("Gain max ±1σ ($)", f"{m0.get('max_gain_real_dollar', 0):+.2f}")
        st.caption(f"Prix yfinance — IV ATM : {m0.get('_atm_vol_pct','?')}")
        st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)

    if results is None:
        st.info("Configurez les paramètres dans la page **Paramètres** puis cliquez sur **Lancer le scan**.")
        return

    if "error" in results:
        st.error(results["error"])
        return

    # ── Résumé ─────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    col1.metric("Combinaisons testées", f"{results['n_tested']:,}")
    col2.metric("Résultats trouvés", f"{results['n_found']:,}")
    col3.metric("Temps total", f"{results['gpu_time_s']:.2f}s")

    if results["n_found"] == 0:
        st.warning("Aucune combinaison ne satisfait les critères. Essayez d'assouplir les filtres.")
        return

    st.markdown("---")

    # ── Toggle vue ──────────────────────────────────────────────────────────
    if "view_mode_live" not in st.session_state:
        st.session_state["view_mode_live"] = "Grille"
    view_mode = st.radio(
        "Affichage", options=["Grille", "Vue unique"],
        horizontal=True, label_visibility="collapsed",
        key="view_mode_live",
    )

    st.markdown("---")

    if "live_selected_idx" not in st.session_state:
        st.session_state.live_selected_idx = 0
    dbc = results.get("days_before_close", params.get("days_before_close", 3))

    if view_mode == "Grille":
        _render_grid(results, "live", params)
        st.markdown("---")
        sel_tbl = render_results_table(
            results["combinations"], results["metrics"],
            results.get("symbols"),
            spot=results["spots"][0] if results.get("spots") else None,
            selected_row=st.session_state.get("live_selected_idx", 0),
        )
        if sel_tbl is not None and sel_tbl != st.session_state.get("live_selected_idx", 0):
            st.session_state["live_selected_idx"] = sel_tbl
            st.rerun()
        st.markdown("---")
        _render_grid_details_compact(results, "live_selected_idx", days_before_close=dbc)
    else:
        _render_single(results, "live_selected_idx", params, days_before_close=dbc)
