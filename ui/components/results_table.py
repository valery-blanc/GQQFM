"""Tableau comparatif des résultats."""

import math

import pandas as pd
import streamlit as st

from data.models import Combination


def _fmt_liquidity(val: float) -> str:
    """Format compact pour volume × OI : 42, 1.2k, 35M."""
    if val <= 0:
        return "—"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.1f}k"
    return f"{val:.0f}"


def _fmt_annualized(pct: float) -> str:
    """Affiche le rendement annualisé : >999% → multiplicateur ×, sinon %."""
    if abs(pct) > 999:
        return f"{pct / 100:+.1f}×"
    return f"{pct:+.0f}%"


def _fmt_slippage(pct: float) -> str:
    if math.isnan(pct):
        return "—"
    return f"{pct:.1f}%"


def render_results_table(
    combinations: list[Combination],
    metrics: list[dict],
    symbols: list[str] | None = None,
    realistic_range_pct: float | None = None,
    spot: float | None = None,
    selected_row: int | None = None,
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
        days_lbl  = f"J-{m['days_to_close']}" if "days_to_close" in m else ""
        row = {" ": "▶" if i == selected_row else ""}
        row.update({
            "Rang": i + 1,
            "Template": combo.template_name,
            "Legs": legs_summary,
            "Net Debit ($)": f"{combo.net_debit:+,.0f}",
            "Perte max %": f"{m['max_loss_pct']:.1f}%",
            "Proba perte %": f"{m['loss_prob_pct']:.1f}%",
            "Gain ±1σ %": f"{m.get('max_gain_real_pct', m['max_gain_pct']):.1f}%",
            "Gain ±1σ $": f"${m.get('max_gain_real_dollar', 0):+,.0f}",
            "$/j": f"${m.get('daily_gain_dollar', 0):+,.1f}" if days_lbl else "—",
            "% / an": _fmt_annualized(m.get("annualized_return_pct", 0.0)),
            "Liq.": _fmt_liquidity(m.get("liquidity_score", 0.0)),
            "Disp. vol": f"{m.get('vol_dispersion_pct', 0.0):.1f}%",
            "Slipp.": _fmt_slippage(m.get("slippage_pct", float('nan'))),
            "Ratio G/L": f"{m['gain_loss_ratio']:.2f}",
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
    if spot and metrics:
        m0 = metrics[0]
        range_pct = m0.get("realistic_range_pct", 0.0)
        lo = spot * (1 - range_pct / 100)
        hi = spot * (1 + range_pct / 100)
        st.caption(
            f"fenêtre ±1σ (top combo) = $[{lo:,.0f}, {hi:,.0f}], σ = {range_pct:.1f}% · "
            "**Tous les % sont calculés sur le capital immobilisé** = max(|net_debit|, |perte max|) "
            "— couvre la marge des shorts (FEAT-026b). · "
            "Gain ±1σ $ = valeur absolue · $/j = gain $ / jours jusqu'à J-3 short · "
            "% / an = annualisé · Liq. = min(volume × OI) · "
            "Disp. vol = std(P&L) au spot / capital · Slipp. = Σ(ask−bid) / capital"
        )
    else:
        st.caption(
            "**% calculés sur le capital immobilisé** = max(|net_debit|, |perte max|) (FEAT-026b). · "
            "Gain ±1σ % = gain dans la fenêtre ±1σ / capital · "
            "Gain ±1σ $ = valeur absolue · $/j = gain $ / jours jusqu'à J-3 short · "
            "% / an = annualisé · Liq. = min(volume × OI) · "
            "Disp. vol = std(P&L) au spot / capital · Slipp. = Σ(ask−bid) / capital"
        )

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
