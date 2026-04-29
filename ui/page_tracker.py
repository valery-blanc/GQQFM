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


def _plot_comparison(pnl_data: list[dict], combo: dict) -> go.Figure:
    """Superpose P&L mid et P&L exécution réaliste (bid/ask)."""
    if not pnl_data:
        return go.Figure()

    ts     = [d["timestamp"] for d in pnl_data]
    pct    = [d["pnl_pct"]      for d in pnl_data]
    pct_ex = [d["pnl_exec_pct"] for d in pnl_data]
    spots  = [d["spot"]         for d in pnl_data]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ts, y=pct, mode="lines+markers", name="P&L mid (collecté)",
        line=dict(color="#00CC96", width=2),
        hovertemplate="%{x}<br>P&L mid: %{y:+.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=ts, y=pct_ex, mode="lines", name="P&L exécution (bid/ask)",
        line=dict(color="#FFA15A", width=2, dash="dot"),
        hovertemplate="%{x}<br>P&L exec: %{y:+.2f}%<extra></extra>",
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
        yaxis=dict(title="P&L (%)", ticksuffix="%", tickformat=".2f"),
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
                    fig = _plot_comparison(pnl_data, combo)
                    st.plotly_chart(fig, use_container_width=True)

                    final  = pnl_data[-1]
                    peak   = max(pnl_data, key=lambda d: d["pnl_dollar"])
                    trough = min(pnl_data, key=lambda d: d["pnl_dollar"])
                    mc1, mc2, mc3 = st.columns(3)
                    mc1.metric("P&L final (mid)",  f"${final['pnl_dollar']:+,.2f}",
                               f"{final['pnl_pct']:+.2f}%")
                    mc2.metric("Peak P&L", f"${peak['pnl_dollar']:+,.2f}",
                               f"{peak['pnl_pct']:+.2f}%")
                    mc3.metric("Worst P&L", f"${trough['pnl_dollar']:+,.2f}",
                               f"{trough['pnl_pct']:+.2f}%")
                    st.caption(
                        f"P&L exec (bid/ask réel) : final {final['pnl_exec_dollar']:+,.2f}$ "
                        f"({final['pnl_exec_pct']:+.2f}%) — "
                        f"écart vs mid = {final['pnl_exec_dollar']-final['pnl_dollar']:+.0f}$ "
                        f"(coût du spread bid/ask)"
                    )
                else:
                    st.info("Pas encore de données collectées pour ce combo.")

            if c2.button("Supprimer du tracker", key=f"del_{combo['id']}",
                         type="secondary"):
                if _api_delete(f"/combos/{combo['id']}"):
                    st.success("Combo supprimé du tracker Avignon.")
                    st.rerun()
                else:
                    st.error("Impossible de supprimer — Avignon non joignable.")
