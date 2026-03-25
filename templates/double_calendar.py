"""Template 2 : Double Calendar Spread."""

from data.models import Leg
from templates.base import LegSpec, TemplateDefinition


def _constraints(legs: list[Leg]) -> bool:
    """
    Contraintes inter-legs pour le Double Calendar :
    - K_put < spot < K_call (implicite via les plages de strikes)
    - expiry(NEAR) < expiry(FAR)
    - même strike pour long et short d'un même type
    - qty(short) == qty(long) par type
    """
    long_call_far, short_call_near, long_put_far, short_put_near = legs

    # Même strikes par type
    if long_call_far.strike != short_call_near.strike:
        return False
    if long_put_far.strike != short_put_near.strike:
        return False

    # Expirations
    if not (short_call_near.expiration < long_call_far.expiration):
        return False
    if short_call_near.expiration != short_put_near.expiration:
        return False

    # Quantités identiques par type
    if long_call_far.quantity != short_call_near.quantity:
        return False
    if long_put_far.quantity != short_put_near.quantity:
        return False

    # K_put < K_call
    if long_put_far.strike >= long_call_far.strike:
        return False

    return True


DOUBLE_CALENDAR = TemplateDefinition(
    name="double_calendar",
    description="Calendar spread sur les calls + calendar spread sur les puts",
    legs_spec=[
        LegSpec(
            option_type="call",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(1.005, 1.15),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="call",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(1.005, 1.15),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
        LegSpec(
            option_type="put",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(0.85, 0.995),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="put",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(0.85, 0.995),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
    ],
    constraints=[_constraints],
)
