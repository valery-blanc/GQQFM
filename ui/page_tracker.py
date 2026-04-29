"""Page Streamlit : gestion des combos trackés + comparaison replay vs réel."""

from __future__ import annotations

import plotly.graph_objects as go
import requests
import streamlit as st

TRACKER_API = "http://192.168.0.222:8502"


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


def _plot_comparison(pnl_data: list[dict], combo: dict, mode: str = "pct") -> go.Figure:
    """Superpose P&L mid et spot. mode='pct' ou 'dollar'."""
    if not pnl_data:
        return go.Figure()

    ts    = [d["timestamp"] for d in pnl_data]
    spots = [d["spot"]      for d in pnl_data]
    net_debit = combo.get("net_debit", 0)
    zero_cost = abs(net_debit) < 0.01  # combo à coût nul → forcer mode dollar

    if mode == "pct" and not zero_cost:
        y_mid  = [d["pnl_pct"]      for d in pnl_data]
        y_label = "P&L (%)"
        hover_mid  = "%{x}<br>P&L mid: %{y:+.2f}%<extra></extra>"
        tick_suffix, tick_fmt = "%", ".2f"
    else:
        y_mid  = [d["pnl_dollar"]   for d in pnl_data]
        y_label = "P&L ($)"
        hover_mid  = "%{x}<br>P&L mid: $%{y:+,.2f}<extra></extra>"
        tick_suffix, tick_fmt = "$", ",.2f"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=y_mid, mode="lines+markers", name="P&L mid (collecté)",
        line=dict(color="#00CC96", width=2),
        hovertemplate=hover_mid,
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=spots, mode="lines", name="Spot ($)",
        line=dict(color="#FFD700", width=1.5),
        yaxis="y2",
        hovertemplate="%{x}<br>Spot: $%{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))

    fig.update_layout(
        title=f"Prix réels collectés — {combo['symbol']} (depuis {combo['tracked_since'][:10]})",
        template="plotly_dark",
        xaxis=dict(title="Horodatage (ET)"),
        yaxis=dict(title=y_label, ticksuffix=tick_suffix if mode == "pct" else "",
                   tickprefix="" if mode == "pct" else "$", tickformat=tick_fmt),
        yaxis2=dict(title="Spot ($)", overlaying="y", side="right",
                    showgrid=False, tickformat=",.2f"),
        hovermode="x unified",
        height=500,
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

        with st.expander(
            f"**{combo['symbol']}** — scanné le {combo.get('as_of', '?')} "
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

            c1, c2 = st.columns(2)

            if c1.button("Afficher les données réelles", key=f"show_{combo['id']}"):
                pnl_data = _api_get(f"/pnl/{combo['id']}", timeout=10)
                if pnl_data:
                    net_debit = combo.get("net_debit", 0)
                    zero_cost = abs(net_debit) < 0.01
                    if zero_cost:
                        mode = "dollar"
                        st.caption("Combo à coût nul — affichage en dollars.")
                    else:
                        mode = st.radio(
                            "Affichage P&L",
                            options=["pct", "dollar"],
                            format_func=lambda x: "% (relatif au débit)" if x == "pct" else "$ (absolu)",
                            horizontal=True,
                            key=f"pnl_mode_{combo['id']}",
                        )

                    fig = _plot_comparison(pnl_data, combo, mode=mode)
                    st.plotly_chart(fig, use_container_width=True)

                    final  = pnl_data[-1]
                    peak   = max(pnl_data, key=lambda d: d["pnl_dollar"])
                    trough = min(pnl_data, key=lambda d: d["pnl_dollar"])
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric("P&L final", f"${final['pnl_dollar']:+,.2f}",
                               f"{final['pnl_pct']:+.2f}%" if not zero_cost else None)
                    mc2.metric("Peak P&L", f"${peak['pnl_dollar']:+,.2f}",
                               f"{max(pnl_data, key=lambda d: d['pnl_dollar'])['pnl_pct']:+.2f}%" if not zero_cost else None)
                    mc3.metric("Worst P&L", f"${trough['pnl_dollar']:+,.2f}",
                               f"{min(pnl_data, key=lambda d: d['pnl_dollar'])['pnl_pct']:+.2f}%" if not zero_cost else None)
                else:
                    st.info("Pas encore de données collectées pour ce combo.")

            if c2.button("Supprimer du tracker", key=f"del_{combo['id']}",
                         type="secondary"):
                if _api_delete(f"/combos/{combo['id']}"):
                    st.success("Combo supprimé du tracker Avignon.")
                    st.rerun()
                else:
                    st.error("Impossible de supprimer — Avignon non joignable.")
