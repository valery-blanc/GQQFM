"""Template 3 : Reverse Iron Condor Calendar."""

from data.models import Leg
from templates.base import LegSpec, TemplateDefinition


def _constraints(legs: list[Leg]) -> bool:
    """
    Contraintes : K4 < K2 < spot < K1 < K3
    - legs : [long_call_far, long_put_far, short_call_near, short_put_near]
    - K1 = long_call strike, K2 = long_put strike
    - K3 = short_call strike, K4 = short_put strike
    """
    long_call_far, long_put_far, short_call_near, short_put_near = legs

    # K4 < K2 : short put < long put
    if not (short_put_near.strike < long_put_far.strike):
        return False

    # K1 < K3 : long call < short call
    if not (long_call_far.strike < short_call_near.strike):
        return False

    # Expirations
    if not (short_call_near.expiration < long_call_far.expiration):
        return False
    if short_call_near.expiration != short_put_near.expiration:
        return False

    # Quantités : qty(short) <= qty(long) par type
    if short_call_near.quantity > long_call_far.quantity:
        return False
    if short_put_near.quantity > long_put_far.quantity:
        return False

    return True


REVERSE_IRON_CONDOR_CALENDAR = TemplateDefinition(
    name="reverse_iron_condor_calendar",
    description="Iron condor inversé avec expirations différentes",
    legs_spec=[
        LegSpec(
            option_type="call",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(1.005, 1.03),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="put",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(0.97, 0.995),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="call",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(1.01, 1.15),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
        LegSpec(
            option_type="put",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(0.85, 0.99),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
    ],
    constraints=[_constraints],
)
