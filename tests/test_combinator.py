"""Tests du combinator — vérification des contraintes inter-legs."""

from datetime import date, timedelta

import pytest

from data.models import OptionsChain, OptionContract
from engine.combinator import generate_combinations
from templates.calendar_strangle import CALENDAR_STRANGLE


def make_mock_chain(spot: float = 100.0) -> OptionsChain:
    """Crée une chaîne d'options fictive avec 2 expirations et plusieurs strikes."""
    today = date.today()
    near = today + timedelta(days=14)
    far = today + timedelta(days=45)

    contracts = []
    # Strikes autour du spot pour les calls
    for strike_factor, option_type in [
        (0.92, "put"), (0.94, "put"), (0.96, "put"), (0.97, "put"), (0.98, "put"),
        (1.02, "call"), (1.03, "call"), (1.04, "call"), (1.06, "call"), (1.08, "call"),
    ]:
        strike = round(spot * strike_factor, 2)
        for exp in [near, far]:
            contracts.append(OptionContract(
                contract_symbol=f"TEST{exp}{option_type[0].upper()}{int(strike)}",
                option_type=option_type,
                strike=strike,
                expiration=exp,
                bid=1.0,
                ask=1.2,
                mid=1.1,
                implied_vol=0.20,
                volume=100,
                open_interest=50,
            ))

    return OptionsChain(
        underlying_symbol="TEST",
        underlying_price=spot,
        contracts=contracts,
        expirations=sorted({near, far}),
        strikes=sorted({c.strike for c in contracts}),
        fetch_timestamp=__import__("datetime").datetime.now(),
    )


class TestCalendarStrangleConstraints:
    def setup_method(self):
        self.chain = make_mock_chain(spot=100.0)
        self.combos = generate_combinations(CALENDAR_STRANGLE, self.chain)

    def test_generates_combos(self):
        assert len(self.combos) > 0, "Doit générer au moins une combinaison"

    def test_exactly_four_legs(self):
        for combo in self.combos:
            assert len(combo.legs) == 4

    def test_strike_order(self):
        """put_far < put_near < spot < call_near < call_far"""
        spot = self.chain.underlying_price
        for combo in self.combos:
            long_call, long_put, short_call, short_put = combo.legs
            assert long_put.strike < short_put.strike, "put_far doit être < put_near"
            assert short_call.strike < long_call.strike, "call_near doit être < call_far"
            assert short_put.strike < spot, "put_near doit être < spot"
            assert short_call.strike > spot, "call_near doit être > spot"

    def test_expiry_order(self):
        """short legs expirent avant les long legs."""
        for combo in self.combos:
            long_call, long_put, short_call, short_put = combo.legs
            assert short_call.expiration < long_call.expiration
            assert short_put.expiration < long_put.expiration

    def test_quantity_constraint(self):
        """qty(short) <= qty(long) par type."""
        for combo in self.combos:
            long_call, long_put, short_call, short_put = combo.legs
            assert short_call.quantity <= long_call.quantity
            assert short_put.quantity <= long_put.quantity

    def test_net_debit_positive(self):
        """La position doit coûter de l'argent (débit net > 0)."""
        for combo in self.combos:
            assert combo.net_debit > 0

    def test_close_date_is_near_expiry(self):
        """close_date doit être l'expiration near (min des legs short)."""
        near_exp = min(self.chain.expirations)
        for combo in self.combos:
            assert combo.close_date == near_exp


class TestEmptyChain:
    def test_no_contracts(self):
        """Aucun contrat → aucune combinaison."""
        empty_chain = OptionsChain(
            underlying_symbol="EMPTY",
            underlying_price=100.0,
            contracts=[],
            expirations=[],
            strikes=[],
            fetch_timestamp=__import__("datetime").datetime.now(),
        )
        result = generate_combinations(CALENDAR_STRANGLE, empty_chain)
        assert result == []
