"""Vue détaillée d'une combinaison sélectionnée."""

from datetime import date, timedelta

import streamlit as st

from data.models import Combination


def _render_exit_plan(combination: Combination) -> None:
    """Affiche les seuils de sortie recommandés (target / stop / date butoir)."""
    net_debit = combination.net_debit
    target_profit = net_debit * 0.30
    stop_loss = net_debit * 0.50

    deadline = combination.close_date - timedelta(days=3)
    days_left = (deadline - date.today()).days

    st.markdown("##### Plan de sortie")

    if combination.events_in_sweet_zone:
        events_str = ", ".join(combination.events_in_sweet_zone)
        st.info(
            f"📅 Sortie post-event recommandée : fermer dès le lendemain de "
            f"**{events_str}** (l'IV crush attendu est la thèse de la position)."
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Target profit (+30 %)", f"+${target_profit:,.0f}")
    col2.metric("Stop loss (−50 %)", f"−${stop_loss:,.0f}")
    col3.metric("Date butoir (J-3 short)", deadline.strftime("%d %b %Y"))

    if days_left < 0:
        days_label = "dépassée"
    elif days_left < 5:
        days_label = f"⚠ {days_left} j"
    else:
        days_label = f"{days_left} j"
    col4.metric("Jours restants", days_label)

    st.caption(
        "Couper aussi si : spot sort de ±15 % du strike central "
        "(thèse vol/temps cassée) ou si la perte courante atteint le stop −50 %."
    )


def _check_ex_div_warning(combination: Combination, symbol: str | None) -> str | None:
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

        # exDividendDate est un timestamp Unix
        from datetime import datetime, timezone
        ex_date = datetime.fromtimestamp(ex_date_ts, tz=timezone.utc).date()

        today = date.today()
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


def render_combo_detail(combination: Combination, metrics: dict, symbol: str | None = None) -> None:
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

    ex_div_warning = _check_ex_div_warning(combination, symbol)
    if ex_div_warning:
        st.info(ex_div_warning)

    _render_exit_plan(combination)

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
