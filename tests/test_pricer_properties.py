"""Property-based tests for the BS European and BS-1993 American pricers, and PnL attribution.

Properties covered:
- Bounds:           0 <= C <= S; 0 <= P <= K e^(-rT)
- Lower bounds:     C >= max(S - K e^(-rT), 0); P >= max(K e^(-rT) - S, 0)
- Put-call parity:  C - P = S - K e^(-rT) (European, no q)
- Monotonicity:     dC/dS >= 0, dP/dS <= 0, dC/dvol >= 0, dP/dvol >= 0
- American:         price >= intrinsic; American put >= European put;
                    American call (q=0) = European call;
                    call non-increasing in q, put non-decreasing in q
- PnL attribution:  inversion symmetry, leg linearity, expiry intrinsic payoff

Note: the model uses a continuous dividend yield, so there is no discrete ex-div
discontinuity to test. Boundary behaviour at q -> 0 is asserted via the
TestDividendBoundary class instead.
"""

import math
from datetime import date

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from data.models import Combination, Leg
from engine.backend import xp
from engine.black_scholes import bs_american_price, bs_price
from engine.pnl import combinations_to_tensor, compute_pnl_batch


# ── Tolerances ───────────────────────────────────────────────────────────────
# Pricer is float32 internally; combined absolute + relative tolerance.
ABS_TOL = 0.02      # 2 cents
REL_TOL = 0.005     # 0.5%


def _close(a: float, b: float, abs_tol: float = ABS_TOL, rel_tol: float = REL_TOL) -> bool:
    return abs(a - b) <= abs_tol + rel_tol * max(abs(a), abs(b))


# Hypothesis profile: keep total wall time reasonable (~50 examples per test).
PROP = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _scalar(arr) -> float:
    a = np.asarray(arr) if not isinstance(arr, np.ndarray) else arr
    if hasattr(a, "get"):
        a = a.get()
    return float(a.flat[0])


def _make(v, dtype=xp.float32):
    return xp.array([v], dtype=dtype)


def _euro_price(opt_type: int, S, K, T, vol, r) -> float:
    return _scalar(bs_price(
        _make(opt_type, xp.int8), _make(S), _make(K), _make(T), _make(vol), r
    ))


def _amer_price(opt_type: int, S, K, T, vol, r, q) -> float:
    return _scalar(bs_american_price(
        _make(opt_type, xp.int8), _make(S), _make(K), _make(T),
        _make(vol), r, _make(q),
    ))


# ── Hypothesis strategies — realistic option parameter ranges ────────────────

# Restricted to the regime where BS-1993 + float32 is numerically stable.
# - vol >= 0.10: avoids S**beta overflow when beta blows up at very low vol.
# - rate >= 0.005: avoids the BS-1993 r->0 degeneracy (handled separately by
#   explicit fallback tests in TestZeroRateFallback below).
spot_strat = st.floats(min_value=50.0, max_value=500.0, allow_nan=False)
strike_strat = st.floats(min_value=50.0, max_value=500.0, allow_nan=False)
T_strat = st.floats(min_value=0.02, max_value=2.0, allow_nan=False)   # ~1 week .. 2 yrs
vol_strat = st.floats(min_value=0.10, max_value=0.80, allow_nan=False)
rate_strat = st.floats(min_value=0.005, max_value=0.08, allow_nan=False)
divy_strat = st.floats(min_value=0.0, max_value=0.08, allow_nan=False)


# ════════════════════════════════════════════════════════════════════════════
# European pricer properties
# ════════════════════════════════════════════════════════════════════════════

class TestEuropeanProperties:
    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_non_negative(self, S, K, T, vol, r):
        assert _euro_price(0, S, K, T, vol, r) >= -ABS_TOL

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_non_negative(self, S, K, T, vol, r):
        assert _euro_price(1, S, K, T, vol, r) >= -ABS_TOL

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_upper_bound(self, S, K, T, vol, r):
        """C <= S (no dividends in European pricer)."""
        c = _euro_price(0, S, K, T, vol, r)
        assert c <= S + ABS_TOL, f"C={c} > S={S}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_upper_bound(self, S, K, T, vol, r):
        """P <= K * exp(-rT)."""
        p = _euro_price(1, S, K, T, vol, r)
        ub = K * math.exp(-r * T)
        assert p <= ub + ABS_TOL, f"P={p} > K*e^(-rT)={ub}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_lower_bound(self, S, K, T, vol, r):
        """C >= max(S - K*exp(-rT), 0)."""
        c = _euro_price(0, S, K, T, vol, r)
        lb = max(S - K * math.exp(-r * T), 0.0)
        assert c >= lb - ABS_TOL, f"C={c} < max(S-Ke^(-rT),0)={lb}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_lower_bound(self, S, K, T, vol, r):
        """P >= max(K*exp(-rT) - S, 0)."""
        p = _euro_price(1, S, K, T, vol, r)
        lb = max(K * math.exp(-r * T) - S, 0.0)
        assert p >= lb - ABS_TOL, f"P={p} < max(Ke^(-rT)-S,0)={lb}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_call_parity(self, S, K, T, vol, r):
        """C - P = S - K * exp(-rT)."""
        c = _euro_price(0, S, K, T, vol, r)
        p = _euro_price(1, S, K, T, vol, r)
        parity = S - K * math.exp(-r * T)
        assert _close(c - p, parity), (
            f"parity violated: C-P={c - p:.4f} vs S-Ke^(-rT)={parity:.4f}"
            f" (S={S}, K={K}, T={T}, vol={vol}, r={r})"
        )

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_monotone_in_spot(self, S, K, T, vol, r):
        """Call price non-decreasing in spot."""
        c_lo = _euro_price(0, S,        K, T, vol, r)
        c_hi = _euro_price(0, S * 1.05, K, T, vol, r)
        assert c_hi >= c_lo - ABS_TOL, f"call not monotone: {c_lo} -> {c_hi}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_monotone_in_spot(self, S, K, T, vol, r):
        """Put price non-increasing in spot."""
        p_lo = _euro_price(1, S,        K, T, vol, r)
        p_hi = _euro_price(1, S * 1.05, K, T, vol, r)
        assert p_hi <= p_lo + ABS_TOL, f"put not monotone: {p_lo} -> {p_hi}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_monotone_in_vol(self, S, K, T, vol, r):
        """Call vega >= 0."""
        v_lo = _euro_price(0, S, K, T, vol,        r)
        v_hi = _euro_price(0, S, K, T, vol * 1.10, r)
        assert v_hi >= v_lo - ABS_TOL

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_monotone_in_vol(self, S, K, T, vol, r):
        """Put vega >= 0."""
        v_lo = _euro_price(1, S, K, T, vol,        r)
        v_hi = _euro_price(1, S, K, T, vol * 1.10, r)
        assert v_hi >= v_lo - ABS_TOL


# ════════════════════════════════════════════════════════════════════════════
# American pricer (BS-1993) properties
# ════════════════════════════════════════════════════════════════════════════

class TestAmericanProperties:
    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat,
           r=rate_strat, q=divy_strat)
    def test_non_negative(self, S, K, T, vol, r, q):
        for ot in (0, 1):
            assert _amer_price(ot, S, K, T, vol, r, q) >= -ABS_TOL

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat,
           r=rate_strat, q=divy_strat)
    def test_finite(self, S, K, T, vol, r, q):
        for ot in (0, 1):
            assert math.isfinite(_amer_price(ot, S, K, T, vol, r, q))

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat,
           r=rate_strat, q=divy_strat)
    def test_geq_intrinsic(self, S, K, T, vol, r, q):
        for ot in (0, 1):
            v = _amer_price(ot, S, K, T, vol, r, q)
            intr = max(S - K, 0.0) if ot == 0 else max(K - S, 0.0)
            assert v >= intr - ABS_TOL, f"ot={ot} v={v} intrinsic={intr}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_no_div_equals_european(self, S, K, T, vol, r):
        """Without dividends, an American call is never exercised early -> = European."""
        amer = _amer_price(0, S, K, T, vol, r, 0.0)
        euro = _euro_price(0, S, K, T, vol, r)
        assert _close(amer, euro), f"amer={amer:.4f} vs euro={euro:.4f}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_american_put_geq_european(self, S, K, T, vol, r):
        """American put >= European put (early-exercise premium, q=0)."""
        amer = _amer_price(1, S, K, T, vol, r, 0.0)
        euro = _euro_price(1, S, K, T, vol, r)
        assert amer >= euro - ABS_TOL, f"amer_put={amer} < euro_put={euro}"


# ════════════════════════════════════════════════════════════════════════════
# Dividend boundary behaviour (proxy for ex-div sensitivity)
# ════════════════════════════════════════════════════════════════════════════

class TestDividendBoundary:
    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_q0_branch_continuous(self, S, K, T, vol, r):
        """Below the q > 1e-6 threshold the pricer falls back to the European
        path; values at q=0 and q=1e-7 must therefore be identical."""
        v0 = _amer_price(0, S, K, T, vol, r, 0.0)
        v1 = _amer_price(0, S, K, T, vol, r, 1e-7)
        assert _close(v0, v1), f"q=0->{v0:.4f}, q=1e-7->{v1:.4f}"

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_call_decreases_in_dividend(self, S, K, T, vol, r):
        """Higher continuous dividend yield -> lower American call value."""
        v_lo = _amer_price(0, S, K, T, vol, r, 0.01)
        v_hi = _amer_price(0, S, K, T, vol, r, 0.06)
        assert v_hi <= v_lo + ABS_TOL, (
            f"call rose with q: q=0.01->{v_lo:.4f}, q=0.06->{v_hi:.4f}"
            f" (S={S}, K={K}, T={T}, vol={vol}, r={r})"
        )

    @PROP
    @given(S=spot_strat, K=strike_strat, T=T_strat, vol=vol_strat, r=rate_strat)
    def test_put_increases_in_dividend(self, S, K, T, vol, r):
        """Higher continuous dividend yield -> higher American put value."""
        v_lo = _amer_price(1, S, K, T, vol, r, 0.01)
        v_hi = _amer_price(1, S, K, T, vol, r, 0.06)
        assert v_hi >= v_lo - ABS_TOL, (
            f"put fell with q: q=0.01->{v_lo:.4f}, q=0.06->{v_hi:.4f}"
        )


# ════════════════════════════════════════════════════════════════════════════
# PnL attribution consistency
# ════════════════════════════════════════════════════════════════════════════

CLOSE_DATE = date(2025, 1, 17)
FAR_DATE   = date(2025, 3, 21)


def _leg(opt_type: str, direction: int, qty: int, K: float, exp: date,
         entry: float, vol: float, q: float = 0.0) -> Leg:
    return Leg(opt_type, direction, qty, K, exp, entry, vol, div_yield=q)


def _single_leg_combo(leg: Leg, close_date: date = CLOSE_DATE) -> Combination:
    debit = leg.direction * leg.quantity * leg.entry_price * 100.0
    return Combination(legs=[leg], net_debit=debit,
                       close_date=close_date, template_name="test")


def _eval_pnl(combo: Combination, spots: list[float], r: float = 0.045) -> np.ndarray:
    spot_range = xp.array(spots, dtype=xp.float32)
    tensor = combinations_to_tensor([combo])
    pnl = compute_pnl_batch(tensor, spot_range, [1.0], r, use_american_pricer=True)
    arr = pnl[0, 0]
    return np.asarray(arr.get() if hasattr(arr, "get") else arr)


class TestPnLAttribution:
    def test_pnl_inversion_symmetry(self):
        """PnL of a sign-flipped combo equals the negative of the original."""
        leg_long  = _leg("call", +1, 1, 100.0, FAR_DATE, 5.0, 0.25)
        leg_short = _leg("call", -1, 1, 100.0, FAR_DATE, 5.0, 0.25)
        spots = [80.0, 100.0, 120.0]
        long_pnl  = _eval_pnl(_single_leg_combo(leg_long), spots)
        short_pnl = _eval_pnl(_single_leg_combo(leg_short), spots)
        assert np.allclose(long_pnl, -short_pnl, atol=0.5), (
            f"long {long_pnl}, short {short_pnl}"
        )

    def test_pnl_linearity_two_legs(self):
        """PnL of a 2-leg combo equals the sum of single-leg PnLs."""
        leg_a = _leg("call", +1, 1, 100.0, FAR_DATE, 5.0, 0.25)
        leg_b = _leg("put",  +1, 1, 100.0, FAR_DATE, 4.5, 0.25)
        combo_ab = Combination(
            legs=[leg_a, leg_b],
            net_debit=(5.0 + 4.5) * 100.0,
            close_date=CLOSE_DATE,
            template_name="test",
        )
        spots = [80.0, 100.0, 120.0]
        pnl_ab = _eval_pnl(combo_ab, spots)
        pnl_a = _eval_pnl(_single_leg_combo(leg_a), spots)
        pnl_b = _eval_pnl(_single_leg_combo(leg_b), spots)
        assert np.allclose(pnl_ab, pnl_a + pnl_b, atol=1.0), (
            f"combo {pnl_ab}, sum-of-legs {pnl_a + pnl_b}"
        )

    def test_pnl_at_expiry_intrinsic(self):
        """Leg expiring at close_date: PnL = (intrinsic - entry) * dir * qty * 100."""
        leg = _leg("call", +1, 1, 100.0, CLOSE_DATE, 3.0, 0.25)
        combo = _single_leg_combo(leg)
        spots = [90.0, 100.0, 115.0]
        pnl = _eval_pnl(combo, spots)
        for i, S in enumerate(spots):
            expected = (max(S - 100.0, 0.0) - 3.0) * 100.0
            assert abs(pnl[i] - expected) < 1.0, (
                f"S={S}: pnl={pnl[i]:.2f}, expected={expected:.2f}"
            )

    def test_pnl_short_at_expiry(self):
        """Short call expiring ITM: PnL = (entry - intrinsic) * qty * 100."""
        leg = _leg("call", -1, 2, 100.0, CLOSE_DATE, 3.0, 0.25)
        combo = _single_leg_combo(leg)
        spots = [95.0, 110.0]
        pnl = _eval_pnl(combo, spots)
        for i, S in enumerate(spots):
            expected = -1 * 2 * (max(S - 100.0, 0.0) - 3.0) * 100.0
            assert abs(pnl[i] - expected) < 1.0, (
                f"S={S}: pnl={pnl[i]:.2f}, expected={expected:.2f}"
            )


# ════════════════════════════════════════════════════════════════════════════
# Zero-rate regression tests (BUG-006)
# ════════════════════════════════════════════════════════════════════════════

class TestZeroRateFallback:
    """The BS-1993 put-call transformation degenerates at r=0; the fallback
    must produce the European put exactly."""

    @pytest.mark.parametrize("S,K,T,vol", [
        (50.0,  50.0,  1.0,  0.50),
        (100.0, 100.0, 0.25, 0.20),
        (100.0, 110.0, 0.50, 0.30),
        (90.0,  100.0, 0.10, 0.40),
    ])
    def test_put_at_r_zero_equals_european(self, S, K, T, vol):
        """At r = 0, American put has no early-exercise premium -> = European put."""
        amer = _amer_price(1, S, K, T, vol, r=0.0, q=0.0)
        euro = _euro_price(1, S, K, T, vol, r=0.0)
        assert _close(amer, euro, abs_tol=0.05), (
            f"r=0 fallback wrong: amer={amer:.4f} vs euro={euro:.4f}"
        )

    def test_put_at_r_zero_with_dividend(self):
        """r=0 with q>0: still no early-exercise premium for the put."""
        amer = _amer_price(1, 100.0, 100.0, 0.5, 0.25, r=0.0, q=0.03)
        euro = _euro_price(1, 100.0, 100.0, 0.5, 0.25, r=0.0)
        assert _close(amer, euro, abs_tol=0.05)

    def test_no_nan_at_extreme_low_vol(self):
        """Float32 overflow regime: must not produce NaN (isfinite guard)."""
        # vol=0.06, r=0.05, q=0.05, T=2 -- previously triggered S**beta overflow
        v = _amer_price(0, 50.0, 50.0, 2.0, 0.06, r=0.05, q=0.05)
        assert math.isfinite(v), f"NaN leaked through guard: v={v}"
        intr = max(50.0 - 50.0, 0.0)
        assert v >= intr - ABS_TOL
