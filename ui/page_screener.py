"""Page Screener automatique de sous-jacents."""

import threading

import streamlit as st

import config

# État background du screener — survit aux changements de tab (module-level)
_bg: dict = {
    "running": False,
    "progress": 0.0,
    "status": "",
    "results": None,
    "error": None,
}


def _launch_screener(top_n: int, profile: str, include_high_vol: bool) -> None:
    """Lance le screener dans un thread daemon (survit aux reruns Streamlit)."""
    if _bg["running"]:
        return
    _bg.update({"running": True, "results": None, "error": None,
                "progress": 0.0, "status": "Démarrage…"})

    def _run() -> None:
        from screener import UnderlyingScreener

        def on_progress(pct: float, msg: str) -> None:
            _bg["progress"] = pct / 100.0
            _bg["status"] = msg

        try:
            screener = UnderlyingScreener()
            results = screener.screen(
                top_n=top_n,
                profile=profile,
                include_high_vol=include_high_vol,
                progress_callback=on_progress,
            )
            _bg["results"] = results
        except Exception as exc:
            _bg["error"] = str(exc)
        finally:
            _bg["running"] = False

    threading.Thread(target=_run, daemon=True).start()


@st.fragment(run_every=1)
def _progress_fragment() -> None:
    """Polling 1 fois/seconde — affiche la progression et capte la fin du scan."""
    if _bg["running"]:
        st.progress(_bg["progress"], text=_bg["status"] or "En cours…")
    elif _bg["results"] is not None:
        st.session_state["screener_results"] = _bg["results"]
        _bg["results"] = None
        st.rerun()
    elif _bg["error"] is not None:
        err = _bg["error"]
        _bg["error"] = None
        st.error(f"Erreur screener : {err}")


def render_screener_page() -> None:
    """Rend la page screener sous-jacents."""
    from screener.models import ScreenerResult

    st.header("🔎 Screener automatique de sous-jacents")

    col1, col2 = st.columns([2, 2])

    with col1:
        top_n = st.selectbox(
            "Nombre de résultats",
            options=list(range(1, 11)),
            index=config.SCREENER_DEFAULT_TOP_N - 1,
            key="screener_top_n",
        )

        profile_label = st.radio(
            "Stratégie cible",
            options=["Calendar / Double Calendar", "Reverse Iron Condor"],
            index=0,
            key="screener_profile",
            help=(
                "Calendar : privilégie IV Rank modéré, vol stable, mean revert.\n"
                "Reverse IC : privilégie IV Rank bas, vol qui accélère, ATR élevé."
            ),
        )
        profile = "ric" if "Reverse" in profile_label else "calendar"

        include_high_vol = st.checkbox(
            "Inclure tickers haute vol",
            value=False,
            key="screener_include_high_vol",
            help="COIN, PLTR, MRNA, BIIB, NIO, BABA, etc. — souvent inadaptés calendar.",
        )

    with col2:
        try:
            from screener.screener import _is_us_market_open
            if not _is_us_market_open():
                st.warning(
                    "Marché US fermé. Les données IV peuvent être imprécises. "
                    "Pour un screening fiable, relancez pendant les heures de marché "
                    "(15h30–22h00 heure de Genève)."
                )
        except Exception:
            pass

    run_screener = st.button(
        "🔍 Trouver les meilleurs sous-jacents",
        type="primary",
        use_container_width=False,
        key="run_screener",
        disabled=_bg["running"],
    )

    if run_screener:
        _launch_screener(top_n, profile, include_high_vol)
        st.rerun()

    # Fragment de polling actif dès qu'un screener est en cours ou a terminé
    if _bg["running"] or _bg["results"] is not None or _bg["error"] is not None:
        _progress_fragment()

    results: list[ScreenerResult] = st.session_state.get("screener_results", [])

    if not results:
        if not _bg["running"]:
            st.info(
                "Lancez le screener pour trouver automatiquement les sous-jacents "
                "les mieux adaptés à vos templates."
            )
        return

    n_disq = sum(1 for r in results if r.disqualification_reason)
    if n_disq:
        st.success(f"✓ {len(results)} sous-jacent(s) ({n_disq} fallback ⚠)")
    else:
        st.success(f"✓ {len(results)} sous-jacent(s) trouvé(s)")

    col_list, col_detail = st.columns([1, 2])

    with col_list:
        for i, r in enumerate(results):
            star = " ★" if r.has_event_bonus else ""
            if r.disqualification_reason:
                st.caption(
                    f"{i + 1}. **{r.symbol}** (score {r.score:.0f}){star}"
                    f" ⚠ _{r.disqualification_reason}_"
                )
            else:
                st.caption(f"{i + 1}. **{r.symbol}** (score {r.score:.0f}){star}")

        if st.button("Utiliser ces résultats (Live + Backtest)", key="use_screener_results",
                     type="primary"):
            tickers = ",".join(r.symbol for r in results)
            st.session_state["live_symbols_input"] = tickers
            st.session_state["bt_symbols_input"] = tickers
            st.success(f"Tickers injectés : {tickers}")

    with col_detail:
        with st.expander("Détails du screening", expanded=True):
            for r in results:
                events_str = ""
                if r.events_in_near_zone:
                    events_str += f" ⚠ {', '.join(r.events_in_near_zone)}"
                if r.events_in_sweet_zone:
                    events_str += f" ★ {', '.join(r.events_in_sweet_zone)}"
                st.caption(
                    f"**{r.symbol}** | Score {r.score:.0f} ({r.profile}) | "
                    f"IV Rank 52w {r.iv_rank_52w:.0f} | "
                    f"Term {r.term_structure_ratio:.2f} | "
                    f"Spread {r.avg_option_spread_pct:.1%} | "
                    f"ATR {r.atr_pct:.1%} | "
                    f"HV20/60 {r.hv_ratio_20_60:.2f} | "
                    f"AC1 {r.autocorr_1d:+.2f}{events_str}"
                )
            st.caption(
                "_IV Rank 52w : Polygon historique quand disponible (≥10 pts), "
                "sinon approximé depuis HV historique._"
            )
