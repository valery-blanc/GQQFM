"""Vue détaillée d'une combinaison sélectionnée."""

from datetime import date, timedelta

import numpy as np
import streamlit as st

from data.models import Combination

REALISTIC_MOVE_PCT = 0.03  # ±3 % : amplitude typique d'un sous-jacent sur quelques jours


def _render_exit_plan(
    combination: Combination,
    metrics: dict,
    pnl_tensor: np.ndarray,
    spot_range: np.ndarray,
    current_spot: float,
    as_of: date | None = None,
    days_before_close: int = 3,
) -> None:
    """Affiche les seuils de sortie calibrés sur la courbe P&L réelle."""
    net_debit = combination.net_debit
    if net_debit <= 0:
        return

    # Target réaliste : max P&L observé si le spot reste dans ±3 % (vol médiane)
    pnl_mid = pnl_tensor[1]
    pct_change = spot_range / current_spot - 1.0
    in_range = (pct_change >= -REALISTIC_MOVE_PCT) & (pct_change <= REALISTIC_MOVE_PCT)
    if in_range.any():
        target_dollar = float(pnl_mid[in_range].max())
    else:
        target_dollar = float(pnl_mid.max())
    target_pct = target_dollar / net_debit * 100

    # Stop loss = perte max structurelle (déjà calculée par le scanner)
    max_loss_pct = metrics["max_loss_pct"]   # négatif
    stop_dollar = max_loss_pct / 100 * net_debit

    deadline = combination.close_date - timedelta(days=days_before_close)
    days_left = (deadline - (as_of or date.today())).days

    st.markdown("##### Plan de sortie")

    if combination.events_in_sweet_zone:
        events_str = ", ".join(combination.events_in_sweet_zone)
        st.info(
            f"📅 Sortie post-event recommandée : fermer dès le lendemain de "
            f"**{events_str}** (l'IV crush attendu est la thèse de la position)."
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        f"Target (spot ±{int(REALISTIC_MOVE_PCT*100)} %)",
        f"+${target_dollar:,.0f}",
        delta=f"+{target_pct:.1f}% capital",
        delta_color="off",
    )
    col2.metric(
        "Stop loss (perte max struct.)",
        f"−${abs(stop_dollar):,.0f}",
        delta=f"{max_loss_pct:.1f}% capital",
        delta_color="off",
    )
    col3.metric(f"Date butoir (J-{days_before_close} short)", deadline.strftime("%d %b %Y"))

    if days_left < 0:
        days_label = "dépassée"
    elif days_left < 5:
        days_label = f"⚠ {days_left} j"
    else:
        days_label = f"{days_left} j"
    col4.metric("Jours restants", days_label)

    st.caption(
        f"Target = P&L max sur la courbe vol médiane si le spot reste dans ±{int(REALISTIC_MOVE_PCT*100)} % "
        f"sur quelques jours. Stop = perte max structurelle de la combo (le pire cas que le scanner a identifié)."
    )


def _check_ex_div_warning(combination: Combination, symbol: str | None,
                          as_of: date | None = None) -> str | None:
    """Vérifie si un ex-dividende tombe pendant la vie de la position."""
    if not symbol:
        return None
    try:
        import yfinance as yf
        from datetime import date

        ticker = yf.Ticker(symbol)
        ex_date_ts = ticker.info.get("exDividendDate")
        if not ex_date_ts:
            return None

        from datetime import datetime, timezone
        ex_date = datetime.fromtimestamp(ex_date_ts, tz=timezone.utc).date()

        today = as_of or date.today()
        close_date = combination.close_date

        if today <= ex_date <= close_date:
            return (
                f"Ex-dividende {symbol} le {ex_date.strftime('%d/%m/%Y')} "
                f"pendant la vie de la position. Les prix des options seront "
                f"ajustés à cette date (calls baissent, puts montent)."
            )
    except Exception:
        pass
    return None


def render_combo_detail(
    combination: Combination,
    metrics: dict,
    symbol: str | None = None,
    pnl_tensor: np.ndarray | None = None,
    spot_range: np.ndarray | None = None,
    current_spot: float | None = None,
    as_of: date | None = None,
    days_before_close: int = 3,
) -> None:
    """Affiche les détails d'une combinaison : legs, coûts, métriques."""
    st.subheader("Détails de la combinaison")

    gain_real = metrics.get("max_gain_real_pct", metrics.get("max_gain_pct", 0))
    gain_abs  = metrics.get("max_gain_pct", 0)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Net Debit", f"${combination.net_debit:,.0f}")
    col2.metric("Perte max", f"{metrics['max_loss_pct']:.1f}%")
    col3.metric("Proba perte", f"{metrics['loss_prob_pct']:.1f}%")
    col4.metric("Gain ±1σ", f"{gain_real:.1f}%",
                delta=f"max absolu {gain_abs:.0f}%", delta_color="off")

    st.caption(f"Template : `{combination.template_name}` — Clôture prévue : {combination.close_date}")

    if combination.event_warning:
        st.warning(combination.event_warning)

    ex_div_warning = _check_ex_div_warning(combination, symbol, as_of=as_of)
    if ex_div_warning:
        st.info(ex_div_warning)

    if pnl_tensor is not None and spot_range is not None and current_spot is not None:
        _render_exit_plan(combination, metrics, pnl_tensor, spot_range, current_spot,
                          as_of=as_of, days_before_close=days_before_close)

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
