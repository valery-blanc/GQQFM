"""Structures de base pour les templates de stratégies."""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from data.models import Combination, Leg, OptionsChain


@dataclass
class LegSpec:
    """Spécification d'un leg dans un template."""
    option_type: str        # "call" ou "put"
    direction: int          # +1 = long, -1 = short
    quantity_range: range   # ex: range(1, 6) pour 1 à 5 contrats
    strike_range: tuple[float, float]   # (min_factor, max_factor) × spot
    strike_step: float                  # pas de variation (facteur)
    expiry_selector: str    # "NEAR" ou "FAR"


@dataclass
class TemplateDefinition:
    """Définition d'un template de stratégie."""
    name: str
    description: str
    legs_spec: list[LegSpec]
    constraints: list[Callable[[list[Leg]], bool]] = field(default_factory=list)
    use_adjacent_expiry_pairs: bool = False   # itère sur toutes les paires proches (5-45j)


def find_nearest_strike(chain: OptionsChain, option_type: str, target: float) -> float | None:
    """Retourne le strike disponible le plus proche de target pour un type donné."""
    candidates = sorted(
        set(c.strike for c in chain.contracts if c.option_type == option_type),
    )
    if not candidates:
        return None
    return min(candidates, key=lambda s: abs(s - target))


def get_contracts_in_strike_range(
    chain: OptionsChain,
    option_type: str,
    min_strike: float,
    max_strike: float,
    expiration: date,
) -> list:
    """Retourne tous les contrats correspondant aux critères."""
    return [
        c for c in chain.contracts
        if c.option_type == option_type
        and c.expiration == expiration
        and min_strike <= c.strike <= max_strike
    ]
