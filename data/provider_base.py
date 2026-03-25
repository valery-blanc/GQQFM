"""Interface abstraite DataProvider."""

from datetime import date
from typing import Protocol

from data.models import OptionsChain


class DataProvider(Protocol):
    """Interface abstraite pour les fournisseurs de données d'options."""

    def get_options_chain(
        self,
        symbol: str,
        min_expiry: date | None = None,
        max_expiry: date | None = None,
        min_strike: float | None = None,
        max_strike: float | None = None,
        min_volume: int = 0,
        min_open_interest: int = 0,
    ) -> OptionsChain:
        """Récupère la chaîne d'options filtrée pour un sous-jacent."""
        ...

    def get_risk_free_rate(self) -> float:
        """Taux sans risque actuel.

        V1 : constante 0.045 (définie dans config.py).
        V2 : fetch ^IRX (T-bill 13 semaines) via yfinance.
        """
        ...
