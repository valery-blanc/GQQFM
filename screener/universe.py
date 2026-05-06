"""Univers statique de sous-jacents analysés par le screener (FEAT-023 § Étape 3)."""

# ETFs liquides (options actives)
ETFS: list[str] = [
    # Indices US large
    "SPY", "QQQ", "IWM", "DIA",
    # Sectoriels SPDR
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLC",
    "XBI", "SMH", "GDX", "GLD", "SLV", "TLT", "HYG",
    # Internationaux / thématiques
    "EEM", "EWZ", "FXI", "ARKK", "KWEB", "SOXX", "IBB", "KRE",
    # Defensifs / diversifiants (FEAT-023)
    "EFA", "IEMG", "LQD", "IEF",
    # USO retiré : options sur futures, pricing désaligné avec BS (cohérent VIX)
    # VIX exclu : options sur futures, pricing cash-settled, incompatible BS.
]

# Actions US large/mid cap avec options liquides (vol modérée — calendar-friendly)
STOCKS: list[str] = [
    # Tech mega-cap
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AVGO", "ORCL", "CRM", "AMD", "ADBE", "INTC", "CSCO", "QCOM",
    "NFLX", "UBER", "SNOW", "MU", "MRVL", "ANET", "PANW", "CRWD",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
    # Santé / Pharma (pas trop biotech)
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN", "GILD",
    # Énergie
    "XOM", "CVX", "COP", "SLB", "OXY",
    # Consommation / Distribution
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS", "TGT", "LOW", "PEP", "KO",
    # Industriels
    "BA", "CAT", "GE", "DE", "HON", "UPS", "RTX", "LMT",
    # Divers
    "F", "GM", "T", "VZ", "AAL", "DAL", "UAL",
]

# Tickers haute volatilité — pertinents pour reverse iron condor mais
# rarement bons pour calendar (IV crush, spread % large, gaps fréquents).
# Inclus uniquement quand l'utilisateur coche "inclure haute vol" dans la sidebar.
HIGH_VOL_TICKERS: list[str] = [
    "COIN", "PLTR", "SHOP", "SQ", "MRNA", "BIIB", "NIO", "BABA",
    "PYPL", "SBUX",
]

# Univers par défaut (calendar-friendly)
UNIVERSE: list[str] = ETFS + STOCKS


def get_universe(include_high_vol: bool = False) -> list[str]:
    """Retourne l'univers à scanner. Inclut les tickers haute vol si demandé."""
    if include_high_vol:
        return ETFS + STOCKS + HIGH_VOL_TICKERS
    return UNIVERSE
