"""Configuration globale et constantes."""

# Taux sans risque (V1 : constante, V2 : fetch ^IRX)
DEFAULT_RISK_FREE_RATE: float = 0.045  # 4.5%

# Grille de spots pour le calcul P&L
SPOT_RANGE_LOW: float = 0.85   # spot × 85%
SPOT_RANGE_HIGH: float = 1.15  # spot × 115%
NUM_SPOT_POINTS: int = 200

# Scénarios de volatilité par défaut
DEFAULT_VOL_LOW: float = 0.8   # vol implicite × 80%
DEFAULT_VOL_HIGH: float = 1.2  # vol implicite × 120%
# Le scénario médian est toujours 1.0 (vol inchangée), fixe, non configurable.
VOL_MEDIAN_INDEX: int = 1      # index du scénario médian dans [vol_low, 1.0, vol_high]

# Filtrage initial des données
MAX_DAYS_TO_EXPIRY: int = 90
MIN_DAYS_TO_EXPIRY: int = 2
MAX_STRIKE_PCT_FROM_SPOT: float = 0.20   # ±20% du spot
MAX_BID_ASK_SPREAD_PCT: float = 0.20     # 20% du mid
MIN_OPEN_INTEREST: int = 10

# GPU / batching
MAX_GPU_MEMORY_BYTES: int = 12 * 1024**3   # 12 GB max sur 16 GB
BYTES_PER_COMBO_PER_SPOT: int = 4 * 4      # 4 bytes × 4 tenseurs intermédiaires
GPU_SAFETY_FACTOR: float = 2.5

# Nombre max de combinaisons générées par template
MAX_COMBINATIONS: int = 500_000

# Scoring weights
SCORE_WEIGHT_GAIN_LOSS_RATIO: float = 0.4
SCORE_WEIGHT_LOSS_PROB: float = 0.3
SCORE_WEIGHT_EXPECTED_RETURN: float = 0.3

# ── Screener ──
SCREENER_MIN_PRICE: float = 50.0
SCREENER_MIN_AVG_VOLUME: int = 1_000_000
SCREENER_EARNINGS_BUFFER_DAYS: int = 2
SCREENER_REQUEST_DELAY: float = 0.5
SCREENER_DEFAULT_TOP_N: int = 5
SCREENER_NEAR_EXPIRY_RANGE: tuple = (5, 21)
SCREENER_FAR_EXPIRY_RANGE: tuple = (25, 70)
SCREENER_MAX_SPREAD_PCT: float = 0.10
SCREENER_MIN_AVG_OPTION_VOLUME: int = 100
SCREENER_MIN_AVG_OPEN_INTEREST: int = 500
SCREENER_MIN_STRIKE_COUNT: int = 10

# Screener scoring weights (sum = 1.0)
SCREENER_SCORE_WEIGHT_IV_RANK: float = 0.30
SCREENER_SCORE_WEIGHT_TERM_STRUCTURE: float = 0.25
SCREENER_SCORE_WEIGHT_LIQUIDITY: float = 0.20
SCREENER_SCORE_WEIGHT_DENSITY: float = 0.10
SCREENER_SCORE_WEIGHT_EVENTS: float = 0.15

# Screener penalty multipliers
SCREENER_PENALTY_EX_DIV: float = 0.3
SCREENER_PENALTY_HIGH_IV_RANK: float = 0.5
SCREENER_PENALTY_BACKWARDATION: float = 0.7

# ── Scanner expiry ranges (sélection de paires événementielles) ──
SCANNER_NEAR_EXPIRY_RANGE: tuple = (5, 21)    # jours avant expiration near
SCANNER_FAR_EXPIRY_RANGE: tuple = (25, 70)    # jours avant expiration far

# EventCalendar
FINNHUB_API_KEY: str | None = None  # override via env var FINNHUB_API_KEY
EVENT_PENALTY_CRITICAL_IN_NEAR: float = 0.4
EVENT_PENALTY_MODERATE_IN_NEAR: float = 0.7
EVENT_BONUS_HIGH_IN_SWEET: float = 0.05
EVENT_BONUS_MODERATE_IN_SWEET: float = 0.02
EVENT_BONUS_CAP: float = 0.15
