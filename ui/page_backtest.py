"""Page Streamlit : scan historique + replay 30 j via Polygon."""

from __future__ import annotations

import statistics
import time
from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import plotly.graph_objects as go
import streamlit as st

import config
from backtesting import backtest_combo, backtest_combo_hourly, RESOLUTIONS
from backtesting.replay import compute_iv_at_replay_point
from data.models import Combination
from data.provider_polygon import PolygonHistoricalProvider, resolve_polygon_key
from engine.backend import to_cpu, xp
from engine.combinator import generate_combinations
from engine.pnl import combinations_to_tensor, compute_pnl_batch
from scoring.filters import filter_combinations, realistic_max_gain
from scoring.metrics import compute_combo_metrics
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
            "spot": spot, "spots": [spot], "spot_range": to_cpu(spot_range),
        }

    filtered_combos = [all_combinations[i] for i in valid_indices_cpu]
    pnl_filtered = pnl_tensor[:, valid_indices, :]
    pnl_mid_filtered = pnl_filtered[config.VOL_MEDIAN_INDEX]
    net_debits_f = net_debits[valid_indices]

    event_factors = xp.ones(len(filtered_combos), dtype=xp.float32)
    weights = params.get("score_weights") or config.SCORE_WEIGHTS_DEFAULT

    metrics_batch = compute_combo_metrics(
        filtered_combos, pnl_filtered, spot_range, net_debits_f,
        current_spot=spot, today=as_of, risk_free_rate=rfr,
        atm_vol_global=atm_vol, days_to_close_global=days_to_close,
    )

    scores = score_combinations(
        metrics_batch, weights, event_score_factors=event_factors,
    )
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
    capital_required_cpu = to_cpu(metrics_batch.capital_required)

    metrics = []
    for i in range(len(filtered_combos)):
        max_loss_d = float(max_loss_dollar_cpu[i])
        max_gain_d = float(pnl_mid_cpu[i].max())
        max_gain_real_d = float(max_gain_real_dollar_cpu[i])
        nd_raw = float(safe_debits[i])
        cap_req = float(capital_required_cpu[i])

        metrics.append({
            "max_loss_pct":         float(max_loss_pct_cpu[i]),
            "loss_prob_pct":        float(loss_prob_cpu[i]) * 100,
            "max_gain_pct":         max_gain_d / cap_req * 100,
            "max_gain_real_pct":    float(max_gain_real_pct_cpu[i]),
            "annualized_return_pct": float(annualized_pct_cpu[i]),
            "liquidity_score":      float(liquidity_cpu[i]),
            "vol_dispersion_pct":   float(vol_disp_cpu[i]),
            "slippage_pct":         float(slippage_cpu[i]),
            "gain_loss_ratio":      max_gain_real_d / abs(max_loss_d) if max_loss_d != 0 else 0,
            "score":                float(scores_cpu[i]),
            "realistic_range_pct":  float(realistic_range_cpu[i]),
            "max_gain_real_dollar": max_gain_real_d,
            "capital_required":     cap_req,
            "days_to_close":        int(days_close_cpu[i]),
            "daily_gain_dollar":    float(daily_gain_cpu[i]),
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
        "realistic_range_pct": None,  # désormais per-combo dans metrics[i]
    }


def _replay_y_config(points, combo):
    """
    Détermine si on affiche en % net debit ou en $ (quand net_debit ≈ 0).
    Retourne (y_vals, y_label, y_tick_fmt, y_tick_suffix, hover_y, hover_secondary).
    Toutes les valeurs hover sont pré-formatées en strings pour éviter les bugs
    de format specifier de Plotly en mode unified hover.
    """
    net_debit = combo.net_debit
    use_dollar = abs(net_debit) < 1.0  # coût quasi-nul → % sans sens

    if use_dollar:
        y_vals = [p.pnl_dollar for p in points]
        hover_y    = [f"${p.pnl_dollar:+,.2f}" for p in points]
        hover_sec  = ["N/A" for _ in points]
        y_label    = "P&L ($)"
        y_tick_fmt = ",.2f"
        y_tick_sfx = ""
    else:
        y_vals = [p.pnl_pct for p in points]
        hover_y    = [f"{p.pnl_pct:+.2f}%" for p in points]
        hover_sec  = [f"${p.pnl_dollar:+,.2f}" for p in points]
        y_label    = "P&L (% net debit)"
        y_tick_fmt = ".2f"
        y_tick_sfx = "%"

    spots_fmt = [f"${p.spot:.2f}" for p in points]
    return y_vals, y_label, y_tick_fmt, y_tick_sfx, hover_y, hover_sec, spots_fmt


def _add_expiry_vlines(fig, combo, as_of, last_x, is_hourly=False):
    """Ajoute les barres verticales d'expiration de legs."""
    from datetime import datetime as _dt
    for leg in combo.legs:
        if is_hourly:
            if as_of > leg.expiration:
                continue
            x_val = _dt(leg.expiration.year, leg.expiration.month, leg.expiration.day, 16, 0)
        else:
            if not (as_of <= leg.expiration <= last_x):
                continue
            x_val = leg.expiration.isoformat()
        label = f"{'L' if leg.direction == 1 else 'S'} {leg.option_type[0].upper()} K{leg.strike:g}"
        fig.add_vline(x=x_val, line=dict(color="orange", dash="dot", width=1))
        fig.add_annotation(
            x=x_val, y=1.02, yref="paper",
            text=f"exp {label}",
            showarrow=False,
            font=dict(size=12, color="orange"),
            textangle=-90,
            xanchor="center",
        )


def _plot_replay(points, combo, as_of: date) -> go.Figure:
    """Graphe Plotly du P&L jour par jour."""
    color_map = {"market": "#00CC96", "expired": "#636EFA",
                 "theoretical": "#FFA15A", "mixed": "#FFA15A"}
    dates  = [p.date for p in points]
    spots  = [p.spot  for p in points]
    modes  = [p.mode  for p in points]
    colors = [color_map.get(m, "#888") for m in modes]

    y_vals, y_label, y_tick_fmt, y_tick_sfx, hover_y, hover_sec, spots_fmt = \
        _replay_y_config(points, combo)

    # customdata = liste de strings pré-formatées (pas de format specifier dans hovertemplate)
    customdata = list(zip(hover_y, hover_sec, spots_fmt, modes))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=y_vals, mode="lines",
        line=dict(color="#636EFA", width=2),
        name=y_label,
        customdata=customdata,
        hovertemplate="%{x|%d %b %Y}<br>P&L: %{customdata[0]} (%{customdata[1]})<br>"
                      "Spot: %{customdata[2]}<br>Mode: %{customdata[3]}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=y_vals, mode="markers",
        marker=dict(color=colors, size=7),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=spots, mode="lines",
        line=dict(color="#FFD700", width=2),
        name="Spot ($)", yaxis="y2",
    ))
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))
    _add_expiry_vlines(fig, combo, as_of, dates[-1] if dates else as_of)

    fig.update_layout(
        title=f"Backtest replay (journalier) — entrée {as_of.strftime('%d %b %Y')}",
        template="plotly_dark",
        xaxis=dict(title="Date", rangebreaks=[dict(bounds=["sat", "mon"])]),
        yaxis=dict(title=y_label, ticksuffix=y_tick_sfx, tickformat=y_tick_fmt),
        yaxis2=dict(title="Spot ($)", overlaying="y", side="right",
                    showgrid=False, tickformat=",.2f"),
        hovermode="x unified",
        height=600,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _plot_replay_hourly(points, combo, as_of, resolution: str = "1h") -> go.Figure:
    """Graphe Plotly du P&L heure par heure avec rangeslider horizontal."""
    from datetime import datetime as _dt
    color_map = {"market": "#00CC96", "expired": "#636EFA",
                 "theoretical": "#FFA15A", "mixed": "#FFA15A"}
    dts    = [p.date for p in points]
    spots  = [p.spot  for p in points]
    modes  = [p.mode  for p in points]
    colors = [color_map.get(m, "#888") for m in modes]

    y_vals, y_label, y_tick_fmt, y_tick_sfx, hover_y, hover_sec, spots_fmt = \
        _replay_y_config(points, combo)

    customdata = list(zip(hover_y, hover_sec, spots_fmt, modes))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dts, y=y_vals, mode="lines",
        line=dict(color="#636EFA", width=1.5),
        name=y_label,
        customdata=customdata,
        hovertemplate="%{x|%d %b %Hh%M}<br>P&L: %{customdata[0]} (%{customdata[1]})<br>"
                      "Spot: %{customdata[2]}<br>Mode: %{customdata[3]}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=dts, y=y_vals, mode="markers",
        marker=dict(color=colors, size=4),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=dts, y=spots, mode="lines",
        line=dict(color="#FFD700", width=2),
        name="Spot ($)", yaxis="y2",
    ))
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))
    _add_expiry_vlines(fig, combo, as_of, None, is_hourly=True)

    n_days = len(set(d.date() for d in dts)) if dts else 0
    date_range = f"{dts[0].strftime('%d/%m')} → {dts[-1].strftime('%d/%m/%Y')}" if dts else "—"

    # Rangebreaks : weekends + heures hors NYSE (9h30-16h)
    rbreaks = [dict(bounds=["sat", "mon"])]
    if resolution == "1h":
        rbreaks.append(dict(bounds=[16, 9], pattern="hour"))
    else:
        # Pour les résolutions sub-horaires, on cache avant 9h30 et après 16h
        rbreaks.append(dict(bounds=[16, 9.5], pattern="hour"))

    fig.update_layout(
        title=f"Backtest replay ({resolution}) — entrée {as_of.strftime('%d %b %Y')} "
              f"| {len(dts)} barres / {n_days} jours ({date_range})",
        template="plotly_dark",
        xaxis=dict(
            title="Date / Heure (ET)",
            range=[dts[0], dts[-1]] if dts else None,
            rangeslider=dict(visible=True, thickness=0.04),
            rangebreaks=rbreaks,
        ),
        yaxis=dict(title=y_label, ticksuffix=y_tick_sfx, tickformat=y_tick_fmt),
        yaxis2=dict(title="Spot ($)", overlaying="y", side="right",
                    showgrid=False, tickformat=",.2f"),
        hovermode="x unified",
        height=620,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _render_dynamic_profile_at_cursor(
    points: list, combo, idx: int, params: dict, results: dict,
) -> None:
    """FEAT-028 — Profil P&L théorique recalculé à l'instant choisi via curseur,
    avec marker du P&L observé superposé.

    Pour le point sélectionné :
      - days_before_close = (close_date − today).days (au lieu de 3 figé)
      - IV par leg = bisection BS depuis point.leg_values (au lieu de IV entrée figée)
      - re-render plot_pnl_profile avec observed_point=(point.spot, point.pnl_pct)

    Si pricer + IV refetched cohérents, le marker tombe sur la courbe → l'écart
    statique courbe-vs-replay disparaît à l'instant choisi.
    """
    if not points:
        return

    st.markdown("---")
    st.subheader("Profil P&L théorique recalculé à l'instant choisi")
    st.caption(
        "FEAT-028 — la courbe est rebâtie pour `today = curseur` avec l'IV implicite "
        "de chaque leg recalculée depuis les prix marché du replay. L'étoile jaune = "
        "P&L observé : doit poser sur la courbe si le marché valorise le combo "
        "comme prévu par BS américain."
    )

    cursor_key = f"bt_replay_cursor_{idx}_{combo.close_date}"
    n = len(points)
    # Défaut = dernier point en mode "market" (alignement marché possible).
    # Si tout le replay est theoretical/expired → tombe sur le dernier point.
    default_idx = n - 1
    for i in range(n - 1, -1, -1):
        if points[i].mode == "market":
            default_idx = i
            break

    is_intraday = isinstance(points[0].date, datetime)
    default_dt = points[default_idx].date
    if is_intraday:
        target_dt = st.slider(
            "Instant du replay",
            min_value=points[0].date,
            max_value=points[-1].date,
            value=default_dt,
            step=timedelta(minutes=5),
            format="DD/MM/YYYY HH:mm",
            key=cursor_key,
            help="Default = dernier point en mode 'market'. Glisse pour choisir "
                 "n'importe quel instant entre l'entrée et la fin du replay.",
        )
        # Snap au point du replay le plus proche du target_dt
        pt = min(points, key=lambda p: abs((p.date - target_dt).total_seconds()))
    else:
        target_d = st.slider(
            "Instant du replay",
            min_value=points[0].date,
            max_value=points[-1].date,
            value=default_dt,
            step=timedelta(days=1),
            format="DD/MM/YYYY",
            key=cursor_key,
        )
        pt = min(points, key=lambda p: abs((p.date - target_d).days))
    pt_date = pt.date.date() if isinstance(pt.date, datetime) else pt.date
    pt_label = (pt.date.strftime("%d/%m/%Y %Hh%M ET")
                if isinstance(pt.date, datetime)
                else pt.date.strftime("%d/%m/%Y"))

    rate = params["risk_free_rate"]
    iv_per_leg = compute_iv_at_replay_point(pt, combo.legs, rate)
    legs_dyn = [
        replace(l, implied_vol=iv_per_leg.get(l.contract_symbol, l.implied_vol))
        for l in combo.legs
    ]
    combo_dyn = Combination(
        legs=legs_dyn,
        net_debit=combo.net_debit,
        close_date=combo.close_date,
        template_name=combo.template_name,
        event_score_factor=combo.event_score_factor,
        events_in_sweet_zone=combo.events_in_sweet_zone,
        event_warning=combo.event_warning,
    )

    days_bc = max(0, (combo.close_date - pt_date).days)
    spot_range = xp.linspace(
        pt.spot * config.SPOT_RANGE_LOW,
        pt.spot * config.SPOT_RANGE_HIGH,
        config.NUM_SPOT_POINTS,
        dtype=xp.float32,
    )
    vol_scenarios = [params["vol_low"], 1.0, params["vol_high"]]
    # FEAT-028 : si point intraday, passer today_dt → TTE en secondes au lieu de jours
    today_dt = pt.date if isinstance(pt.date, datetime) else None
    tensor = combinations_to_tensor(
        [combo_dyn], days_before_close=days_bc, today_dt=today_dt,
    )
    # FEAT-028 : on force BS-européen pour la courbe (cohérence avec la bisection
    # BS-européen utilisée par compute_iv_at_replay_point). Sinon BJS-américain
    # sur IV bisectée-européenne → prix > prix marché pour les puts long-dated
    # (early exercise premium), et le marker ne pose plus sur la courbe.
    pnl_tensor = compute_pnl_batch(
        tensor, spot_range, vol_scenarios, rate,
        use_american_pricer=False,
    )
    pnl_2d = to_cpu(pnl_tensor)[:, 0, :]            # (V, M)
    spot_range_cpu = to_cpu(spot_range)
    pnl_mid = pnl_2d[config.VOL_MEDIAN_INDEX]
    nd_abs = abs(combo.net_debit) if abs(combo.net_debit) > 1.0 else 1.0
    max_loss_pct = float(pnl_mid.min()) / nd_abs * 100
    max_gain_pct = float(pnl_mid.max()) / nd_abs * 100

    # P&L théorique au spot du marker (pour mesurer l'écart d'alignement)
    nearest_idx = int(np.argmin(np.abs(spot_range_cpu - pt.spot)))
    pnl_theo_at_spot_pct = float(pnl_mid[nearest_idx]) / nd_abs * 100
    align_gap = pnl_theo_at_spot_pct - pt.pnl_pct

    symbol = results.get("symbol")
    fig = plot_pnl_profile(
        combination=combo_dyn,
        pnl_tensor=pnl_2d,
        spot_range=spot_range_cpu,
        current_spot=pt.spot,
        loss_prob=0.0,           # non recalculé pour le profil dynamique
        max_loss_pct=max_loss_pct,
        max_gain_pct=max_gain_pct,
        symbol=symbol,
        observed_point=(pt.spot, pt.pnl_pct),
    )

    col_info, col_meta = st.columns([3, 2])
    mode_warn = ""
    if pt.mode != "market":
        mode_warn = (
            f"  \n⚠ Mode `{pt.mode}` à cet instant : au moins un leg n'a pas de prix "
            f"marché Polygon, le replay utilise BS avec IV figée → IV non recalculées, "
            f"alignement marker-courbe trivial. Choisis un instant en mode `market` "
            f"pour tester FEAT-028."
        )
    col_info.markdown(
        f"**Instant** : {pt_label}  \n"
        f"**Spot** : ${pt.spot:.2f}  \n"
        f"**P&L observé** : {pt.pnl_pct:+.2f}% (${pt.pnl_dollar:+,.0f})  \n"
        f"**P&L théorique au spot du marker** : {pnl_theo_at_spot_pct:+.2f}%  \n"
        f"**Écart marker-courbe** : {align_gap:+.2f} pts "
        f"({'OK' if abs(align_gap) < 0.5 else 'décalage'})  \n"
        f"**Mode** : {pt.mode}{mode_warn}"
    )
    iv_lines = []
    for leg in combo.legs:
        sign = "L" if leg.direction > 0 else "S"
        iv_old = leg.implied_vol * 100
        iv_new = iv_per_leg.get(leg.contract_symbol, leg.implied_vol) * 100
        iv_lines.append(
            f"{sign}{leg.quantity} {leg.option_type} {leg.strike:g} "
            f"{leg.expiration.strftime('%d%b').upper()} : "
            f"IV {iv_old:.1f}% → **{iv_new:.1f}%**"
        )
    col_meta.markdown("**IV par leg (entrée → instant)**  \n" + "  \n".join(iv_lines))

    st.plotly_chart(fig, use_container_width=True)


def _render_replay_section(combo, idx: int, as_of, results: dict, params: dict) -> None:
    """Replay historique — partagé entre vue unique et vue grille."""
    st.markdown("---")
    st.subheader("Replay historique")

    _REPLAY_OPTS = {"1 jour": "daily", "1 heure": "1h", "15 min": "15min", "5 min": "5min"}

    default_days = max(5, min(60, (combo.close_date - as_of).days))
    slider_key = f"bt_days_{idx}_{combo.close_date}"
    days_forward = st.slider("Jours à replayer", 5, 60, default_days, 1, key=slider_key)

    res_label = st.selectbox(
        "Résolution",
        options=list(_REPLAY_OPTS.keys()),
        index=3,  # "5 min" par défaut
        key=f"bt_resolution_{idx}_{combo.close_date}",
    )
    res_key = _REPLAY_OPTS[res_label]

    launch = st.button(f"Lancer le replay ({res_label})", type="primary",
                       use_container_width=True)

    if launch:
        bar = st.progress(0.0, text=f"Replay {res_label}…")
        status = st.empty()
        cb = _make_progress_callback(bar, status)
        try:
            if res_key == "daily":
                points = backtest_combo(
                    combo, as_of=as_of, days_forward=days_forward,
                    provider=results["provider"], rate=params["risk_free_rate"],
                    progress_callback=cb,
                )
            else:
                points = backtest_combo_hourly(
                    combo, as_of=as_of, days_forward=days_forward,
                    provider=results["provider"], rate=params["risk_free_rate"],
                    progress_callback=cb, resolution=res_key,
                )
            st.session_state.bt_replay = (res_key, points)
        except Exception as exc:
            st.error(f"Erreur replay : {exc}")
        finally:
            bar.empty()
            status.empty()

    replay_state = st.session_state.bt_replay
    if replay_state:
        replay_mode, points = replay_state
        if replay_mode in RESOLUTIONS:
            replay_fig = _plot_replay_hourly(points, combo, as_of, resolution=replay_mode)
        else:
            replay_fig = _plot_replay(points, combo, as_of)
        st.plotly_chart(replay_fig, use_container_width=True)

        final = points[-1]
        peak = max(points, key=lambda p: p.pnl_dollar)
        trough = min(points, key=lambda p: p.pnl_dollar)
        col1, col2, col3 = st.columns(3)
        col1.metric("P&L final", f"${final.pnl_dollar:+,.2f}", f"{final.pnl_pct:+.2f}%")
        col2.metric("Peak P&L", f"${peak.pnl_dollar:+,.2f}",
                    f"{peak.pnl_pct:+.2f}% @ {peak.date.strftime('%d/%m')}")
        col3.metric("Worst P&L", f"${trough.pnl_dollar:+,.2f}",
                    f"{trough.pnl_pct:+.2f}% @ {trough.date.strftime('%d/%m')}")

        from collections import Counter
        mode_counts = Counter(p.mode for p in points)
        total_pts = len(points)
        n_mkt = mode_counts.get("market", 0)
        n_theo = mode_counts.get("theoretical", 0) + mode_counts.get("mixed", 0)
        n_exp = mode_counts.get("expired", 0)
        st.caption(
            f"Fiabilité replay — "
            f"Market (prix réels) : **{n_mkt}/{total_pts} ({100*n_mkt//max(total_pts,1)}%)** | "
            f"Theoretical (BS IV figée) : **{n_theo}/{total_pts} ({100*n_theo//max(total_pts,1)}%)** | "
            f"Expiré : {n_exp}"
        )

        # ── FEAT-028 : Profil P&L recalculé à l'instant choisi ──────────────
        _render_dynamic_profile_at_cursor(points, combo, idx, params, results)

        is_intraday = replay_mode in RESOLUTIONS
        label_col = f"Date/Heure ({replay_mode})" if is_intraday else "Date"
        with st.expander(f"Détail {'barre par barre' if is_intraday else 'jour par jour'}"):
            import pandas as pd
            fmt = "%d/%m %Hh%M" if is_intraday else "%Y-%m-%d"
            df = pd.DataFrame([{
                label_col: p.date.strftime(fmt),
                "Spot": f"${p.spot:.2f}",
                "P&L $": f"{p.pnl_dollar:+,.2f}",
                "P&L %": f"{p.pnl_pct:+.2f}%",
                "Mode": p.mode,
            } for p in points])
            st.dataframe(df, use_container_width=True, hide_index=True)


def render_backtest_page(base_params: dict) -> None:
    """Page principale du backtest."""
    from datetime import date as _date, timedelta as _td

    if resolve_polygon_key() is None:
        st.error(
            "Aucune clé Polygon trouvée. Place ta clé dans **polygon.key** à la racine du projet, "
            "ou dans la variable d'env `POLYGON_API_KEY`."
        )
        return

    # ── Inputs propres à la page Backtest ──────────────────────────────────
    col_date, col_time, col_sym = st.columns([2, 2, 3])
    with col_date:
        max_as_of = _date.today() - _td(days=1)
        min_as_of = _date.today() - _td(days=2 * 365)
        default_as_of = max(min_as_of, min(max_as_of, _date(2026, 2, 5)))
        as_of = st.date_input(
            "Date d'entrée (as_of)",
            value=default_as_of,
            min_value=min_as_of,
            max_value=max_as_of,
            key="bt_as_of",
            help="Massive (ex-Polygon) : 2 ans d'historique max.",
        )
    with col_time:
        from data.provider_polygon import SCAN_TIME_OPTIONS
        scan_time_label = st.selectbox(
            "Heure du scan (ET)",
            options=list(SCAN_TIME_OPTIONS.keys()),
            index=1,
            key="bt_scan_time_label",
            help="Heure de la prise de prix en temps de marché (America/New_York).",
        )
        scan_time = SCAN_TIME_OPTIONS[scan_time_label]
    with col_sym:
        if "bt_symbols_input" not in st.session_state:
            st.session_state["bt_symbols_input"] = "SPY"
        raw = st.text_input(
            "Sous-jacent(s)",
            key="bt_symbols_input",
            help="En backtest, seul le 1er ticker est utilisé.",
            placeholder="SPY",
        )
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

    params = {
        **base_params,
        "symbols": symbols,
        "as_of": as_of,
        "scan_time": scan_time,
        "mode": "backtest",
    }

    if "bt_results" not in st.session_state:
        st.session_state.bt_results = None
    if "bt_replay" not in st.session_state:
        st.session_state.bt_replay = None
    if "bt_selected_idx" not in st.session_state:
        st.session_state.bt_selected_idx = 0

    time_label = f" @ {scan_time} ET" if scan_time else " (close EOD)"

    # ── Saisie directe d'un combo (FEAT-021) ───────────────────────────────
    from ui.combo_parser import parse_combo_string, resolve_combo_backtest, build_single_combo_results
    with st.expander("Saisir un combo directement (sans scan)", expanded=False):
        st.caption(
            "Format : `L1 call SPY 17JUL2026 715 | L2 put SPY 17JUL2026 690 | "
            "S1 call SPY 15MAY2026 745 | S2 put SPY 15MAY2026 672`  "
            "(copier depuis la page Tracker)"
        )
        combo_text = st.text_area("Combo", height=68, key="bt_combo_input",
                                  placeholder="L1 call SPY 17JUL2026 715 | S1 put SPY 15MAY2026 672")
        if st.button("Analyser ce combo", key="bt_analyze_combo"):
            leg_specs = parse_combo_string(combo_text)
            if not leg_specs:
                st.error("Format invalide.")
            else:
                symbol = leg_specs[0]["symbol"]
                with st.spinner(f"Chargement prix Polygon ({symbol} @ {as_of}{time_label})…"):
                    resolved = resolve_combo_backtest(leg_specs, symbol, as_of, scan_time)
                if resolved:
                    combination, spot, provider, missing, details = resolved
                    result = build_single_combo_results(
                        combination, spot, symbol, params, as_of=as_of, provider=provider
                    )
                    st.session_state.bt_results = result
                    st.session_state.bt_replay = None
                    st.session_state.bt_selected_idx = 0
                    warnings = result["metrics"][0].get("_warnings", [])
                    if missing:
                        warnings.insert(0,
                            f"⚠ {len(missing)} leg(s) non trouvé(s) dans la chaîne "
                            f"Polygon @ {as_of} (prix=0, P&L faussé) : {', '.join(missing)}"
                        )
                    st.session_state["_combo_warnings"] = warnings
                    st.session_state["_combo_leg_details"] = details
                    st.session_state["_combo_net_debit"] = combination.net_debit
                    st.rerun()

    # ── Bouton Lancer le scan (FEAT-020) ────────────────────────────────────
    scan_clicked = st.button("🔍 Lancer le scan", type="primary", key="bt_scan_btn")
    if scan_clicked:
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
            st.session_state["grid_page_bt"] = 0
        except Exception as exc:
            st.error(f"Erreur scan : {exc}")
            st.session_state.bt_results = None
            return

    results = st.session_state.bt_results

    warnings = st.session_state.pop("_combo_warnings", [])
    details  = st.session_state.pop("_combo_leg_details", [])
    nd_info  = st.session_state.pop("_combo_net_debit", None)
    for w in warnings:
        st.warning(w) if "non trouvé" in w else st.info(w)
    if details:
        import pandas as pd
        nd_txt = f" | Net debit : **{nd_info:+.2f}$**" if nd_info is not None else ""
        st.caption(f"Prix utilisés par la saisie directe (Polygon @ as_of){nd_txt} :")
        st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)

    if results is None:
        st.info(
            f"Mode **Backtest**. Date : `{as_of}`{time_label}. "
            "Saisir un combo directement ou lancer un scan.\n\n"
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

    from ui.page_live import _render_grid, _render_grid_details_compact

    if "view_mode_bt" not in st.session_state:
        st.session_state["view_mode_bt"] = "Grille"
    view_mode = st.radio(
        "Affichage", options=["Grille", "Vue unique"],
        horizontal=True, label_visibility="collapsed",
        key="view_mode_bt",
    )

    st.markdown("---")

    dbc_bt = results.get("days_before_close", params.get("days_before_close", 3))
    if view_mode == "Grille":
        _render_grid(results, "bt", params)
        st.markdown("---")
        sel_tbl = render_results_table(
            results["combinations"], results["metrics"],
            results.get("symbols"),
            spot=results["spots"][0] if results.get("spots") else None,
            selected_row=st.session_state.get("bt_selected_idx", 0),
        )
        if sel_tbl is not None and sel_tbl != st.session_state.get("bt_selected_idx", 0):
            st.session_state["bt_selected_idx"] = sel_tbl
            st.rerun()
        st.markdown("---")
        _render_grid_details_compact(results, "bt_selected_idx",
                                     days_before_close=dbc_bt, as_of=as_of)
        grid_idx = min(st.session_state.get("bt_selected_idx", 0), results["n_found"] - 1)
        _render_replay_section(results["combinations"][grid_idx], grid_idx,
                               as_of, results, params)
        return

    # ── Vue unique ─────────────────────────────────────────────────────────
    idx = st.session_state.bt_selected_idx
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
        combination=combo, pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx], current_spot=results["spots"][idx],
        loss_prob=m["loss_prob_pct"] / 100,
        max_loss_pct=m["max_loss_pct"], max_gain_pct=m["max_gain_pct"],
        symbol=combo_symbol,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    selected = render_results_table(results["combinations"], results["metrics"],
                                    results.get("symbols"),
                                    realistic_range_pct=results.get("realistic_range_pct"))
    if selected is not None and selected != idx:
        st.session_state.bt_selected_idx = selected
        st.session_state.bt_replay = None
        st.rerun()

    st.markdown("---")
    render_combo_detail(
        combo, m, symbol=combo_symbol,
        pnl_tensor=pnl_for_combo,
        spot_range=results["spot_ranges"][idx],
        current_spot=results["spots"][idx],
        as_of=as_of,
        days_before_close=results.get("days_before_close", 3),
    )

    _render_replay_section(combo, idx, as_of, results, params)
