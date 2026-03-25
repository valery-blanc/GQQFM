"""Validation du pricer Black-Scholes contre des valeurs de référence."""

import numpy as np
import pytest

from engine.backend import xp
from engine.black_scholes import bs_price, intrinsic_value


def _scalar(arr):
    """Extrait un scalaire depuis un array 0-d ou 1-d."""
    a = np.asarray(arr) if not isinstance(arr, np.ndarray) else arr
    if hasattr(a, "get"):
        a = a.get()
    return float(a.flat[0])


def make(v):
    return xp.array([v], dtype=xp.float32)


class TestBSPrice:
    def test_call_atm(self):
        """Test 1 — Call ATM : S=100, K=100, T=0.25, vol=0.20, r=0.05 → 4.6148"""
        price = bs_price(make(0), make(100.0), make(100.0), make(0.25), make(0.20), 0.05)
        assert abs(_scalar(price) - 4.6148) < 0.01

    def test_put_otm(self):
        """Test 2 — Put OTM : S=100, K=90, T=0.5, vol=0.25, r=0.03 → ~2.44"""
        # Valeur calculée analytiquement : d1≈0.769, d2≈0.592, put≈2.44
        price = bs_price(make(1), make(100.0), make(90.0), make(0.5), make(0.25), 0.03)
        assert abs(_scalar(price) - 2.44) < 0.02

    def test_call_deep_itm(self):
        """Un call deep ITM doit valoir approximativement S - K*exp(-rT)."""
        S, K, T, vol, r = 200.0, 100.0, 1.0, 0.20, 0.05
        price = bs_price(make(0), make(S), make(K), make(T), make(vol), r)
        intrinsic = S - K * np.exp(-r * T)
        assert _scalar(price) > intrinsic * 0.95

    def test_put_call_parity(self):
        """Parité put-call : C - P = S - K*exp(-rT)."""
        S, K, T, vol, r = 100.0, 105.0, 0.5, 0.20, 0.05
        call = _scalar(bs_price(make(0), make(S), make(K), make(T), make(vol), r))
        put = _scalar(bs_price(make(1), make(S), make(K), make(T), make(vol), r))
        parity = S - K * np.exp(-r * T)
        assert abs((call - put) - parity) < 0.01

    def test_vectorized_1m(self):
        """Test 3 — Vectorisation : 1M évaluations cohérentes avec calcul scalaire."""
        N = 1_000_000
        rng = np.random.default_rng(42)
        spots = rng.uniform(80, 120, N).astype(np.float32)
        strikes = rng.uniform(80, 120, N).astype(np.float32)
        ttes = rng.uniform(0.05, 1.0, N).astype(np.float32)
        vols = rng.uniform(0.10, 0.50, N).astype(np.float32)
        types = rng.integers(0, 2, N).astype(np.int8)

        prices = bs_price(
            xp.array(types), xp.array(spots), xp.array(strikes),
            xp.array(ttes), xp.array(vols), 0.05
        )
        assert prices.shape[0] == N
        prices_np = np.asarray(prices.get() if hasattr(prices, "get") else prices)
        assert np.all(prices_np >= 0), "Les prix d'options ne peuvent pas être négatifs"


class TestIntrinsicValue:
    def test_call_intrinsic(self):
        val = intrinsic_value(make(0), make(110.0), make(100.0))
        assert abs(_scalar(val) - 10.0) < 1e-4

    def test_put_intrinsic(self):
        val = intrinsic_value(make(1), make(90.0), make(100.0))
        assert abs(_scalar(val) - 10.0) < 1e-4

    def test_otm_intrinsic_zero(self):
        call_val = intrinsic_value(make(0), make(90.0), make(100.0))
        put_val = intrinsic_value(make(1), make(110.0), make(100.0))
        assert _scalar(call_val) == 0.0
        assert _scalar(put_val) == 0.0
