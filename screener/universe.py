"""Univers statique de sous-jacents analysés par le screener."""

# ETFs liquides (options actives)
ETFS: list[str] = [
    "SPY", "QQQ", "IWM", "DIA", "EEM",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLC",
    "XBI", "SMH", "GDX", "GLD", "SLV", "TLT", "HYG",
    "EWZ", "FXI", "USO", "ARKK", "KWEB", "SOXX", "IBB", "KRE",
    # VIX exclu : options sur futures, pricing cash-settled, incompatible avec BS.
]

# Actions US large/mid cap avec options liquides
STOCKS: list[str] = [
    # Tech mega-cap
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AVGO", "ORCL", "CRM", "AMD", "ADBE", "INTC", "CSCO", "QCOM",
    "NFLX", "UBER", "SHOP", "SQ", "SNOW", "PLTR", "COIN", "MU",
    "MRVL", "ANET", "PANW", "CRWD",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
    # Santé / Pharma
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN", "GILD", "MRNA", "BIIB",
    # Énergie
    "XOM", "CVX", "COP", "SLB", "OXY",
    # Consommation / Distribution
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS", "TGT", "LOW",
    # Industriels
    "BA", "CAT", "GE", "DE", "HON", "UPS", "RTX", "LMT",
    # Divers
    "BABA", "NIO", "PYPL", "F", "GM", "T", "VZ", "AAL", "DAL", "UAL",
]

UNIVERSE: list[str] = ETFS + STOCKS
