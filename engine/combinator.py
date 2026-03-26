"""Génération de combinaisons d'options par template."""

from __future__ import annotations

from datetime import date
from itertools import product
from typing import TYPE_CHECKING

from data.models import Combination, Leg, OptionsChain
from templates.base import TemplateDefinition

import config

if TYPE_CHECKING:
    from events.calendar import EventCalendar


def _select_event_pairs(
    expirations: list[date],
    chain: OptionsChain,
    event_calendar: EventCalendar,
    top_n: int = 3,
) -> list[tuple[date, date, float, list[str]]]:
    """
    Sélectionne les meilleures paires d'expirations selon le profil événementiel.

    Considère TOUTES les paires valides (far - near >= 10 jours) depuis
    les expirations disponibles dans la chaîne. Cela inclut toujours la paire
    (expirations[0], expirations[-1]) qui était utilisée avant FEAT-005,
    garantissant la rétro-compatibilité.

    Priorité : paires sans CRITICAL en danger zone. Si toutes ont CRITICAL,
    les inclut quand même (le facteur bas les pénalise au scoring sans les bloquer).

    Retourne les top_n paires triées par event_score_factor décroissant.
    Retourne une liste de (near_exp, far_exp, factor, sweet_names).
    """
    safe_pairs: list[tuple[date, date, float, list[str]]] = []
    critical_pairs: list[tuple[date, date, float, list[str]]] = []

    for i in range(len(expirations)):
        for j in range(i + 1, len(expirations)):
            near, far = expirations[i], expirations[j]
            if (far - near).days < 10:
                continue
            profile = event_calendar.classify_events_for_pair(near, far)
            sweet_names = [ev.name for ev in profile["sweet_zone"]]
            entry = (near, far, profile["event_score_factor"], sweet_names)
            if profile["has_critical_in_danger"]:
                critical_pairs.append(entry)
            else:
                safe_pairs.append(entry)

    if safe_pairs:
        safe_pairs.sort(key=lambda x: -x[2])
        return safe_pairs[:top_n]
    # Toutes les paires ont CRITICAL en danger zone — les inclure quand même
    # (facteur < 1.0 les pénalise au scoring, mais évite un résultat vide)
    critical_pairs.sort(key=lambda x: -x[2])
    return critical_pairs[:top_n]


def generate_combinations(
    template: TemplateDefinition,
    chain: OptionsChain,
    event_calendar: EventCalendar | None = None,
    max_combinations: int = config.MAX_COMBINATIONS,
    min_volume: int = 0,
    max_net_debit: float = float("inf"),
    max_iterations: int = 2_000_000,
) -> list[Combination]:
    """
    Génère toutes les combinaisons valides pour un template donné.

    Si event_calendar est fourni et use_adjacent_expiry_pairs=False :
      - Sélectionne les meilleures paires via _select_event_pairs (top 3).
      - Stocke event_score_factor et events_in_sweet_zone dans chaque Combination.
      - Fallback sur (expirations[0], expirations[-1]) si aucune paire éligible.

    Si event_calendar est fourni et use_adjacent_expiry_pairs=True :
      - Comportement adjacent existant + calcul de event_score_factor par paire.

    Si event_calendar est None :
      - Comportement identique à l'existant (rétro-compatible, factor=1.0).
    """
    expirations = sorted(chain.expirations)
    if len(expirations) < 2:
        return []

    spot = chain.underlying_price

    # ── Construire la liste des paires (near_exp, far_exp) avec facteurs ──────
    # pair_event_info : (near, far) → (factor, sweet_names)
    pair_event_info: dict[tuple[date, date], tuple[float, list[str]]] = {}

    if template.use_adjacent_expiry_pairs:
        expiry_pairs = [
            (expirations[i], expirations[j])
            for i in range(len(expirations))
            for j in range(i + 1, len(expirations))
            if 5 <= (expirations[j] - expirations[i]).days <= 45
        ]
        if not expiry_pairs:
            expiry_pairs = [(expirations[0], expirations[-1])]

        if event_calendar is not None:
            for near, far in expiry_pairs:
                profile = event_calendar.classify_events_for_pair(near, far)
                sweet_names = [ev.name for ev in profile["sweet_zone"]]
                pair_event_info[(near, far)] = (profile["event_score_factor"], sweet_names)

    else:
        # use_adjacent_expiry_pairs=False (templates 1-3 : calendar strangle, etc.)
        if event_calendar is not None:
            selected = _select_event_pairs(expirations, chain, event_calendar)
            if selected:
                expiry_pairs = [(near, far) for near, far, _, _ in selected]
                pair_event_info = {
                    (near, far): (factor, sweet)
                    for near, far, factor, sweet in selected
                }
            else:
                # Fallback : première et dernière expiration, factor neutre
                expiry_pairs = [(expirations[0], expirations[-1])]
        else:
            expiry_pairs = [(expirations[0], expirations[-1])]

    all_combos: list[Combination] = []

    for near_exp, far_exp in expiry_pairs:
        if len(all_combos) >= max_combinations:
            break

        factor, sweet_names = pair_event_info.get((near_exp, far_exp), (1.0, []))

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
                event_score_factor=factor,
                events_in_sweet_zone=sweet_names,
            ))

            if len(all_combos) >= max_combinations:
                break

    return all_combos
