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
    available_expirations: list[date],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
    event_calendar: EventCalendar,
    top_n: int = 3,
    today: date | None = None,
) -> list[tuple[date, date, float, list[str], str | None]]:
    """
    Sélectionne les meilleures paires d'expirations selon le profil événementiel.

    Les plages near_range et far_range sont des contraintes STRICTES (FEAT-011) :
    on ne sort jamais des bornes choisies par l'utilisateur dans la sidebar.

    Algorithme en 2 étapes :
      Étape 1 — Paires normales (near ∈ near_range, far ∈ far_range), CRITICAL exclus.
      Étape 2 — Dernier recours : paires (near ∈ near_range, far ∈ far_range)
                avec CRITICAL accepté + warning explicite.
      Sinon : retourne [] et le combinator utilisera _build_default_pairs.

    Retourne les top_n paires triées par event_score_factor décroissant.
    Chaque élément : (near_exp, far_exp, factor, sweet_names, warning).
    """
    from datetime import date as date_type
    if today is None:
        today = date_type.today()

    near_min, near_max = near_range
    far_min, far_max = far_range

    def days_out(exp: date) -> int:
        return (exp - today).days

    def classify(near: date, far: date):
        profile = event_calendar.classify_events_for_pair(near, far)
        sweet_names = [f"{ev.name} {ev.date.strftime('%d/%m')}" for ev in profile["sweet_zone"]]
        return profile["event_score_factor"], sweet_names, profile["has_critical_in_danger"]

    near_normal = [e for e in available_expirations if near_min <= days_out(e) <= near_max]
    far_normal = [e for e in available_expirations if far_min <= days_out(e) <= far_max]

    # ── Étape 1 : paires normales (CRITICAL exclus) ───────────────────────────
    step1_pairs = []
    for near in near_normal:
        for far in far_normal:
            if (far - near).days < 10:
                continue
            factor, sweet_names, has_critical = classify(near, far)
            if has_critical:
                continue
            step1_pairs.append((near, far, factor, sweet_names, None))

    if step1_pairs:
        step1_pairs.sort(key=lambda x: -x[2])
        return step1_pairs[:top_n]

    # ── Étape 2 : dernier recours (CRITICAL accepté avec warning) ─────────────
    last_resort = []
    for near in near_normal:
        for far in far_normal:
            if (far - near).days < 10:
                continue
            factor, sweet_names, has_critical = classify(near, far)
            if not has_critical:
                continue
            profile = event_calendar.classify_events_for_pair(near, far)
            critical_events = [ev for ev in profile["danger_zone"]
                               if hasattr(ev, "impact") and ev.impact.name == "CRITICAL"]
            if critical_events:
                ev = critical_events[0]
                ev_date = ev.date.strftime("%Y-%m-%d") if hasattr(ev.date, "strftime") else str(ev.date)
                warning = (
                    f"⚠ Événement {ev.name} le {ev_date} pendant la vie des legs courts. "
                    f"Risque de gap de prix. Vérifiez le profil P&L attentivement."
                )
            else:
                warning = "⚠ Événement CRITICAL pendant la vie des legs courts. Risque de gap de prix."
            last_resort.append((near, far, factor, sweet_names, warning))

    if last_resort:
        last_resort.sort(key=lambda x: -x[2])
        return last_resort[:top_n]

    return []


def _build_default_pairs(
    expirations: list[date],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
    today: date | None = None,
) -> list[tuple[date, date]]:
    """Construit les paires (near, far) respectant les plages DTE quand event_calendar est absent."""
    if today is None:
        today = date.today()
    near_min, near_max = near_range
    far_min, far_max = far_range
    near_candidates = [e for e in expirations if near_min <= (e - today).days <= near_max]
    far_candidates = [e for e in expirations if far_min <= (e - today).days <= far_max]
    pairs = [
        (n, f) for n in near_candidates for f in far_candidates if (f - n).days >= 10
    ]
    if pairs:
        return pairs
    # Fallback : on garde au moins une paire pour ne pas casser l'UI
    return [(expirations[0], expirations[-1])]


def generate_combinations(
    template: TemplateDefinition,
    chain: OptionsChain,
    as_of: date | None = None,
    event_calendar: EventCalendar | None = None,
    max_combinations: int = config.MAX_COMBINATIONS,
    min_volume: int = 0,
    max_net_debit: float = float("inf"),
    max_iterations: int = 2_000_000,
    near_expiry_range: tuple[int, int] | None = None,
    far_expiry_range: tuple[int, int] | None = None,
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

    near_range = near_expiry_range or config.SCANNER_NEAR_EXPIRY_RANGE
    far_range = far_expiry_range or config.SCANNER_FAR_EXPIRY_RANGE

    # ── Construire la liste des paires (near_exp, far_exp) avec facteurs ──────
    # pair_event_info : (near, far) → (factor, sweet_names, warning)
    pair_event_info: dict[tuple[date, date], tuple[float, list[str], str | None]] = {}

    today = as_of if as_of is not None else date.today()
    near_min, near_max = near_range
    far_min, far_max = far_range

    if template.use_adjacent_expiry_pairs:
        # Filtre near ∈ near_range, far ∈ far_range, far-near ≥ 10 j
        expiry_pairs = [
            (expirations[i], expirations[j])
            for i in range(len(expirations))
            for j in range(i + 1, len(expirations))
            if near_min <= (expirations[i] - today).days <= near_max
            and far_min <= (expirations[j] - today).days <= far_max
            and 10 <= (expirations[j] - expirations[i]).days <= 60
        ]
        if not expiry_pairs:
            expiry_pairs = [(expirations[0], expirations[-1])]

        if event_calendar is not None:
            for near, far in expiry_pairs:
                profile = event_calendar.classify_events_for_pair(near, far)
                sweet_names = [f"{ev.name} {ev.date.strftime('%d/%m')}" for ev in profile["sweet_zone"]]
                pair_event_info[(near, far)] = (profile["event_score_factor"], sweet_names, None)

    else:
        # use_adjacent_expiry_pairs=False (templates 1-3 : calendar strangle, etc.)
        if event_calendar is not None:
            selected = _select_event_pairs(
                expirations,
                near_range,
                far_range,
                event_calendar,
                today=today,
            )
            if selected:
                expiry_pairs = [(near, far) for near, far, _, _, _ in selected]
                pair_event_info = {
                    (near, far): (factor, sweet, warning)
                    for near, far, factor, sweet, warning in selected
                }
            else:
                expiry_pairs = _build_default_pairs(expirations, near_range, far_range, today=today)
        else:
            expiry_pairs = _build_default_pairs(expirations, near_range, far_range, today=today)

    all_combos: list[Combination] = []

    for near_exp, far_exp in expiry_pairs:
        if len(all_combos) >= max_combinations:
            break

        factor, sweet_names, event_warning = pair_event_info.get(
            (near_exp, far_exp), (1.0, [], None)
        )

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
                    div_yield=contract.div_yield,
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
                event_warning=event_warning,
            ))

            if len(all_combos) >= max_combinations:
                break

    return all_combos
