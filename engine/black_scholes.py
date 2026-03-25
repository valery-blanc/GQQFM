"""Black-Scholes vectorisé via le backend GPU/CPU."""

from engine.backend import xp, ndtr


def bs_price(
    option_type: "xp.ndarray",    # 0 = call, 1 = put
    spot: "xp.ndarray",
    strike: "xp.ndarray",
    time_to_expiry: "xp.ndarray", # en années, > 0
    vol: "xp.ndarray",
    rate: float,
) -> "xp.ndarray":
    """
    Calcul Black-Scholes vectorisé.

    Supporte le broadcast spot × combinaisons :
      spot:             shape (M, 1)  → grille de prix simulés
      strike, tte, vol: shape (1, N)  → paramètres des legs
      résultat:         shape (M, N)

    Précondition : time_to_expiry > 0 pour tous les éléments.
    Les legs expirés (tte == 0) doivent être traités par intrinsic_value
    avant d'appeler cette fonction.
    """
    sqrt_t = xp.sqrt(time_to_expiry)
    d1 = (xp.log(spot / strike) + (rate + 0.5 * vol ** 2) * time_to_expiry) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    discount = xp.exp(-rate * time_to_expiry)
    call_price = spot * ndtr(d1) - strike * discount * ndtr(d2)
    put_price = strike * discount * ndtr(-d2) - spot * ndtr(-d1)

    is_call = (option_type == 0)
    return xp.where(is_call, call_price, put_price)


def intrinsic_value(
    option_type: "xp.ndarray",  # 0 = call, 1 = put
    spot: "xp.ndarray",
    strike: "xp.ndarray",
) -> "xp.ndarray":
    """Valeur intrinsèque pour une option expirée (tte == 0)."""
    call_val = xp.maximum(spot - strike, 0.0)
    put_val = xp.maximum(strike - spot, 0.0)
    is_call = (option_type == 0)
    return xp.where(is_call, call_val, put_val)
