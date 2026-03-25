"""Tests du calcul P&L batch — vérification de la forme et de cas connus."""

from datetime import date

import numpy as np
import pytest

import config
from data.models import Combination, Leg
from engine.backend import xp
from engine.pnl import combinations_to_tensor, compute_pnl_batch


def make_simple_combo(
    close_date=date(2024, 7, 19),
    far_date=date(2024, 8, 16),
    spot=100.0,
) -> Combination:
    """Combinaison Calendar Strangle simplifiée pour les tests.

    qty_long=2, qty_short=1 : comme l'exemple de référence (3 vs 2),
    ce ratio produit un profil smile car aux extrêmes les longs surpassent
    les shorts (2×intrinsic_long > 1×intrinsic_short).
    """
    return Combination(
        legs=[
            Leg("call", +1, 2, spot * 1.05, far_date, 2.0, 0.20),   # long call far  ×2
            Leg("put",  +1, 2, spot * 0.95, far_date, 2.0, 0.20),   # long put far   ×2
            Leg("call", -1, 1, spot * 1.02, close_date, 1.5, 0.18), # short call near ×1
            Leg("put",  -1, 1, spot * 0.98, close_date, 1.5, 0.18), # short put near  ×1
        ],
        net_debit=(2*2.0 + 2*2.0 - 1*1.5 - 1*1.5) * 100,  # = 500.0 $
        close_date=close_date,
        template_name="calendar_strangle",
    )


class TestCombinationsToTensor:
    def test_shape(self):
        combo = make_simple_combo()
        tensor = combinations_to_tensor([combo])
        for key in ("option_types", "directions", "quantities", "strikes",
                    "entry_prices", "implied_vols", "time_to_expiry_at_close"):
            assert tensor[key].shape == (1, 4), f"{key} shape incorrect"

    def test_tte_at_close(self):
        """Les legs short (near) ont TTE=0, les legs long (far) ont TTE>0."""
        combo = make_simple_combo()
        tensor = combinations_to_tensor([combo])
        tte = np.asarray(tensor["time_to_expiry_at_close"].get()
                         if hasattr(tensor["time_to_expiry_at_close"], "get")
                         else tensor["time_to_expiry_at_close"])
        # legs 0,1 = far (TTE > 0), legs 2,3 = near/close (TTE = 0)
        assert tte[0, 0] > 0, "Leg long far doit avoir TTE > 0"
        assert tte[0, 1] > 0, "Leg long far doit avoir TTE > 0"
        assert tte[0, 2] == 0.0, "Leg short near doit avoir TTE = 0"
        assert tte[0, 3] == 0.0, "Leg short near doit avoir TTE = 0"

    def test_net_debit_sign(self):
        """net_debit doit être positif (la position coûte de l'argent)."""
        combo = make_simple_combo()
        assert combo.net_debit > 0


class TestComputePnlBatch:
    def test_output_shape(self):
        combo = make_simple_combo()
        spot_range = xp.linspace(85.0, 115.0, 50, dtype=xp.float32)
        tensor = combinations_to_tensor([combo])
        pnl = compute_pnl_batch(tensor, spot_range, [0.8, 1.0, 1.2], 0.045)
        assert pnl.shape == (3, 1, 50)

    def test_multiple_combos(self):
        combos = [make_simple_combo() for _ in range(10)]
        spot_range = xp.linspace(85.0, 115.0, config.NUM_SPOT_POINTS, dtype=xp.float32)
        tensor = combinations_to_tensor(combos)
        pnl = compute_pnl_batch(tensor, spot_range, [0.8, 1.0, 1.2], 0.045)
        assert pnl.shape == (3, 10, config.NUM_SPOT_POINTS)

    def test_pnl_is_finite(self):
        combo = make_simple_combo()
        spot_range = xp.linspace(85.0, 115.0, 50, dtype=xp.float32)
        tensor = combinations_to_tensor([combo])
        pnl = compute_pnl_batch(tensor, spot_range, [0.8, 1.0, 1.2], 0.045)
        pnl_np = np.asarray(pnl.get() if hasattr(pnl, "get") else pnl)
        assert np.all(np.isfinite(pnl_np)), "Le P&L ne doit pas contenir NaN ou inf"

    def test_smile_shape(self):
        """Un calendar strangle correctement calibré doit avoir une perte au centre
        et des gains aux extrêmes (profil smile/U)."""
        combo = make_simple_combo(spot=100.0)
        spot_range = xp.linspace(70.0, 130.0, 200, dtype=xp.float32)
        tensor = combinations_to_tensor([combo])
        pnl = compute_pnl_batch(tensor, spot_range, [0.8, 1.0, 1.2], 0.045)
        pnl_mid = np.asarray(
            pnl[1, 0].get() if hasattr(pnl[1, 0], "get") else pnl[1, 0]
        )
        center_idx = len(pnl_mid) // 2
        wing_max = max(pnl_mid[0], pnl_mid[-1])
        # Les extrêmes doivent avoir un P&L supérieur au centre
        assert wing_max > pnl_mid[center_idx], (
            "Profil smile attendu : gains aux extrêmes > centre"
        )
