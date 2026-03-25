"""Génération de combinaisons d'options par template."""

from itertools import product

from data.models import Combination, Leg, OptionsChain
from templates.base import TemplateDefinition

import config


def generate_combinations(
    template: TemplateDefinition,
    chain: OptionsChain,
    max_combinations: int = config.MAX_COMBINATIONS,
    min_volume: int = 0,
    max_net_debit: float = float("inf"),
    max_iterations: int = 2_000_000,
) -> list[Combination]:
    """
    Génère toutes les combinaisons valides pour un template donné.

    Si use_adjacent_expiry_pairs=True, itère sur toutes les paires d'expirations
    séparées de 5 à 45 jours (utile pour les diagonales à expirations proches).
    Sinon, utilise expirations[0] (NEAR) et expirations[-1] (FAR).
    """
    expirations = sorted(chain.expirations)
    if len(expirations) < 2:
        return []

    spot = chain.underlying_price

    # Construire la liste des paires (near_exp, far_exp) à explorer
    if template.use_adjacent_expiry_pairs:
        expiry_pairs = [
            (expirations[i], expirations[j])
            for i in range(len(expirations))
            for j in range(i + 1, len(expirations))
            if 5 <= (expirations[j] - expirations[i]).days <= 45
        ]
        if not expiry_pairs:
            expiry_pairs = [(expirations[0], expirations[-1])]
    else:
        expiry_pairs = [(expirations[0], expirations[-1])]

    all_combos: list[Combination] = []

    for near_exp, far_exp in expiry_pairs:
        if len(all_combos) >= max_combinations:
            break

        # Candidats par leg_spec pour cette paire d'expirations
        leg_candidates: list[list[tuple]] = []
        valid_pair = True
        for spec in template.legs_spec:
            exp = near_exp if spec.expiry_selector == "NEAR" else far_exp
            min_strike = spot * spec.strike_range[0]
            max_strike = spot * spec.strike_range[1]

            contracts = [
                c for c in chain.contracts
                if c.option_type == spec.option_type
                and c.expiration == exp
                and min_strike <= c.strike <= max_strike
                and c.volume >= min_volume
            ]

            if not contracts:
                valid_pair = False
                break

            candidates = [
                (contract, qty)
                for contract in contracts
                for qty in spec.quantity_range
            ]
            leg_candidates.append(candidates)

        if not valid_pair:
            continue

        iterations = 0

        for leg_selections in product(*leg_candidates):
            iterations += 1
            if iterations > max_iterations:
                break

            legs = []
            for (contract, qty), spec in zip(leg_selections, template.legs_spec):
                legs.append(Leg(
                    option_type=contract.option_type,
                    direction=spec.direction,
                    quantity=qty,
                    strike=contract.strike,
                    expiration=contract.expiration,
                    entry_price=contract.mid,
                    implied_vol=contract.implied_vol,
                    contract_symbol=contract.contract_symbol,
                    volume=contract.volume,
                    open_interest=contract.open_interest,
                ))

            if not all(constraint(legs) for constraint in template.constraints):
                continue

            short_expirations = [leg.expiration for leg in legs if leg.direction == -1]
            if not short_expirations:
                continue
            close_date = min(short_expirations)

            net_debit = sum(
                leg.direction * leg.quantity * leg.entry_price * 100
                for leg in legs
            )

            if net_debit <= 0:
                continue

            if net_debit > max_net_debit:
                continue

            all_combos.append(Combination(
                legs=legs,
                net_debit=net_debit,
                close_date=close_date,
                template_name=template.name,
            ))

            if len(all_combos) >= max_combinations:
                break

    return all_combos
