"""Panneau latéral Streamlit : saisie des paramètres."""

from datetime import date

import streamlit as st

import config
from data.models import ScoringCriteria
from data.risk_free_rate import fetch_risk_free_rate
from engine.backend import get_device_info
from templates import ALL_TEMPLATES


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_risk_free_rate() -> tuple[float, str]:
    """Cache 1h : évite de re-fetcher ^IRX à chaque rerun de la sidebar."""
    return fetch_risk_free_rate()


def _render_screener_section() -> None:
    """
    Section screener dans la sidebar.
    Lance le screening et injecte les tickers résultants dans session_state["symbols_input"].
    """
    from screener import UnderlyingScreener
    from screener.models import ScreenerResult

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 Screener automatique")

    top_n = st.sidebar.selectbox(
        "Nombre de résultats",
        options=list(range(1, 11)),
        index=config.SCREENER_DEFAULT_TOP_N - 1,
        key="screener_top_n",
    )

    run_screener = st.sidebar.button(
        "🔍 Trouver les meilleurs sous-jacents",
        use_container_width=True,
        key="run_screener",
    )

    if run_screener:
        progress_bar = st.sidebar.progress(0.0)
        status_text = st.sidebar.empty()

        def on_progress(pct: float, msg: str) -> None:
            progress_bar.progress(min(pct / 100.0, 1.0))
            status_text.caption(msg)

        try:
            screener = UnderlyingScreener()
            results: list[ScreenerResult] = screener.screen(
                top_n=top_n,
                progress_callback=on_progress,
            )
            st.session_state["screener_results"] = results
        except Exception as exc:
            st.sidebar.error(f"Erreur screener : {exc}")
            st.session_state["screener_results"] = []
        finally:
            progress_bar.empty()
            status_text.empty()

    # Affichage des résultats précédents
    results: list[ScreenerResult] = st.session_state.get("screener_results", [])
    if results:
        st.sidebar.success(f"✓ {len(results)} sous-jacent(s) trouvé(s)")
        for i, r in enumerate(results):
            star = " ★" if r.has_event_bonus else ""
            st.sidebar.caption(f"{i + 1}. **{r.symbol}** (score {r.score:.0f}){star}")

        if st.sidebar.button("Utiliser ces résultats", key="use_screener_results"):
            # Clé intermédiaire : appliquée AVANT la création du widget text_input
            st.session_state["_inject_symbols"] = ",".join(r.symbol for r in results)
            st.rerun()

        with st.sidebar.expander("Détails du screening"):
            for r in results:
                events_str = ""
                if r.events_in_near_zone:
                    events_str += f" ⚠ {', '.join(r.events_in_near_zone)}"
                if r.events_in_sweet_zone:
                    events_str += f" ★ {', '.join(r.events_in_sweet_zone)}"
                st.caption(
                    f"**{r.symbol}** | Score {r.score:.0f} | "
                    f"IV Rank {r.iv_rank_proxy:.0f} | "
                    f"Term {r.term_structure_ratio:.2f} | "
                    f"Spread {r.avg_option_spread_pct:.1%}{events_str}"
                )

    # Avertissement hors-séance
    try:
        from screener.screener import _is_us_market_open
        if not _is_us_market_open():
            st.sidebar.warning(
                "Marché US fermé. Les données IV peuvent être imprécises. "
                "Pour un screening fiable, relancez pendant les heures de marché "
                "(15h30–22h00 heure de Genève)."
            )
    except Exception:
        pass


def render_sidebar() -> dict:
    """
    Affiche la sidebar et retourne un dict avec tous les paramètres saisis.

    Retourne:
        mode: "live" | "backtest"
        as_of: date | None        (uniquement en backtest)
        symbols: list[str]
        selected_templates: list[str]
        criteria: ScoringCriteria
        vol_low: float
        vol_high: float
        risk_free_rate: float
        max_combinations: int
        scan_clicked: bool
    """
    st.sidebar.title("Options P&L Scanner")

    from datetime import date as _date_today, timedelta as _td
    mode = st.sidebar.radio(
        "Mode",
        options=["Live (yfinance)", "Backtest (Polygon historique)"],
        index=0,
        help=(
            "**Live** : scan en temps réel via Yahoo Finance.\n\n"
            "**Backtest** : rejoue un scan à une date passée via Polygon (free tier 5/min, "
            "première requête peut prendre 20-30 min puis cache instantané)."
        ),
    )
    is_backtest = mode.startswith("Backtest")
    as_of: date | None = None
    if is_backtest:
        # Polygon free tier : 2 ans d'historique
        max_as_of = _date_today.today() - _td(days=1)
        min_as_of = _date_today.today() - _td(days=2 * 365)
        as_of = st.sidebar.date_input(
            "Date d'entrée (as_of)",
            value=max_as_of - _td(days=60),
            min_value=min_as_of,
            max_value=max_as_of,
            help="Polygon free : 2 ans d'historique max.",
        )

    # Injection depuis le screener : appliquée AVANT la création du widget
    if "_inject_symbols" in st.session_state:
        st.session_state.symbols_input = st.session_state.pop("_inject_symbols")
    elif "symbols_input" not in st.session_state:
        st.session_state.symbols_input = "SPY"

    raw = st.sidebar.text_input(
        "Sous-jacent(s)",
        key="symbols_input",
        help="Un ou plusieurs tickers séparés par des virgules : SPY,AAPL,NVDA",
    )
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

    # Section screener (en haut, avant les templates)
    _render_screener_section()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Templates")
    selected_templates = []
    for name, tmpl in ALL_TEMPLATES.items():
        checked = st.sidebar.checkbox(tmpl.description, value=(name == "calendar_strangle"), key=f"tmpl_{name}")
        if checked:
            selected_templates.append(name)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Critères")
    max_loss_pct = st.sidebar.number_input(
        "Perte max (%)", value=-50.0, max_value=0.0, step=0.5,
        help="Perte maximale admissible en % du capital engagé (valeur négative)."
    )
    max_loss_prob = st.sidebar.number_input(
        "Proba perte (%)", value=25.0, min_value=0.0, max_value=100.0, step=1.0
    )
    min_max_gain_pct = st.sidebar.number_input(
        "Gain min (%)", value=10.0, min_value=0.0, step=5.0
    )
    min_gain_loss_ratio = st.sidebar.number_input(
        "Ratio G/L min", value=0.1, min_value=0.0, step=0.1
    )
    max_net_debit = st.sidebar.number_input(
        "Budget max ($)", value=10_000.0, min_value=0.0, step=500.0
    )
    min_avg_volume = st.sidebar.number_input(
        "Volume moyen min", value=0, min_value=0, step=10
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("Scénarios de volatilité")
    st.sidebar.caption("Le scénario médian (1.0×) est fixe et sert au filtrage.")
    vol_low = st.sidebar.slider("Vol basse (×)", 0.5, 0.95, 0.8, 0.05)
    vol_high = st.sidebar.slider("Vol haute (×)", 1.05, 2.0, 1.2, 0.05)

    with st.sidebar.expander("Avancé", expanded=True):
        rfr_default, rfr_source = _cached_risk_free_rate()
        rfr_help = (
            f"^IRX (T-bill 13 semaines) live = {rfr_default * 100:.3f} %"
            if rfr_source == "live"
            else f"Yahoo indisponible — fallback constante = {rfr_default * 100:.3f} %"
        )
        risk_free_rate = st.number_input(
            "Taux sans risque", value=rfr_default,
            min_value=0.0, max_value=0.2, step=0.005, format="%.3f",
            help=rfr_help,
        )
        rfr_label = "✓ ^IRX live" if rfr_source == "live" else "⚠ fallback constante"
        st.caption(f"{rfr_label} — {rfr_default * 100:.3f} %")
        max_combinations = st.number_input(
            "Max combinaisons", value=50_000, min_value=1_000, max_value=500_000,
            step=10_000,
            help="Réduire pour accélérer en mode CPU. GPU peut gérer 500K."
        )

        st.markdown("**Échéance des legs (DTE)**")
        near_expiry_range = st.slider(
            "Short leg (jours)",
            min_value=2, max_value=60,
            value=config.SCANNER_NEAR_EXPIRY_RANGE,
            step=1,
            help=(
                "Plage de jours avant expiration pour la jambe la plus courte. "
                "En dessous de 14 j, le gamma cliff rend la position très sensible "
                "aux mouvements du spot. La sweet zone théta/gamma est 21-35 j."
            ),
        )
        far_expiry_range = st.slider(
            "Long leg (jours)",
            min_value=20, max_value=config.MAX_DAYS_TO_EXPIRY,
            value=config.SCANNER_FAR_EXPIRY_RANGE,
            step=1,
            help=(
                "Plage de jours avant expiration pour la jambe la plus longue. "
                f"Le data provider ne récupère que les expirations ≤ {config.MAX_DAYS_TO_EXPIRY} j."
            ),
        )

        pricer_choice = st.radio(
            "Pricer",
            options=["Pricer américain : Bjerksund-Stensland", "Pricer européen : Black-Scholes"],
            index=0,
            help=(
                "Bjerksund-Stensland 1993 : tient compte de la prime d'exercice anticipé "
                "et du dividende. Plus précis pour les options US.\n\n"
                "Black-Scholes : pricer européen classique, ignore l'exercice anticipé."
            ),
        )
        use_american_pricer = pricer_choice.startswith("Pricer américain")

    scan_clicked = st.sidebar.button("🔍 Lancer le scan", use_container_width=True)

    st.sidebar.markdown("---")
    st.sidebar.subheader("GPU Info")
    device = get_device_info()
    if device:
        st.sidebar.caption(f"Device: {device['name']}")
        st.sidebar.caption(
            f"VRAM: {device['vram_total_gb'] - device['vram_free_gb']:.1f}"
            f" / {device['vram_total_gb']:.1f} GB"
        )
    else:
        st.sidebar.caption("Pas de GPU — mode CPU (NumPy)")

    from events.calendar import EventCalendar
    if EventCalendar.resolve_api_key():
        st.sidebar.caption("Finnhub: ✓ clé API active")
    else:
        st.sidebar.caption("Finnhub: ✗ FOMC statiques uniquement")

    criteria = ScoringCriteria(
        max_loss_pct=max_loss_pct,
        max_loss_probability_pct=max_loss_prob,
        min_max_gain_pct=min_max_gain_pct,
        min_gain_loss_ratio=min_gain_loss_ratio,
        max_net_debit=max_net_debit,
        min_avg_volume=int(min_avg_volume),
    )

    return {
        "mode": "backtest" if is_backtest else "live",
        "as_of": as_of,
        "symbols": symbols,
        "selected_templates": selected_templates,
        "criteria": criteria,
        "vol_low": vol_low,
        "vol_high": vol_high,
        "risk_free_rate": risk_free_rate,
        "max_combinations": int(max_combinations),
        "use_american_pricer": use_american_pricer,
        "near_expiry_range": tuple(near_expiry_range),
        "far_expiry_range": tuple(far_expiry_range),
        "scan_clicked": scan_clicked,
    }
