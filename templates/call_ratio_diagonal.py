"""
Template : Call Ratio Diagonal (3 legs)

Structure : Short N calls NEAR + Long N calls FAR + Long 1 call FAR (strike plus élevé).
Exemples réels : S3 C 245 AAPL NEAR / L3 C 250 AAPL FAR / L1 C 255 AAPL FAR
                 S3 C 190 BA NEAR   / L3 C 195 BA FAR   / L1 C 200 BA FAR

Profil P&L : le leg L1 supplémentaire plafonne la perte en cas de fort mouvement haussier,
             gains max si le sous-jacent monte modérément vers FAR expiry.
"""

from data.models import Leg
from templates.base import LegSpec, TemplateDefinition


def _constraints(legs: list[Leg]) -> bool:
    short_call, long_call_main, long_call_otm = legs

    # Strikes croissants : NEAR < FAR_main < FAR_otm
    if not (short_call.strike < long_call_main.strike < long_call_otm.strike):
        return False

    # Les deux long legs sont sur FAR
    if long_call_main.expiration != long_call_otm.expiration:
        return False

    # Short est NEAR
    if not (short_call.expiration < long_call_main.expiration):
        return False

    # Même quantité pour short et long principal
    if short_call.quantity != long_call_main.quantity:
        return False

    # Le leg extra OTM est toujours quantité 1
    if long_call_otm.quantity != 1:
        return False

    return True


CALL_RATIO_DIAGONAL = TemplateDefinition(
    name="call_ratio_diagonal",
    description="Call Ratio Diagonal : vente N calls NEAR + achat N calls FAR + achat 1 call FAR OTM",
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
            quantity_range=range(1, 6),
            strike_range=(1.00, 1.08),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="call",
            direction=+1,
            quantity_range=range(1, 2),   # toujours 1
            strike_range=(1.02, 1.12),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
    ],
    constraints=[_constraints],
)
