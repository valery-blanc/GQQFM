"""Page Streamlit : scan historique + replay 30 j via Polygon."""

from __future__ import annotations

import statistics
import time
from datetime import date

import numpy as np
import plotly.graph_objects as go
import streamlit as st

import config
from backtesting import backtest_combo
from data.provider_polygon import PolygonHistoricalProvider, resolve_polygon_key
from engine.backend import to_cpu, xp
from engine.combinator import generate_combinations
from engine.pnl import combinations_to_tensor, compute_pnl_batch
from scoring.filters import filter_combinations
from scoring.probability import compute_loss_probability
from scoring.scorer import score_combinations
from templates import ALL_TEMPLATES
from ui.components.chart import plot_pnl_profile
from ui.components.combo_detail import render_combo_detail
from ui.components.results_table import render_results_table


def _make_progress_callback(bar, status):
    """Wrap a Streamlit progress bar + status text into a single callback."""
    def cb(progress: float, message: str) -> None:
        bar.progress(min(max(progress, 0.0), 1.0))
        status.caption(message)
    return cb


def run_backtest_scan(params: dict, symbol: str, as_of: date) -> dict:
    """Pipeline scan complet sur une date passée via Massive (ex-Polygon)."""
    from data.risk_free_rate import fetch_historical_risk_free_rate

    criteria = params["criteria"]
    vol_scenarios = [params["vol_low"], 1.0, params["vol_high"]]
    scan_time: str | None = params.get("scan_time")
    selected = params["selected_templates"]

    # ^IRX historique pour le jour de la simulation
    rfr, rfr_src = fetch_historical_risk_free_rate(as_of)

    time_label = f" @ {scan_time} ET" if scan_time else " (EOD)"
    bar = st.progress(0.0, text=f"[{symbol} @ {as_of}{time_label}] Initialisation…")
    status = st.empty()
    cb = _make_progress_callback(bar, status)
    t_start = time.perf_counter()

    cb(0.0, f"^IRX {as_of}: {rfr*100:.3f}% ({rfr_src})")

    provider = PolygonHistoricalProvider()
    chain = provider.get_options_chain(
        symbol, as_of=as_of, progress_callback=cb, scan_time=scan_time,
    )
    spot = chain.underlying_price
    cb(0.97, f"Chain {symbol} : {len(chain.contracts)} contrats — génération combos…")

    # Génération combinaisons
    max_combinations = params.get("max_combinations", config.MAX_COMBINATIONS)
    all_combinations = []
    for tmpl_name in selected:
        template = ALL_TEMPLATES[tmpl_name]
        # event_calendar=None : pas d'historique d'events macro pour le moment
        combos = generate_combinations(
            template, chain,
            as_of=as_of,
            event_calendar=None,
            max_combinations=max_combinations,
            min_volume=criteria.min_avg_volume,
            max_net_debit=criteria.max_net_debit,
            near_expiry_range=params.get("near_expiry_range"),
            far_expiry_range=params.get("far_expiry_range"),
        )
        all_combinations.extend(combos)

    if not all_combinations:
        bar.empty()
        status.empty()
        return {"error": "Aucune combinaison générée. Élargissez les plages DTE ou changez de date."}

    cb(0.98, f"Calcul scan ({len(all_combinations):,} combinaisons)…")

    # Pipeline GPU/CPU identique au live scan
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

    net_debits = xp.array([c.net_debit for c in all_combinations], dtype=xp.float32)
    avg_volumes = xp.array(
        [sum(l.volume for l in c.legs) / 4 for c in all_combinations],
        dtype=xp.float32,
    )
    atm_vols = [
        min(
            (abs(l.strike - spot), l.implied_vol)
            for l in c.legs
        )[1]
        for c in all_combinations
    ]
    atm_vol = float(np.median(atm_vols)) if atm_vols else 0.20
    days_list = [(c.close_date - as_of).days for c in all_combinations]
    days_to_close = max(1, int(statistics.median(days_list)))

    valid_indices = filter_combinations(
        pnl_tensor, spot_range, net_debits, avg_volumes,
        criteria, spot, atm_vol, days_to_close, rfr,
    )
    valid_indices_cpu = to_cpu(valid_indices)

    if len(valid_indices_cpu) == 0:
        bar.empty()
        status.empty()
        return {
            "combinations": [], "metrics": [], "n_tested": len(all_combinations),
            "n_found": 0, "gpu_time_s": time.perf_counter() - t_start,
            "spot": spot, "spot_range": to_cpu(spot_range),
        }

    filtered_combos = [all_combinations[i] for i in valid_indices_cpu]
    pnl_filtered = pnl_tensor[:, valid_indices, :]
    pnl_mid_filtered = pnl_filtered[config.VOL_MEDIAN_INDEX]
    net_debits_f = net_debits[valid_indices]

    event_factors = xp.ones(len(filtered_combos), dtype=xp.float32)
    scores = score_combinations(
        pnl_mid_filtered, net_debits_f, spot_range,
        spot, atm_vol, days_to_close, rfr,
        event_score_factors=event_factors,
    )
    scores_cpu = to_cpu(scores)

    safe_debits = to_cpu(net_debits_f)
    pnl_mid_cpu = to_cpu(pnl_mid_filtered)
    loss_probs = to_cpu(compute_loss_probability(
        pnl_mid_filtered, spot_range, spot, atm_vol, days_to_close, rfr
    ))

    metrics = []
    for i in range(len(filtered_combos)):
        max_loss = pnl_mid_cpu[i].min()
        max_gain = pnl_mid_cpu[i].max()
        nd = safe_debits[i] if safe_debits[i] != 0 else 1e-6
        metrics.append({
            "max_loss_pct": max_loss / nd * 100,
            "loss_prob_pct": loss_probs[i] * 100,
            "max_gain_pct": max_gain / nd * 100,
            "gain_loss_ratio": max_gain / abs(max_loss) if max_loss != 0 else 0,
            "score": float(scores_cpu[i]),
        })

    order = sorted(range(len(filtered_combos)), key=lambda i: -metrics[i]["score"])
    filtered_combos = [filtered_combos[i] for i in order]
    metrics = [metrics[i] for i in order]
    pnl_filtered_np = to_cpu(pnl_filtered)[:, order, :]

    cb(1.0, f"Scan terminé — {len(filtered_combos)} combos retenues")
    bar.empty()
    status.empty()

    return {
        "combinations": filtered_combos,
        "metrics": metrics,
        "n_tested": len(all_combinations),
        "n_found": len(filtered_combos),
        "gpu_time_s": time.perf_counter() - t_start,
        "pnl_per_combo": [pnl_filtered_np[:, i, :] for i in range(len(filtered_combos))],
        "spot_ranges": [to_cpu(spot_range)] * len(filtered_combos),
        "spots": [spot] * len(filtered_combos),
        "symbol": symbol,
        "symbols": [symbol] * len(filtered_combos),
        "as_of": as_of,
        "provider": provider,
        "days_before_close": params.get("days_before_close", 3),
    }


def _plot_replay(points, combo, as_of: date) -> go.Figure:
    """Graphe Plotly du P&L jour par jour avec couleur par mode."""
    dates = [p.date for p in points]
    pnl_pct = [p.pnl_pct for p in points]
    pnl_dollar = [p.pnl_dollar for p in points]
    spots = [p.spot for p in points]
    modes = [p.mode for p in points]

    color_map = {
        "market": "#00CC96",
        "expired": "#636EFA",
        "theoretical": "#FFA15A",
        "mixed": "#FFA15A",
        "no_data": "#888888",
    }
    colors = [color_map.get(m, "#888") for m in modes]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates, y=pnl_pct, mode="lines",
        line=dict(color="#636EFA", width=2),
        name="P&L %",
        customdata=np.stack([pnl_dollar, spots, modes], axis=1),
        hovertemplate="%{x|%d %b %Y}<br>P&L: %{y:+.2f}% ($%{customdata[0]:+,.2f})<br>"
                      "Spot: $%{customdata[1]:.2f}<br>Mode: %{customdata[2]}<extra></extra>",
        yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=pnl_pct, mode="markers",
        marker=dict(color=colors, size=7),
        showlegend=False, hoverinfo="skip", yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=spots, mode="lines",
        line=dict(color="rgba(150,150,150,0.5)", width=1, dash="dot"),
        name="Spot ($)", yaxis="y2",
    ))
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))

    # Marqueurs verticaux pour chaque expiration de leg
    # Note: add_vline + annotation_text déclenche _mean(X) dans Plotly qui plante
    # sur un axe date — on sépare le trait et l'annotation.
    for leg in combo.legs:
        if as_of <= leg.expiration <= dates[-1]:
            label = f"{'L' if leg.direction == 1 else 'S'} {leg.option_type[0].upper()} K{leg.strike:g}"
            x_iso = leg.expiration.isoformat()
            fig.add_vline(x=x_iso, line=dict(color="orange", dash="dot", width=1))
            fig.add_annotation(
                x=x_iso, y=1.02, yref="paper",
                text=f"exp {label}",
                showarrow=False,
                font=dict(size=13, color="orange"),
                textangle=-90,
                xanchor="center",
            )

    fig.update_layout(
        title=f"Backtest replay — entrée {as_of.strftime('%d %b %Y')}",
        template="plotly_dark",
        xaxis=dict(title="Date"),
        yaxis=dict(title="P&L (% net debit)", ticksuffix="%"),
        yaxis2=dict(title="Spot ($)", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
        height=600,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def render_backtest_page(params: dict) -> None:
    """Page principale du backtest."""
    if resolve_polygon_key() is None:
        st.error(
            "Aucune clé Polygon trouvée. Place ta clé dans **polygon.key** à la racine du projet, "
            "ou dans la variable d'env `POLYGON_API_KEY`."
        )
        return

    as_of: date | None = params.get("as_of")
    if as_of is None:
        st.error("Sélectionne une date d'entrée (as_of) dans la sidebar.")
        return

    if "bt_results" not in st.session_state:
        st.session_state.bt_results = None
    if "bt_replay" not in st.session_state:
        st.session_state.bt_replay = None
    if "bt_selected_idx" not in st.session_state:
        st.session_state.bt_selected_idx = 0

    if params["scan_clicked"]:
        if not params["symbols"]:
            st.error("Entrez au moins un ticker.")
            return
        if not params["selected_templates"]:
            st.error("Sélectionnez au moins un template.")
            return
        if len(params["symbols"]) > 1:
            st.warning(
                "En mode backtest, seul le 1er ticker est utilisé pour limiter les calls API. "
                f"Ticker scanné : **{params['symbols'][0]}**"
            )
        try:
            result = run_backtest_scan(params, params["symbols"][0], as_of)
            st.session_state.bt_results = result
            st.session_state.bt_replay = None
            st.session_state.bt_selected_idx = 0
        except Exception as exc:
            st.error(f"Erreur scan : {exc}")
            st.session_state.bt_results = None
            return

    results = st.session_state.bt_results
    scan_time = params.get("scan_time")
    time_label = f" @ {scan_time} ET" if scan_time else " (close EOD)"
    if results is None:
        st.info(
            f"Mode **Backtest**. Date : `{as_of}`{time_label}. "
            "Configure les paramètres dans la sidebar puis clique sur **Lancer le scan**.\n\n"
            "Plan Massive payant — appels illimités. "
            "Premier scan : quelques secondes à quelques minutes selon le ticker. "
            "Scans suivants sur la même date+heure : instantané (cache SQLite)."
        )
        return

    if "error" in results:
        st.error(results["error"])
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Combinaisons testées", f"{results['n_tested']:,}")
    col2.metric("Résultats trouvés", f"{results['n_found']:,}")
    col3.metric("Spot @ as_of", f"${results['spots'][0]:.2f}" if results['spots'] else "—")
    col4.metric("Temps total", f"{results['gpu_time_s']:.0f} s")

    if results["n_found"] == 0:
        st.warning("Aucune combinaison ne satisfait les critères. Essayez d'assouplir les filtres.")
        return

    st.markdown("---")

    idx = st.session_state.bt_selected_idx
    combo = results["combinations"][idx]
    m = results["metrics"][idx]
    pnl_for_combo = results["pnl_per_combo"][idx]

    fig = plot_pnl_profile(
        combination=combo, pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx], current_spot=results["spots"][idx],
        loss_prob=m["loss_prob_pct"] / 100,
        max_loss_pct=m["max_loss_pct"], max_gain_pct=m["max_gain_pct"],
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    selected = render_results_table(results["combinations"], results["metrics"], results.get("symbols"))
    if selected is not None and selected != idx:
        st.session_state.bt_selected_idx = selected
        st.session_state.bt_replay = None
        st.rerun()

    st.markdown("---")
    render_combo_detail(
        combo, m, symbol=results.get("symbol"),
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        as_of=as_of,
        days_before_close=results.get("days_before_close", 3),
    )

    # ── Replay 30 jours ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Replay historique 30 jours")

    days_forward = st.slider("Jours à replayer", 5, 60, 30, 5, key="bt_days_forward")

    if st.button("Lancer le replay sur cette combo", type="primary"):
        bar = st.progress(0.0, text="Replay…")
        status = st.empty()
        cb = _make_progress_callback(bar, status)
        try:
            points = backtest_combo(
                combo, as_of=as_of, days_forward=days_forward,
                provider=results["provider"], rate=params["risk_free_rate"],
                progress_callback=cb,
            )
            st.session_state.bt_replay = points
        except Exception as exc:
            st.error(f"Erreur replay : {exc}")
        finally:
            bar.empty()
            status.empty()

    points = st.session_state.bt_replay
    if points:
        replay_fig = _plot_replay(points, combo, as_of)
        st.plotly_chart(replay_fig, use_container_width=True)

        # Tableau résumé
        final = points[-1]
        peak = max(points, key=lambda p: p.pnl_dollar)
        trough = min(points, key=lambda p: p.pnl_dollar)
        col1, col2, col3 = st.columns(3)
        col1.metric("P&L final", f"${final.pnl_dollar:+,.2f}", f"{final.pnl_pct:+.2f}%")
        col2.metric("Peak P&L", f"${peak.pnl_dollar:+,.2f}",
                    f"{peak.pnl_pct:+.2f}% @ {peak.date.strftime('%d/%m')}")
        col3.metric("Worst P&L", f"${trough.pnl_dollar:+,.2f}",
                    f"{trough.pnl_pct:+.2f}% @ {trough.date.strftime('%d/%m')}")

        with st.expander("Détail jour par jour"):
            import pandas as pd
            df = pd.DataFrame([{
                "Date": p.date.isoformat(),
                "Spot": f"${p.spot:.2f}",
                "P&L $": f"{p.pnl_dollar:+,.2f}",
                "P&L %": f"{p.pnl_pct:+.2f}%",
                "Mode": p.mode,
            } for p in points])
            st.dataframe(df, use_container_width=True, hide_index=True)
