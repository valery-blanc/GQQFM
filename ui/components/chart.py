"""Graphique P&L interactif (Plotly)."""

import numpy as np
import plotly.graph_objects as go

from data.models import Combination


def plot_pnl_profile(
    combination: Combination,
    pnl_tensor: np.ndarray,    # shape (V, M) — V scénarios, M spots
    spot_range: np.ndarray,    # shape (M,)
    current_spot: float,
    loss_prob: float,
    max_loss_pct: float,
    max_gain_pct: float,
) -> go.Figure:
    """
    Génère le graphique P&L interactif Plotly.

    Éléments affichés :
    - Courbe principale (scénario vol médian, index 1)
    - Bande d'incertitude vol_low / vol_high
    - Zones de profit (vert) et de perte (rouge)
    - Ligne breakeven
    - Annotations : perte max, gain max, probabilité de perte, net debit
    - Double axe Y (% capital / valeur absolue)
    """
    net_debit = combination.net_debit

    pnl_low = pnl_tensor[0]   # vol basse
    pnl_mid = pnl_tensor[1]   # vol médiane (référence)
    pnl_high = pnl_tensor[2]  # vol haute

    pnl_pct_mid = pnl_mid / net_debit * 100
    pnl_pct_low = pnl_low / net_debit * 100
    pnl_pct_high = pnl_high / net_debit * 100

    pct_change = (spot_range / current_spot - 1) * 100

    fig = go.Figure()

    # Bande d'incertitude
    fig.add_trace(go.Scatter(
        x=spot_range.tolist() + spot_range[::-1].tolist(),
        y=pnl_pct_high.tolist() + pnl_pct_low[::-1].tolist(),
        fill="toself",
        fillcolor="rgba(99, 110, 250, 0.12)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Bande vol (incertitude)",
        hoverinfo="skip",
    ))

    # Zone de perte (rouge)
    loss_mask = pnl_pct_mid < 0
    if loss_mask.any():
        fig.add_trace(go.Scatter(
            x=spot_range.tolist() + spot_range[::-1].tolist(),
            y=np.where(pnl_pct_mid < 0, pnl_pct_mid, 0).tolist()
              + [0] * len(spot_range),
            fill="toself",
            fillcolor="rgba(239, 85, 59, 0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="Zone de perte",
            hoverinfo="skip",
        ))

    # Zone de profit (vert)
    profit_mask = pnl_pct_mid > 0
    if profit_mask.any():
        fig.add_trace(go.Scatter(
            x=spot_range.tolist() + spot_range[::-1].tolist(),
            y=np.where(pnl_pct_mid > 0, pnl_pct_mid, 0).tolist()
              + [0] * len(spot_range),
            fill="toself",
            fillcolor="rgba(0, 204, 150, 0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="Zone de profit",
            hoverinfo="skip",
        ))

    # Courbe principale
    fig.add_trace(go.Scatter(
        x=spot_range,
        y=pnl_pct_mid,
        mode="lines",
        name="P&L (vol médiane)",
        line=dict(color="#636EFA", width=2.5),
        customdata=np.stack([pnl_mid, pct_change], axis=1),
        hovertemplate=(
            "Spot: %{x:.2f} (%{customdata[1]:+.1f}%)<br>"
            "P&L: %{y:.1f}% ($%{customdata[0]:,.0f})<extra></extra>"
        ),
    ))

    # Ligne breakeven
    fig.add_hline(y=0, line=dict(color="gray", dash="dash", width=1))

    # Points breakeven
    sign_changes = np.where(np.diff(np.sign(pnl_pct_mid)))[0]
    for idx in sign_changes:
        x0, x1 = spot_range[idx], spot_range[idx + 1]
        y0, y1 = pnl_pct_mid[idx], pnl_pct_mid[idx + 1]
        if y1 != y0:
            be_spot = x0 - y0 * (x1 - x0) / (y1 - y0)
            fig.add_vline(
                x=be_spot,
                line=dict(color="orange", dash="dot", width=1),
                annotation_text=f"BE {be_spot:.1f}",
                annotation_position="top",
            )

    # Ligne spot courant
    fig.add_vline(
        x=current_spot,
        line=dict(color="rgba(255,255,255,0.5)", dash="solid", width=1),
        annotation_text=f"Spot {current_spot:.2f}",
        annotation_position="bottom right",
    )

    # Annotations
    max_loss_idx = int(np.argmin(pnl_pct_mid))
    max_gain_idx = int(np.argmax(pnl_pct_mid))

    fig.add_annotation(
        x=spot_range[max_loss_idx], y=pnl_pct_mid[max_loss_idx],
        text=f"Perte max<br>{max_loss_pct:.1f}%<br>(${pnl_mid[max_loss_idx]:,.0f})",
        showarrow=True, arrowhead=2, font=dict(color="#EF553B"), bgcolor="rgba(0,0,0,0.6)",
    )
    fig.add_annotation(
        x=spot_range[max_gain_idx], y=pnl_pct_mid[max_gain_idx],
        text=f"Gain max<br>{max_gain_pct:.1f}%<br>(${pnl_mid[max_gain_idx]:,.0f})",
        showarrow=True, arrowhead=2, font=dict(color="#00CC96"), bgcolor="rgba(0,0,0,0.6)",
    )
    fig.add_annotation(
        x=0.02, y=0.98, xref="paper", yref="paper",
        text=(
            f"Proba perte : {loss_prob * 100:.1f}%<br>"
            f"Net debit : ${net_debit:,.0f}"
        ),
        showarrow=False,
        align="left",
        font=dict(size=11),
        bgcolor="rgba(0,0,0,0.6)",
        bordercolor="gray",
    )

    if combination.events_in_sweet_zone:
        events_str = ", ".join(combination.events_in_sweet_zone)
        fig.add_annotation(
            text=f"★ Events between expirations: {events_str}",
            xref="paper", yref="paper",
            x=0.02, y=0.88,
            showarrow=False,
            align="left",
            font=dict(size=11, color="gold"),
            bgcolor="rgba(0,0,0,0.6)",
            bordercolor="rgba(255,215,0,0.5)",
        )

    title_legs = " | ".join(
        f"{'L' if leg.direction == 1 else 'S'}{leg.quantity} "
        f"{leg.option_type.upper()[0]} {leg.strike:.0f} "
        f"{leg.expiration.strftime('%d%b%y').upper()}"
        for leg in combination.legs
    )

    fig.update_layout(
        title=dict(text=f"{combination.template_name} — {title_legs}", font=dict(size=13)),
        template="plotly_dark",
        xaxis=dict(title="Prix du sous-jacent"),
        yaxis=dict(title="P&L (% capital)", ticksuffix="%"),
        yaxis2=dict(
            title="P&L ($)",
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        height=1680,
        margin=dict(l=60, r=60, t=60, b=40),
    )

    return fig
