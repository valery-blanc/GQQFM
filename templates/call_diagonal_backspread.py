"""
Template : Call Diagonal Backspread (2 legs)

Structure : Short N calls NEAR + Long N+1 calls FAR, FAR strike > NEAR strike.
Exemples réels : S3 C 255 MCD NEAR / L4 C 260 MCD FAR
                 S3 C 200 GOOG NEAR / L4 C 205 GOOG FAR

Profil P&L : gains si le sous-jacent monte fortement avant FAR expiry
             perte limitée si le sous-jacent reste stable ou baisse peu
"""

from data.models import Leg
from templates.base import LegSpec, TemplateDefinition


def _constraints(legs: list[Leg]) -> bool:
    short_call, long_call = legs

    # Strike diagonal : FAR strike > NEAR strike
    if not (short_call.strike < long_call.strike):
        return False

    # Backspread : plus de longs que de shorts
    if not (long_call.quantity > short_call.quantity):
        return False

    # Expirations : short NEAR < long FAR
    if not (short_call.expiration < long_call.expiration):
        return False

    return True


CALL_DIAGONAL_BACKSPREAD = TemplateDefinition(
    name="call_diagonal_backspread",
    description="Call Diagonal Backspread : vente N calls NEAR + achat N+1 calls FAR OTM",
    use_adjacent_expiry_pairs=True,
    legs_spec=[
        LegSpec(
            option_type="call",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(0.97, 1.05),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
        LegSpec(
            option_type="call",
            direction=+1,
            quantity_range=range(2, 7),
            strike_range=(1.00, 1.10),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
    ],
    constraints=[_constraints],
)
