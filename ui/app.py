"""Application Streamlit principale — Options P&L Scanner."""

import streamlit as st

from ui.components.sidebar import get_base_params

st.set_page_config(
    page_title="Options P&L Scanner",
    page_icon="📈",
    layout="wide",
)


def main():
    st.title("Options P&L Scanner")

    tab_live, tab_backtest, tab_tracker, tab_screener, tab_params = st.tabs([
        "📈 Live",
        "📅 Backtest",
        "🎯 Tracker prix réel",
        "🔎 Screener sous-jacents",
        "⚙️ Paramètres",
    ])

    # Les paramètres doivent être rendus en premier pour peupler session_state
    with tab_params:
        from ui.page_params import render_params_page
        render_params_page()

    # Assembler le dict params depuis session_state
    base_params = get_base_params()

    with tab_screener:
        from ui.page_screener import render_screener_page
        render_screener_page()

    with tab_live:
        from ui.page_live import render_live_page
        render_live_page(base_params)

    with tab_backtest:
        from ui.page_backtest import render_backtest_page
        render_backtest_page(base_params)

    with tab_tracker:
        from ui.page_tracker import render_tracker_page
        render_tracker_page()


if __name__ == "__main__":
    main()
