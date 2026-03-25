"""Calcul de la probabilité de perte via distribution log-normale."""

from engine.backend import xp, ndtr


def compute_loss_probability(
    pnl_curve: "xp.ndarray",   # shape (C, M)
    spot_range: "xp.ndarray",  # shape (M,)
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
) -> "xp.ndarray":
    """
    Calcule la probabilité de perte pour chaque combinaison.

    La distribution log-normale est calibrée sur atm_vol.

    Retourne shape (C,) — probabilité de perte en [0.0, 1.0].
    """
    T = days_to_close / 365.0
    if T <= 0:
        # Pas de temps → probabilité basée sur le spot courant uniquement
        return xp.zeros(pnl_curve.shape[0], dtype=xp.float32)

    mu = xp.log(xp.asarray(current_spot, dtype=xp.float32)) + (
        risk_free_rate - 0.5 * atm_vol ** 2
    ) * T
    sigma = atm_vol * T ** 0.5

    # Densité log-normale sur spot_range : shape (M,)
    log_s = xp.log(spot_range.astype(xp.float32))
    pdf = (
        xp.exp(-0.5 * ((log_s - mu) / sigma) ** 2)
        / (spot_range.astype(xp.float32) * sigma * (2 * 3.141592653589793) ** 0.5)
    )

    # Masque de perte : shape (C, M)
    loss_mask = (pnl_curve < 0).astype(xp.float32)

    # Intégration numérique par trapèzes : shape (C,)
    dx = xp.diff(spot_range.astype(xp.float32))   # (M-1,)
    integrand = loss_mask * pdf[None, :]            # (C, M)

    # Trapèzes : 0.5 * (f[i] + f[i+1]) * dx
    loss_prob = 0.5 * (
        (integrand[:, :-1] + integrand[:, 1:]) * dx[None, :]
    ).sum(axis=1)

    return xp.clip(loss_prob, 0.0, 1.0)
