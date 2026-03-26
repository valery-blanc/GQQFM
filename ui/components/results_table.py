"""Tableau comparatif des résultats."""

import pandas as pd
import streamlit as st

from data.models import Combination


def render_results_table(
    combinations: list[Combination],
    metrics: list[dict],   # [{max_loss_pct, loss_prob_pct, max_gain_pct, gain_loss_ratio, score}]
    symbols: list[str] | None = None,
) -> int | None:
    """
    Affiche le tableau des résultats triés par score décroissant.
    Retourne l'index de la ligne sélectionnée (ou None).
    """
    if not combinations:
        st.info("Aucune combinaison ne correspond aux critères.")
        return None

    rows = []
    for i, (combo, m) in enumerate(zip(combinations, metrics)):
        symbol = symbols[i] if symbols else None
        legs_lines = []
        for leg in combo.legs:
            direction = "L" if leg.direction == 1 else "S"
            date_str = leg.expiration.strftime("%d%b%Y").upper()
            strike_str = f"{leg.strike:g}"
            ticker_part = f" {symbol}" if symbol else ""
            legs_lines.append(
                f"{direction}{leg.quantity} {leg.option_type}{ticker_part} {date_str} {strike_str}"
            )
        legs_summary = " | ".join(legs_lines)
        row = {"Rang": i + 1}
        row.update({
            "Template": combo.template_name,
            "Legs": legs_summary,
            "Net Debit ($)": f"{combo.net_debit:,.0f}",
            "Perte max %": f"{m['max_loss_pct']:.1f}%",
            "Proba perte %": f"{m['loss_prob_pct']:.1f}%",
            "Gain max %": f"{m['max_gain_pct']:.1f}%",
            "Ratio G/L": f"{m['gain_loss_ratio']:.1f}",
            "Score": f"{m['score']:.3f}",
        })
        rows.append(row)

    df = pd.DataFrame(rows)

    # Colonne Events — affichée seulement si au moins une combo a des événements favorables
    if any(combo.events_in_sweet_zone for combo in combinations):
        for i, (combo, row) in enumerate(zip(combinations, rows)):
            row["Events"] = ", ".join(combo.events_in_sweet_zone) if combo.events_in_sweet_zone else "—"
        df = pd.DataFrame(rows)

    st.subheader(f"Résultats — {len(combinations)} combinaison(s)")

    # Police réduite pour le tableau (notamment la colonne Legs multi-lignes)
    st.markdown(
        "<style>div[data-testid='stDataFrame'] * { font-size: 0.82em !important; }</style>",
        unsafe_allow_html=True,
    )

    selected_row = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    if selected_row and selected_row.selection.rows:
        return selected_row.selection.rows[0]
    return None
