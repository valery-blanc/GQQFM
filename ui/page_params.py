"""Page Paramètres — tous les widgets de configuration du scan."""

import streamlit as st

import config
from config import ScoreWeights
from templates import ALL_TEMPLATES
from ui.components.sidebar import _cached_risk_free_rate

_WEIGHT_FIELDS = [
    ("w_gain_real",  "Gain max ±1σ ($)",
     "Gain max réaliste en dollars dans la fenêtre ±1σ — priorité #1. En $ (pas en %) "
     "car le rendement annualisé tient déjà compte du capital immobilisé."),
    ("w_annualized", "Rendement annualisé (%)",
     "max_gain_real / capital_immobilisé × 365 / days_to_close."),
    ("w_loss_prob",  "Sécurité — proba perte",
     "Récompense les combos avec faible probabilité de perte (lognormale)."),
    ("w_max_loss",   "Sécurité — perte max %",
     "Récompense les combos avec faible perte max % du capital immobilisé."),
    ("w_liquidity",  "Liquidité",
     "min(volume × open_interest) sur les legs — exécutabilité réelle."),
    ("w_robustness", "Robustesse à la vol",
     "Stabilité du P&L au spot courant entre les scénarios de vol."),
    ("w_slippage",   "Slippage (bid/ask)",
     "Pénalité spread bid/ask. Neutre (médiane) si données absentes."),
]


def _render_score_weights() -> None:
    if "score_weights" not in st.session_state:
        st.session_state["score_weights"] = ScoreWeights()

    sw: ScoreWeights = st.session_state["score_weights"]

    with st.expander("⚖️ Pondération du score (avancé)", expanded=False):
        if st.button("Réinitialiser les poids par défaut", key="reset_score_weights",
                     use_container_width=True):
            st.session_state["score_weights"] = ScoreWeights()
            st.rerun()

        from dataclasses import asdict
        current = asdict(sw)
        total = sum(current.values()) or 1.0
        st.caption(
            "Les poids sont renormalisés (somme = 100%) avant calcul. "
            "Ajustez l'importance relative de chaque composant du score."
        )

        new_values: dict[str, float] = {}
        for field, label, help_text in _WEIGHT_FIELDS:
            value = float(current[field])
            normalized_share = value / total
            new_values[field] = st.slider(
                f"{label} — {normalized_share:.0%}",
                min_value=0.0, max_value=1.0,
                value=value, step=0.05,
                key=f"sw_{field}",
                help=help_text,
            )

        new_total = sum(new_values.values()) or 1.0
        st.caption(f"Somme brute : **{new_total:.2f}** (renormalisée à 1.0).")

        if any(abs(new_values[k] - current[k]) > 1e-9 for k in current):
            st.session_state["score_weights"] = ScoreWeights(**new_values)


def render_params_page() -> None:
    """Rend tous les widgets de paramètres dans la page Paramètres."""
    st.header("Paramètres")

    # ── Templates ──────────────────────────────────────────────────────────
    st.subheader("Templates")
    for name, tmpl in ALL_TEMPLATES.items():
        st.checkbox(tmpl.description, value=True, key=f"tmpl_{name}")

    st.markdown("---")

    # ── Critères ───────────────────────────────────────────────────────────
    st.subheader("Critères")
    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            "Perte max (%)", value=-50.0, max_value=0.0, step=0.5,
            key="p_max_loss_pct",
            help="Perte maximale admissible en % du capital engagé (valeur négative).",
        )
        st.number_input(
            "Gain min (%)", value=10.0, min_value=0.0, step=5.0,
            key="p_min_gain_pct",
        )
        st.number_input(
            "Budget max ($)", value=10_000.0, min_value=0.0, step=500.0,
            key="p_max_debit",
        )
    with col2:
        st.number_input(
            "Proba perte (%)", value=25.0, min_value=0.0, max_value=100.0, step=1.0,
            key="p_max_loss_prob",
        )
        st.number_input(
            "Ratio G/L min", value=0.1, min_value=0.0, step=0.1,
            key="p_min_gl_ratio",
        )
        st.number_input(
            "Volume moyen min", value=0, min_value=0, step=10,
            key="p_min_volume",
        )

    st.markdown("---")

    # ── Scénarios de volatilité ─────────────────────────────────────────────
    st.subheader("Scénarios de volatilité")
    st.caption("Le scénario médian (1.0×) est fixe et sert au filtrage.")
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        st.slider("Vol basse (×)", 0.5, 0.95, 0.8, 0.05, key="p_vol_low")
    with col_v2:
        st.slider("Vol haute (×)", 1.05, 2.0, 1.2, 0.05, key="p_vol_high")

    st.markdown("---")

    # ── Avancé ─────────────────────────────────────────────────────────────
    with st.expander("Avancé", expanded=True):
        rfr_default, rfr_source = _cached_risk_free_rate()
        rfr_help = (
            f"^IRX (T-bill 13 semaines) live = {rfr_default * 100:.3f} %"
            if rfr_source == "live"
            else f"Yahoo indisponible — fallback constante = {rfr_default * 100:.3f} %"
        )
        if "p_risk_free_rate" not in st.session_state:
            st.session_state["p_risk_free_rate"] = rfr_default
        st.number_input(
            "Taux sans risque", min_value=0.0, max_value=0.2, step=0.005,
            format="%.3f", key="p_risk_free_rate", help=rfr_help,
        )
        rfr_label = "✓ ^IRX live" if rfr_source == "live" else "⚠ fallback constante"
        st.caption(f"{rfr_label} — {rfr_default * 100:.3f} %")

        st.number_input(
            "Max combinaisons", value=400_000, min_value=1_000, max_value=500_000,
            step=10_000, key="p_max_combos",
            help="Réduire pour accélérer en mode CPU. GPU peut gérer 500K.",
        )
        st.slider(
            "Profil P&L à J-N (avant expiration short)",
            min_value=0, max_value=10, value=3, step=1,
            key="p_days_before_close",
            help=(
                "Horizon de pricing du profil P&L : N jours avant l'expiration "
                "des jambes courtes. 0 = à l'expiration exacte, "
                "3 = J-3 (cible réaliste recommandée)."
            ),
        )

        st.markdown("**Échéance des legs (DTE)**")
        st.slider(
            "Short leg (jours)", min_value=2, max_value=60,
            value=config.SCANNER_NEAR_EXPIRY_RANGE, step=1,
            key="p_near_expiry",
            help="Plage DTE pour la jambe courte. Sweet zone théta/gamma : 21–35 j.",
        )
        st.slider(
            "Long leg (jours)", min_value=20, max_value=config.MAX_DAYS_TO_EXPIRY,
            value=config.SCANNER_FAR_EXPIRY_RANGE, step=1,
            key="p_far_expiry",
            help=f"Plage DTE pour la jambe longue (max {config.MAX_DAYS_TO_EXPIRY} j).",
        )

        st.radio(
            "Pricer",
            options=["Pricer américain : Bjerksund-Stensland", "Pricer européen : Black-Scholes"],
            index=0,
            key="p_pricer",
            help=(
                "Bjerksund-Stensland 1993 : tient compte de la prime d'exercice anticipé "
                "et du dividende. Plus précis pour les options US.\n\n"
                "Black-Scholes : pricer européen classique."
            ),
        )

    st.markdown("---")

    # ── Pondération du score ────────────────────────────────────────────────
    _render_score_weights()

    st.markdown("---")

    # ── Info système ────────────────────────────────────────────────────────
    st.subheader("Info système")
    from engine.backend import get_device_info
    device = get_device_info()
    if device:
        st.caption(f"Device: {device['name']}")
        st.caption(
            f"VRAM: {device['vram_total_gb'] - device['vram_free_gb']:.1f}"
            f" / {device['vram_total_gb']:.1f} GB"
        )
    else:
        st.caption("Pas de GPU — mode CPU (NumPy)")

    from events.calendar import EventCalendar
    if EventCalendar.resolve_api_key():
        st.caption("Finnhub: ✓ clé API active")
    else:
        st.caption("Finnhub: ✗ FOMC statiques uniquement")
