"""Calcul P&L batch sur GPU/CPU pour toutes les combinaisons simultanément."""

import numpy as np

import config
from engine.backend import xp, to_xp
from engine.black_scholes import bs_price, intrinsic_value


def compute_batch_size(num_spots: int, num_vol_scenarios: int, num_legs: int = 4) -> int:
    """Calcule le nombre max de combinaisons par batch selon la mémoire GPU disponible."""
    bytes_per_combo = (
        num_spots * num_vol_scenarios * num_legs
        * config.BYTES_PER_COMBO_PER_SPOT
        * config.GPU_SAFETY_FACTOR
    )
    return int(config.MAX_GPU_MEMORY_BYTES / bytes_per_combo)


def combinations_to_tensor(combinations: list) -> dict:
    """
    Convertit une liste de Combination en dict de tenseurs xp prêts pour le GPU.

    Retourne un dict avec les clés :
      option_types, directions, quantities, strikes,
      entry_prices, implied_vols, time_to_expiry_at_close
    Chaque tenseur a shape (C, L) où L = nombre max de legs (padding zéro).
    """
    C = len(combinations)
    L = max(len(c.legs) for c in combinations)  # nb max de legs (variable selon templates)

    option_types = np.zeros((C, L), dtype=np.int8)
    directions = np.zeros((C, L), dtype=np.int8)
    quantities = np.zeros((C, L), dtype=np.int16)
    strikes = np.zeros((C, L), dtype=np.float32)
    entry_prices = np.zeros((C, L), dtype=np.float32)
    implied_vols = np.zeros((C, L), dtype=np.float32)
    tte_at_close = np.zeros((C, L), dtype=np.float32)

    for i, combo in enumerate(combinations):
        for j, leg in enumerate(combo.legs):
            option_types[i, j] = 0 if leg.option_type == "call" else 1
            directions[i, j] = leg.direction
            quantities[i, j] = leg.quantity
            strikes[i, j] = leg.strike
            entry_prices[i, j] = leg.entry_price
            implied_vols[i, j] = leg.implied_vol
            tte_days = max(0, (leg.expiration - combo.close_date).days)
            tte_at_close[i, j] = tte_days / 365.0

    return {
        "option_types": to_xp(option_types),
        "directions": to_xp(directions),
        "quantities": to_xp(quantities),
        "strikes": to_xp(strikes),
        "entry_prices": to_xp(entry_prices),
        "implied_vols": to_xp(implied_vols),
        "time_to_expiry_at_close": to_xp(tte_at_close),
    }


def compute_pnl_batch(
    combinations_tensor: dict,
    spot_range: "xp.ndarray",    # shape (M,)
    vol_scenarios: list[float],   # ex: [0.8, 1.0, 1.2]
    risk_free_rate: float,
) -> "xp.ndarray":
    """
    Calcule le P&L de toutes les combinaisons sur une grille de spots.

    Retourne un tenseur shape (V, C, M) en unités monétaires (dollars).
    V = nb scénarios vol, C = nb combinaisons, M = nb points spot.

    Le traitement est fait par batches si nécessaire pour respecter
    la contrainte mémoire GPU.
    """
    C = combinations_tensor["option_types"].shape[0]
    M = spot_range.shape[0]
    V = len(vol_scenarios)

    batch_size = compute_batch_size(M, V)
    if batch_size <= 0:
        batch_size = 1000  # fallback minimal

    result = xp.zeros((V, C, M), dtype=xp.float32)

    for batch_start in range(0, C, batch_size):
        batch_end = min(batch_start + batch_size, C)
        batch = {k: v[batch_start:batch_end] for k, v in combinations_tensor.items()}
        result[:, batch_start:batch_end, :] = _compute_pnl_batch_chunk(
            batch, spot_range, vol_scenarios, risk_free_rate
        )

    return result


def _compute_pnl_batch_chunk(
    batch: dict,
    spot_range: "xp.ndarray",   # (M,)
    vol_scenarios: list[float],
    rate: float,
) -> "xp.ndarray":
    """
    Calcule le P&L pour un sous-ensemble de combinaisons.

    Retourne shape (V, C_chunk, M).
    """
    C = batch["option_types"].shape[0]
    M = spot_range.shape[0]
    V = len(vol_scenarios)

    # Broadcast dimensions : spot (M,1,1), params (1,C,4)
    # → opérations shape (M, C, 4)
    spot_2d = spot_range[:, None, None]               # (M, 1, 1)
    opt_types = batch["option_types"][None, :, :]     # (1, C, 4)
    dirs = batch["directions"][None, :, :].astype(xp.float32)
    qtys = batch["quantities"][None, :, :].astype(xp.float32)
    strikes = batch["strikes"][None, :, :]            # (1, C, 4)
    entry_prices = batch["entry_prices"][None, :, :]  # (1, C, 4)
    implied_vols = batch["implied_vols"][None, :, :]  # (1, C, 4)
    tte = batch["time_to_expiry_at_close"][None, :, :]  # (1, C, 4)

    expired_mask = (tte <= 0.0)   # legs expirés à close_date

    pnl_out = xp.zeros((V, C, M), dtype=xp.float32)

    for v_idx, vol_factor in enumerate(vol_scenarios):
        adjusted_vol = implied_vols * vol_factor  # (1, C, 4)

        # Valeur BS pour legs vivants (tte > 0)
        # Eviter division par zéro : clip tte à un minimum
        safe_tte = xp.where(expired_mask, xp.ones_like(tte) * 1e-6, tte)
        safe_vol = xp.where(adjusted_vol <= 0, xp.ones_like(adjusted_vol) * 1e-6, adjusted_vol)

        bs_val = bs_price(opt_types, spot_2d, strikes, safe_tte, safe_vol, rate)  # (M, C, 4)
        intr_val = intrinsic_value(opt_types, spot_2d, strikes)                    # (M, C, 4)

        # Sélectionner valeur selon expiration
        value = xp.where(expired_mask, intr_val, bs_val)   # (M, C, 4)

        # P&L par leg : direction × qty × (value - entry_price) × 100
        pnl_legs = dirs * qtys * (value - entry_prices) * 100.0   # (M, C, 4)

        # Sommer les 4 legs → shape (M, C)
        pnl_combo = pnl_legs.sum(axis=2)  # (M, C)

        # Transposer en (C, M) puis stocker dans (V, C, M)
        pnl_out[v_idx] = pnl_combo.T      # (C, M)

    return pnl_out
