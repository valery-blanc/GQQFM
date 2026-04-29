"""Page Streamlit : gestion des combos trackés + comparaison replay vs réel."""

from __future__ import annotations

from datetime import date, datetime

import plotly.graph_objects as go
import requests
import streamlit as st

TRACKER_API = "http://192.168.0.222:8502"

_DELAY_NOTE = (
    "**Sources :** prix options = Polygon `day.close` (dernier prix côté de la session, "
    "free tier — peut être stale sur options illiquides) · spot = yfinance (15min delay) · "
    "courbe théorique = prix historiques réels des options (barres Polygon), "
    "Black-Scholes en fallback uniquement si aucune cotation disponible sur ce créneau."
)


def _api_get(path: str, timeout: int = 5):
    try:
        resp = requests.get(f"{TRACKER_API}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _api_delete(path: str, timeout: int = 5) -> bool:
    try:
        resp = requests.delete(f"{TRACKER_API}{path}", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _combo_to_combination(combo: dict):
    """Reconstruit un objet Combination depuis le dict JSON du tracker."""
    from data.models import Combination, Leg

    legs = [
        Leg(
            option_type=l["option_type"],
            direction=l["direction"],
            quantity=l["quantity"],
            strike=l["strike"],
            expiration=date.fromisoformat(l["expiration"]),
            entry_price=l["entry_price"],
            implied_vol=l["implied_vol"],
            contract_symbol=l["contract_symbol"],
        )
        for l in combo["legs"]
    ]
    close_date = min(l.expiration for l in legs)
    return Combination(
        legs=legs,
        net_debit=combo.get("net_debit", 0),
        close_date=close_date,
        template_name="tracked",
    )


def _run_backtest_overlay(combo: dict, resolution: str = "1h") -> list | None:
    """Lance le backtest horaire depuis as_of jusqu'à aujourd'hui. Retourne None si < 1 jour."""
    from backtesting.replay import backtest_combo_hourly

    as_of = date.fromisoformat(combo["as_of"])
    days_forward = (date.today() - as_of).days
    if days_forward < 1:
        return None

    combination = _combo_to_combination(combo)
    try:
        return backtest_combo_hourly(combination, as_of, days_forward, resolution=resolution)
    except Exception as exc:
        st.warning(f"Backtest impossible : {exc}")
        return None


def _plot_comparison(
    pnl_data: list[dict],
    combo: dict,
    mode: str = "pct",
    bt_points: list | None = None,
) -> go.Figure:
    """
    Superpose :
    - P&L réel collecté (Polygon day.close, vert)
    - Spot sous-jacent (yfinance 15min delay, axe droit, jaune)
    - P&L théorique backtest (BS sur barres Polygon, bleu pointillé) si bt_points fourni
    """
    if not pnl_data:
        return go.Figure()

    ts    = [d["timestamp"] for d in pnl_data]
    spots = [d["spot"]      for d in pnl_data]
    net_debit = combo.get("net_debit", 0)
    zero_cost = abs(net_debit) < 0.01

    use_dollar = (mode == "dollar") or zero_cost

    if not use_dollar:
        y_real   = [d["pnl_pct"]    for d in pnl_data]
        y_label  = "P&L (%)"
        fmt_real = "%{x}<br>P&L réel: %{y:+.2f}%<extra></extra>"
        fmt_bt   = "%{x}<br>P&L théo: %{y:+.2f}%<extra></extra>"
        tick_fmt = ".2f"
    else:
        y_real   = [d["pnl_dollar"] for d in pnl_data]
        y_label  = "P&L ($)"
        fmt_real = "%{x}<br>P&L réel: $%{y:+,.2f}<extra></extra>"
        fmt_bt   = "%{x}<br>P&L théo: $%{y:+,.2f}<extra></extra>"
        tick_fmt = ",.2f"

    fig = go.Figure()

    # Courbe théorique backtest (derrière)
    if bt_points:
        bt_ts  = [str(p.date) for p in bt_points]
        bt_y   = [p.pnl_pct if not use_dollar else p.pnl_dollar for p in bt_points]
        bt_spot = [p.spot for p in bt_points]
        fig.add_trace(go.Scatter(
            x=bt_ts, y=bt_y, mode="lines", name="P&L historique Polygon (BS si pas de cotation)",
            line=dict(color="#636EFA", width=2, dash="dash"),
            hovertemplate=fmt_bt,
        ))
        # Spot backtest (axe droit, gris clair) si différent du spot réel
        fig.add_trace(go.Scatter(
            x=bt_ts, y=bt_spot, mode="lines", name="Spot backtest ($)",
            line=dict(color="#9EA3B0", width=1, dash="dot"),
            yaxis="y2",
            hovertemplate="%{x}<br>Spot BT: $%{y:.2f}<extra></extra>",
            visible="legendonly",  # caché par défaut, activable via légende
        ))

    # Courbe réelle (au premier plan)
    fig.add_trace(go.Scatter(
        x=ts, y=y_real, mode="lines+markers", name="P&L réel (Polygon)",
        line=dict(color="#00CC96", width=2),
        hovertemplate=fmt_real,
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=spots, mode="lines", name="Spot yfinance (15min delay)",
        line=dict(color="#FFD700", width=1.5),
        yaxis="y2",
        hovertemplate="%{x}<br>Spot: $%{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))

    since = combo["tracked_since"][:10]
    fig.update_layout(
        title=f"P&L réel vs théorique — {combo['symbol']} (tracké depuis {since})",
        template="plotly_dark",
        xaxis=dict(title="Horodatage (ET)"),
        yaxis=dict(title=y_label, tickformat=tick_fmt),
        yaxis2=dict(title="Spot ($)", overlaying="y", side="right",
                    showgrid=False, tickformat=",.2f"),
        hovermode="x unified",
        height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def render_tracker_page() -> None:
    """Page principale du tracker."""
    st.title("Tracker de combos — prix réels")

    # ── Statut API Avignon ──────────────────────────────────────────────────
    health = _api_get("/health")
    if health:
        st.success(
            f"Avignon tracker actif — {health['combos']} combo(s) trackés, "
            f"{health['total_price_rows']} mesures en base."
        )
    else:
        st.error(
            "Container Avignon non joignable (192.168.0.222:8502). "
            "Lance `docker-compose up -d` sur Avignon pour démarrer le tracker."
        )
        return

    st.caption(_DELAY_NOTE)
    st.markdown("---")

    # ── Liste des combos trackés (depuis l'API) ─────────────────────────────
    combos = _api_get("/combos") or []

    if not combos:
        st.info(
            "Aucun combo en cours de tracking. "
            "Clique sur **Tracker ce combo** dans les détails d'un combo après un scan."
        )
        return

    st.subheader(f"{len(combos)} combo(s) trackés")

    for combo in combos:
        n_snap = combo.get("n_snapshots", "?")
        since  = combo.get("tracked_since", "?")[:16].replace("T", " ")
        as_of  = combo.get("as_of", "?")

        with st.expander(
            f"**{combo['symbol']}** — scanné le {as_of} "
            f"| tracké depuis {since} | {n_snap} mesures",
            expanded=False,
        ):
            # Legs
            cols = st.columns([3, 1, 1, 1, 1, 1])
            cols[0].markdown("**Leg**")
            cols[1].markdown("**Direction**")
            cols[2].markdown("**Strike**")
            cols[3].markdown("**Expiration**")
            cols[4].markdown("**Prix entrée**")
            cols[5].markdown("**Qté**")
            for leg in combo["legs"]:
                d = "Long" if leg["direction"] > 0 else "Short"
                cols = st.columns([3, 1, 1, 1, 1, 1])
                cols[0].caption(leg["contract_symbol"])
                cols[1].caption(d)
                cols[2].caption(f"{leg['strike']:g}")
                cols[3].caption(leg["expiration"])
                cols[4].caption(f"${leg['entry_price']:.2f}")
                cols[5].caption(str(leg["quantity"]))

            net_debit = combo.get("net_debit", 0)
            zero_cost = abs(net_debit) < 0.01

            ca, cb_, cc = st.columns([2, 2, 1])

            show_real = ca.button("Afficher P&L réel", key=f"show_{combo['id']}")
            show_bt   = cb_.button(
                "Ajouter courbe théorique (backtest)",
                key=f"bt_{combo['id']}",
                help="Calcule le P&L théorique Black-Scholes sur les barres Polygon depuis l'entrée. Disponible dès le lendemain.",
            )

            if show_real or show_bt:
                pnl_data = _api_get(f"/pnl/{combo['id']}", timeout=10)

                # Courbe backtest (si demandée ou déjà en session state)
                bt_key = f"bt_points_{combo['id']}"
                if show_bt:
                    as_of_date = date.fromisoformat(combo["as_of"])
                    days = (date.today() - as_of_date).days
                    if days < 1:
                        st.info(
                            "La courbe théorique sera disponible demain — "
                            "le backtest nécessite au moins 1 jour de barres Polygon."
                        )
                        st.session_state[bt_key] = None
                    else:
                        with st.spinner("Calcul courbe théorique (Polygon historique)…"):
                            bt_points = _run_backtest_overlay(combo)
                            st.session_state[bt_key] = bt_points
                            if not bt_points:
                                st.warning("Pas de données Polygon pour cette période.")

                bt_points = st.session_state.get(bt_key)

                if pnl_data:
                    if zero_cost:
                        mode = "dollar"
                        st.caption("Combo à coût nul — affichage en dollars.")
                    else:
                        mode = st.radio(
                            "Affichage P&L",
                            options=["pct", "dollar"],
                            format_func=lambda x: "% (/ débit)" if x == "pct" else "$ (absolu)",
                            horizontal=True,
                            key=f"pnl_mode_{combo['id']}",
                        )

                    fig = _plot_comparison(pnl_data, combo, mode=mode, bt_points=bt_points)
                    st.plotly_chart(fig, use_container_width=True)

                    final  = pnl_data[-1]
                    peak   = max(pnl_data, key=lambda d: d["pnl_dollar"])
                    trough = min(pnl_data, key=lambda d: d["pnl_dollar"])
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric("P&L final (réel)",  f"${final['pnl_dollar']:+,.2f}",
                               f"{final['pnl_pct']:+.2f}%" if not zero_cost else None)
                    mc2.metric("Peak P&L", f"${peak['pnl_dollar']:+,.2f}",
                               f"{peak['pnl_pct']:+.2f}%" if not zero_cost else None)
                    mc3.metric("Worst P&L", f"${trough['pnl_dollar']:+,.2f}",
                               f"{trough['pnl_pct']:+.2f}%" if not zero_cost else None)

                    if bt_points:
                        bt_final = bt_points[-1]
                        delta = final["pnl_dollar"] - bt_final.pnl_dollar
                        st.caption(
                            f"Écart réel vs théorique (dernier point) : "
                            f"**{delta:+.2f}$** "
                            f"({'réel > théo' if delta > 0 else 'réel < théo'}) — "
                            f"reflète l'erreur de modèle BS + liquidité."
                        )
                else:
                    st.info("Pas encore de données collectées pour ce combo.")

            if cc.button("🗑 Supprimer", key=f"del_{combo['id']}", type="secondary"):
                if _api_delete(f"/combos/{combo['id']}"):
                    st.session_state.pop(f"bt_points_{combo['id']}", None)
                    st.success("Combo supprimé du tracker Avignon.")
                    st.rerun()
                else:
                    st.error("Impossible de supprimer — Avignon non joignable.")
