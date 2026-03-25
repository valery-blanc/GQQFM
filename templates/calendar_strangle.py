"""Template 1 : Calendar Strangle."""

from data.models import Leg
from templates.base import LegSpec, TemplateDefinition


def _constraints(legs: list[Leg]) -> bool:
    """
    Contraintes inter-legs pour le Calendar Strangle :
    - strike(put_far) < strike(put_near) < spot < strike(call_near) < strike(call_far)
    - expiry(short) < expiry(long)
    - qty(short_call) <= qty(long_call)
    - qty(short_put) <= qty(long_put)
    """
    long_call, long_put, short_call, short_put = legs

    # Ordre des strikes
    if not (long_put.strike < short_put.strike):
        return False
    if not (short_call.strike < long_call.strike):
        return False

    # Expirations
    if not (short_call.expiration < long_call.expiration):
        return False
    if short_call.expiration != short_put.expiration:
        return False
    if long_call.expiration != long_put.expiration:
        return False

    # Quantités : on ne vend pas plus qu'on n'achète
    if short_call.quantity > long_call.quantity:
        return False
    if short_put.quantity > long_put.quantity:
        return False

    return True


CALENDAR_STRANGLE = TemplateDefinition(
    name="calendar_strangle",
    description="Achat de strangle long terme + vente de strangle court terme",
    legs_spec=[
        LegSpec(
            option_type="call",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(1.01, 1.10),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="put",
            direction=+1,
            quantity_range=range(1, 6),
            strike_range=(0.90, 0.99),
            strike_step=0.005,
            expiry_selector="FAR",
        ),
        LegSpec(
            option_type="call",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(1.005, 1.05),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
        LegSpec(
            option_type="put",
            direction=-1,
            quantity_range=range(1, 6),
            strike_range=(0.95, 0.995),
            strike_step=0.005,
            expiry_selector="NEAR",
        ),
    ],
    constraints=[_constraints],
)
