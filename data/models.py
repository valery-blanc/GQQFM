"""Modèles de données : contrats d'options, chaînes, legs et combinaisons."""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class OptionContract:
    """Un contrat d'option individuel."""
    contract_symbol: str       # ex: "MSFT240816C00490000"
    option_type: str           # "call" ou "put"
    strike: float
    expiration: date
    bid: float
    ask: float
    mid: float                 # (bid + ask) / 2
    implied_vol: float         # volatilité implicite en décimal (ex: 0.25)
    volume: int
    open_interest: int
    delta: float | None = None
    div_yield: float = 0.0         # rendement de dividende continu annualisé


@dataclass
class OptionsChain:
    """Chaîne d'options complète pour un sous-jacent."""
    underlying_symbol: str
    underlying_price: float
    contracts: list[OptionContract]
    expirations: list[date]
    strikes: list[float]
    fetch_timestamp: datetime
    div_yield: float = 0.0         # rendement de dividende continu annualisé


@dataclass
class Leg:
    """Un leg dans une combinaison d'options."""
    option_type: str           # "call" ou "put"
    direction: int             # +1 = long, -1 = short
    quantity: int
    strike: float
    expiration: date
    entry_price: float         # mid price, en dollars par action
    implied_vol: float
    contract_symbol: str = ""
    volume: int = 0
    open_interest: int = 0
    div_yield: float = 0.0         # rendement de dividende continu annualisé


@dataclass
class Combination:
    """Une combinaison de 2 à 4 legs."""
    legs: list[Leg]            # 2 à 4 legs
    net_debit: float           # coût d'entrée en dollars, ×100 INCLUS
                               # = Σ (direction × quantity × entry_price × 100)
                               # positif = débit (la position coûte de l'argent)
    close_date: date           # = min(expiration des legs short), calculé auto
    template_name: str
    event_score_factor: float = 1.0              # multiplicateur événementiel (1.0 = neutre)
    events_in_sweet_zone: list[str] = field(default_factory=list)  # noms des événements favorables
    event_warning: str | None = None             # warning si CRITICAL en danger zone ou near court


@dataclass
class ScoringCriteria:
    """Critères de sélection définis par l'utilisateur."""
    max_loss_pct: float = -6.0
    max_loss_probability_pct: float = 25.0
    min_max_gain_pct: float = 50.0
    min_gain_loss_ratio: float = 5.0
    max_net_debit: float = 10_000.0
    min_avg_volume: int = 50
    curve_shape: str = "smile"   # V2 uniquement
