"""Score composite et classement des combinaisons filtrées."""

import config
from engine.backend import xp
from scoring.filters import realistic_max_gain
from scoring.probability import compute_loss_probability


def score_combinations(
    pnl_mid: "xp.ndarray",         # shape (C, M)
    net_debits: "xp.ndarray",      # shape (C,)
    spot_range: "xp.ndarray",      # shape (M,)
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
    event_score_factors: "xp.ndarray | None" = None,  # shape (C,), multiplicateur
) -> "xp.ndarray":
    """
    Score composite entre 0 et 1 pour classer les combinaisons filtrées.

    Score = (w1 * norm(gain_loss_ratio)
           + w2 * (1 - norm(loss_prob))
           + w3 * norm(expected_return))
           × event_score_factor

    Poids par défaut : 0.4, 0.3, 0.3
    Si event_score_factors est None : factor=1.0 (rétro-compatible).
    """
    safe_debits = xp.where(net_debits == 0, xp.ones_like(net_debits) * 1e-6, net_debits)

    max_gain_real = realistic_max_gain(pnl_mid, spot_range, current_spot, atm_vol, days_to_close)
    max_loss = xp.abs(pnl_mid.min(axis=1))
    safe_loss = xp.where(max_loss == 0, xp.ones_like(max_loss) * 1e-6, max_loss)

    gain_loss_ratio = max_gain_real / safe_loss

    loss_prob = compute_loss_probability(
        pnl_mid, spot_range, current_spot, atm_vol, days_to_close, risk_free_rate
    )

    # Expected return : espérance du P&L pondéré par la distribution log-normale
    expected_return = _compute_expected_return(
        pnl_mid, spot_range, current_spot, atm_vol, days_to_close,
        risk_free_rate, safe_debits
    )

    # Normalisation min-max par métrique
    def norm(arr):
        mn, mx = arr.min(), arr.max()
        rng = mx - mn
        if rng < 1e-10:
            return xp.zeros_like(arr)
        return (arr - mn) / rng

    score = (
        config.SCORE_WEIGHT_GAIN_LOSS_RATIO * norm(gain_loss_ratio)
        + config.SCORE_WEIGHT_LOSS_PROB * (1.0 - norm(loss_prob))
        + config.SCORE_WEIGHT_EXPECTED_RETURN * norm(expected_return)
    )

    if event_score_factors is not None:
        score = score * event_score_factors

    return score


def _compute_expected_return(
    pnl_mid: "xp.ndarray",
    spot_range: "xp.ndarray",
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
    net_debits: "xp.ndarray",
) -> "xp.ndarray":
    """Espérance du P&L en % du capital, pondérée par la distribution log-normale."""
    T = days_to_close / 365.0
    if T <= 0:
        return xp.zeros(pnl_mid.shape[0], dtype=xp.float32)

    mu = xp.log(xp.asarray(current_spot, dtype=xp.float32)) + (
        risk_free_rate - 0.5 * atm_vol ** 2
    ) * T
    sigma = atm_vol * T ** 0.5

    log_s = xp.log(spot_range.astype(xp.float32))
    pdf = (
        xp.exp(-0.5 * ((log_s - mu) / sigma) ** 2)
        / (spot_range.astype(xp.float32) * sigma * (2 * 3.141592653589793) ** 0.5)
    )

    # P&L en % du capital : (C, M)
    pnl_pct = pnl_mid / net_debits[:, None] * 100.0

    dx = xp.diff(spot_range.astype(xp.float32))
    integrand = pnl_pct * pdf[None, :]
    expected = 0.5 * (
        (integrand[:, :-1] + integrand[:, 1:]) * dx[None, :]
    ).sum(axis=1)

    return expected
