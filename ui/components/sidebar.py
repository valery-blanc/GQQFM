"""Lecture des paramètres depuis session_state (widgets rendus dans page_params.py)."""

import streamlit as st

import config
from config import ScoreWeights
from data.models import ScoringCriteria
from templates import ALL_TEMPLATES


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_risk_free_rate() -> tuple[float, str]:
    """Cache 1h pour le taux ^IRX."""
    from data.risk_free_rate import fetch_risk_free_rate
    return fetch_risk_free_rate()


def get_base_params() -> dict:
    """
    Assemble le dict params à partir des clés session_state (peuplées par page_params.py).
    Toutes les valeurs ont des defaults identiques aux valeurs initiales des widgets.
    """
    ss = st.session_state

    selected_templates = [
        name for name in ALL_TEMPLATES
        if ss.get(f"tmpl_{name}", True)
    ]

    criteria = ScoringCriteria(
        max_loss_pct=float(ss.get("p_max_loss_pct", -50.0)),
        max_loss_probability_pct=float(ss.get("p_max_loss_prob", 25.0)),
        min_max_gain_pct=float(ss.get("p_min_gain_pct", 10.0)),
        min_gain_loss_ratio=float(ss.get("p_min_gl_ratio", 0.1)),
        max_net_debit=float(ss.get("p_max_debit", 10_000.0)),
        min_avg_volume=int(ss.get("p_min_volume", 0)),
    )

    pricer_val = str(ss.get("p_pricer", "Pricer américain : Bjerksund-Stensland"))
    use_american = pricer_val.startswith("Pricer américain")

    near = ss.get("p_near_expiry", config.SCANNER_NEAR_EXPIRY_RANGE)
    far  = ss.get("p_far_expiry",  config.SCANNER_FAR_EXPIRY_RANGE)

    return {
        "selected_templates":  selected_templates,
        "criteria":            criteria,
        "vol_low":             float(ss.get("p_vol_low", 0.8)),
        "vol_high":            float(ss.get("p_vol_high", 1.2)),
        "use_hv_calibration":  bool(ss.get("p_use_hv_calibration", False)),  # FEAT-030-C (default OFF — voir FEAT-029)
        "risk_free_rate":      float(ss.get("p_risk_free_rate", config.DEFAULT_RISK_FREE_RATE)),
        "max_combinations":    int(ss.get("p_max_combos", 400_000)),
        "days_before_close":   int(ss.get("p_days_before_close", 3)),
        "use_american_pricer": use_american,
        "near_expiry_range":   tuple(near),
        "far_expiry_range":    tuple(far),
        "score_weights":       ss.get("score_weights", ScoreWeights()),
        "scan_clicked":        False,
    }
