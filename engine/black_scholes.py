"""Black-Scholes européen + Bjerksund-Stensland 1993 américain, vectorisés GPU/CPU."""

from engine.backend import xp, ndtr


# ── Helpers Bjerksund-Stensland 1993 ─────────────────────────────────────────

def _bs93_phi(S, T, gamma, H, I, r, b, sigma):
    """
    Fonction auxiliaire φ pour l'approximation Bjerksund-Stensland 1993.

    Tous les arguments sont des tenseurs xp broadcast-compatibles.
    gamma peut être un scalaire Python (0.0, 1.0) ou un tenseur (beta).
    """
    sigma2 = sigma ** 2
    lambda_val = (-r + gamma * b + 0.5 * gamma * (gamma - 1.0) * sigma2) * T
    sqrt_T = xp.sqrt(T)
    d = -(xp.log(S / H) + (b + (gamma - 0.5) * sigma2) * T) / (sigma * sqrt_T)
    kappa = 2.0 * b / sigma2 + (2.0 * gamma - 1.0)
    log_I_S = xp.log(I / S)
    d2 = d - 2.0 * log_I_S / (sigma * sqrt_T)
    # Clamp pour éviter overflow dans S^gamma et (I/S)^kappa
    safe_exp = xp.exp(xp.clip(lambda_val, -50.0, 50.0))
    power_S = xp.where(xp.isfinite(S ** gamma), S ** gamma, xp.float32(0.0))
    ratio_pow = (I / S) ** kappa
    ratio_pow = xp.where(xp.isfinite(ratio_pow), ratio_pow, xp.float32(0.0))
    return safe_exp * power_S * (ndtr(d) - ratio_pow * ndtr(d2))


def _bs93_american_call(S, K, T, sigma, r, q):
    """
    Bjerksund-Stensland 1993 — approximation analytique pour call américain.

    Utilise une frontière d'exercice anticipé plate (single trigger price).
    Précision typique < 0.1% vs solutions numériques exactes.

    Arguments broadcast-compatibles (scalaires ou tenseurs xp).
    r et q peuvent être scalaires Python ou tenseurs.
    """
    b = r - q  # cost of carry

    sigma2 = sigma ** 2
    # Éviter division par zéro si sigma très petit
    safe_sigma2 = xp.maximum(sigma2, xp.float32(1e-12))

    beta = (0.5 - b / safe_sigma2) + xp.sqrt(
        (b / safe_sigma2 - 0.5) ** 2 + 2.0 * r / safe_sigma2
    )

    safe_beta_m1 = xp.maximum(beta - 1.0, xp.float32(1e-10))
    B_inf = (beta / safe_beta_m1) * K

    # B0 = max(K, r/q * K) quand q > 0 ; B0 = K sinon
    # On utilise xp.where pour éviter division par zéro
    q_safe = xp.maximum(xp.asarray(q, dtype=xp.float32), xp.float32(1e-10))
    rq_ratio = xp.asarray(r, dtype=xp.float32) / q_safe
    B0_candidate = rq_ratio * K
    B0 = xp.maximum(K, B0_candidate)

    # Frontière d'exercice anticipé I
    denom = xp.maximum(B_inf - B0, xp.float32(1e-10))
    ht = -(b * T + 2.0 * sigma * xp.sqrt(T)) * B0 / denom
    I = B0 + (B_inf - B0) * (1.0 - xp.exp(ht))

    alpha = (I - K) * I ** (-beta)

    # Formule B-S 1993 (6 appels à phi)
    val = (
        alpha * S ** beta
        - alpha * _bs93_phi(S, T, beta, I, I, r, b, sigma)
        + _bs93_phi(S, T, 1.0, I, I, r, b, sigma)
        - _bs93_phi(S, T, 1.0, K, I, r, b, sigma)
        - K * _bs93_phi(S, T, 0.0, I, I, r, b, sigma)
        + K * _bs93_phi(S, T, 0.0, K, I, r, b, sigma)
    )

    # Exercice immédiat si S >= I
    val = xp.where(S >= I, S - K, val)

    return val


def bs_american_price(
    option_type: "xp.ndarray",    # 0 = call, 1 = put
    spot: "xp.ndarray",
    strike: "xp.ndarray",
    time_to_expiry: "xp.ndarray",
    vol: "xp.ndarray",
    rate: float,
    div_yield: "xp.ndarray",     # rendement de dividende continu
) -> "xp.ndarray":
    """
    Prix d'option américaine via Bjerksund-Stensland 1993.

    - Calls sans dividende (q ≈ 0) : retourne le prix européen (exercice anticipé
      jamais optimal).
    - Calls avec dividende : approximation B-S 1993 directe.
    - Puts : transformation put-call P(S,K,T,r,q,σ) = C(K,S,T,q,r,σ).

    Le résultat est toujours ≥ prix européen (floor de sécurité).
    """
    is_call = (option_type == 0)

    # ── Calls américains ──
    call_am = _bs93_american_call(spot, strike, time_to_expiry, vol, rate, div_yield)
    # Si q ≈ 0 : call américain = call européen (pas d'exercice anticipé)
    euro_call = bs_price(
        xp.zeros_like(option_type), spot, strike, time_to_expiry, vol, rate
    )
    call_am = xp.where(div_yield > 1e-6, call_am, euro_call)

    # ── Puts américains via transformation put-call ──
    # P(S,K,T,r,q,σ) = C(K,S,T,q,r,σ) — on échange S↔K et r↔q
    put_am = _bs93_american_call(strike, spot, time_to_expiry, vol, div_yield, rate)

    american = xp.where(is_call, call_am, put_am)

    # Plancher à la valeur intrinsèque (une option américaine vaut toujours
    # au moins sa valeur d'exercice immédiat)
    intr = intrinsic_value(option_type, spot, strike)
    return xp.maximum(american, intr)


# ── Black-Scholes européen ───────────────────────────────────────────────────

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
