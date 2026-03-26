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
) -> list[tuple[date, date, float, list[str], str | None]]:
    """
    Sélectionne les meilleures paires d'expirations selon le profil événementiel.

    Algorithme en 4 étapes :
      Étape 1 — Paires normales (near ∈ near_range, far ∈ far_range), CRITICAL exclus.
      Étape 2 — Extension near vers le bas [2, near_min-1], CRITICAL exclus, warning "near_expiry_short".
      Étape 3 — Extension far vers le haut [far_max+1, far_max+30], CRITICAL exclus.
      Étape 4 — Dernier recours : meilleure paire normale avec CRITICAL + warning explicite.

    Retourne les top_n paires triées par event_score_factor décroissant.
    Chaque élément : (near_exp, far_exp, factor, sweet_names, warning).
    """
    from datetime import date as date_type
    today = date_type.today()

    near_min, near_max = near_range
    far_min, far_max = far_range

    def days_out(exp: date) -> int:
        return (exp - today).days

    def classify(near: date, far: date):
        profile = event_calendar.classify_events_for_pair(near, far)
        sweet_names = [f"{ev.name} {ev.date.strftime('%d/%m')}" for ev in profile["sweet_zone"]]
        return profile["event_score_factor"], sweet_names, profile["has_critical_in_danger"]

    def build_pairs(near_candidates, far_candidates, warning_fn=None):
        """Construit la liste des paires valides sans CRITICAL en danger zone."""
        pairs = []
        for near in near_candidates:
            for far in far_candidates:
                if (far - near).days < 10:
                    continue
                factor, sweet_names, has_critical = classify(near, far)
                if has_critical:
                    continue
                warning = warning_fn(near) if warning_fn else None
                pairs.append((near, far, factor, sweet_names, warning))
        return pairs

    # Candidats far normaux
    far_normal = [e for e in available_expirations if far_min <= days_out(e) <= far_max]

    # ── Étape 1 : paires normales ─────────────────────────────────────────────
    near_normal = [e for e in available_expirations if near_min <= days_out(e) <= near_max]
    step1_pairs = build_pairs(near_normal, far_normal)
    if step1_pairs:
        step1_pairs.sort(key=lambda x: -x[2])
        return step1_pairs[:top_n]

    # ── Étape 2 : extension near vers le bas ──────────────────────────────────
    near_extended = [e for e in available_expirations if 2 <= days_out(e) < near_min]

    def near_short_warning(near: date) -> str:
        d = days_out(near)
        return f"Near expiry très court ({d}j) — prime de calendar réduite"

    step2_pairs = build_pairs(near_extended, far_normal, warning_fn=near_short_warning)
    if step2_pairs:
        step2_pairs.sort(key=lambda x: -x[2])
        return step2_pairs[:top_n]

    # ── Étape 3 : extension far vers le haut ──────────────────────────────────
    far_extended = [e for e in available_expirations if far_max < days_out(e) <= far_max + 30]
    all_near = near_normal + near_extended

    def build_pairs_with_warning(near_candidates, far_candidates, warning_fn=None):
        pairs = []
        for near in near_candidates:
            wfn = warning_fn if near in near_extended else None
            for far in far_candidates:
                if (far - near).days < 10:
                    continue
                factor, sweet_names, has_critical = classify(near, far)
                if has_critical:
                    continue
                warning = wfn(near) if wfn else None
                pairs.append((near, far, factor, sweet_names, warning))
        return pairs

    step3_pairs = build_pairs_with_warning(all_near, far_extended,
                                            warning_fn=near_short_warning)
    if step3_pairs:
        step3_pairs.sort(key=lambda x: -x[2])
        return step3_pairs[:top_n]

    # ── Étape 4 : dernier recours ─────────────────────────────────────────────
    # Meilleure paire normale (étape 1, CRITICAL accepté) + warning explicite
    all_far = far_normal + far_extended if far_extended else far_normal
    last_resort = []
    for near in near_normal:
        for far in all_far:
            if (far - near).days < 10:
                continue
            factor, sweet_names, has_critical = classify(near, far)
            if not has_critical:
                continue
            # Identifier l'événement CRITICAL en danger zone
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
    # pair_event_info : (near, far) → (factor, sweet_names, warning)
    pair_event_info: dict[tuple[date, date], tuple[float, list[str], str | None]] = {}

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
                sweet_names = [f"{ev.name} {ev.date.strftime('%d/%m')}" for ev in profile["sweet_zone"]]
                pair_event_info[(near, far)] = (profile["event_score_factor"], sweet_names, None)

    else:
        # use_adjacent_expiry_pairs=False (templates 1-3 : calendar strangle, etc.)
        if event_calendar is not None:
            selected = _select_event_pairs(
                expirations,
                config.SCANNER_NEAR_EXPIRY_RANGE,
                config.SCANNER_FAR_EXPIRY_RANGE,
                event_calendar,
            )
            if selected:
                expiry_pairs = [(near, far) for near, far, _, _, _ in selected]
                pair_event_info = {
                    (near, far): (factor, sweet, warning)
                    for near, far, factor, sweet, warning in selected
                }
            else:
                expiry_pairs = [(expirations[0], expirations[-1])]
        else:
            expiry_pairs = [(expirations[0], expirations[-1])]

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
