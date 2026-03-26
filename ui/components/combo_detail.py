"""Vue détaillée d'une combinaison sélectionnée."""

import streamlit as st

from data.models import Combination


def render_combo_detail(combination: Combination, metrics: dict) -> None:
    """Affiche les détails d'une combinaison : legs, coûts, métriques."""
    st.subheader("Détails de la combinaison")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Net Debit", f"${combination.net_debit:,.0f}")
    col2.metric("Perte max", f"{metrics['max_loss_pct']:.1f}%")
    col3.metric("Proba perte", f"{metrics['loss_prob_pct']:.1f}%")
    col4.metric("Ratio G/L", f"{metrics['gain_loss_ratio']:.1f}")

    st.caption(f"Template : `{combination.template_name}` — Clôture prévue : {combination.close_date}")

    if combination.event_warning:
        st.warning(combination.event_warning)

    rows = []
    for i, leg in enumerate(combination.legs, 1):
        direction = "Long (+1)" if leg.direction == 1 else "Short (−1)"
        rows.append({
            "Leg": i,
            "Type": leg.option_type.capitalize(),
            "Direction": direction,
            "Qté": leg.quantity,
            "Strike": f"{leg.strike:.2f}",
            "Expiration": leg.expiration.strftime("%d %b %Y"),
            "Prix entrée": f"${leg.entry_price:.2f}",
            "Vol impl.": f"{leg.implied_vol * 100:.1f}%",
            "Volume": f"{leg.volume:,}",
            "OI": f"{leg.open_interest:,}",
        })

    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
