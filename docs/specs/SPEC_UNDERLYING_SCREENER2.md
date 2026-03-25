# Module Underlying Screener — Spécifications Techniques

## 1. Vue d'ensemble

### 1.1 Objectif

Module complémentaire au scanner d'options (SPEC_OPTIONS_SCANNER.md) qui identifie automatiquement les X meilleurs sous-jacents du moment pour les stratégies de type calendar strangle. L'utilisateur clique sur un bouton dans la sidebar, le module analyse un univers pré-défini, et injecte les tickers résultants (séparés par des virgules) dans le champ de saisie sous-jacent de l'application principale.

### 1.2 Interaction utilisateur

```
SIDEBAR (ajout au layout existant)
──────────────────────────────
  Sous-jacent: [           ]  ← champ texte, accepte "SPY" ou "SPY,QQQ,AAPL"

  Nombre de résultats: [5 ▼]  ← dropdown : 1 à 10, défaut : 5

  [🔍 Trouver les meilleurs sous-jacents]

  Après exécution :
  ┌──────────────────────────┐
  │ ✓ 5 sous-jacents trouvés │
  │ 1. QQQ  (score 87) ★     │  ★ = événement macro favorable
  │ 2. AAPL (score 82)       │
  │ 3. NVDA (score 79)       │
  │ 4. IWM  (score 74) ★     │
  │ 5. META (score 71)       │
  │ [Utiliser ces résultats]  │ ← injecte "QQQ,AAPL,NVDA,IWM,META"
  │ [Détails du screening ▼]  │
  └──────────────────────────┘

  ⚠ (si marché fermé) :
  "Marché US fermé. Les données IV peuvent être imprécises.
   Pour un screening fiable, relancez pendant les heures de
   marché (15h30-22h00 heure de Genève)."
```

Clic "Utiliser ces résultats" : le champ sous-jacent reçoit les tickers CSV, le scanner itère ensuite sur chacun.

### 1.3 Contraintes

- CPU uniquement (appels API + calculs légers, pas de GPU)
- Sources : **yfinance** (gratuit) + **Finnhub** (gratuit, clé API requise)
- Temps cible : **< 3 minutes**
- Rate limiting yfinance : ~2 req/s
- Module indépendant du GPU engine, testable seul

### 1.4 Limitation IV hors-séance

yfinance retourne IV ≈ 0 quand le marché est fermé (bid=ask=0). Le screener utilise l'IV brute sans correction. Hors séance, les tickers sans cotation afterhours sont éliminés par `iv_data_missing`.

**V1** : pas de fallback bisection (trop lent sur 50+ tickers). Avertissement UI si marché fermé (section 10.1).

**V2** : cache JSON des IV de la dernière séance, utilisé hors-séance.

---

## 2. Architecture — Pipeline en entonnoir

```
Étape 1 — Univers statique              ~128 tickers  (instantané)
Étape 2 — Filtre stock rapide           ~128→80       (~5s, batch yfinance)
Étape 3 — Chargement calendrier events   enrichissement (~2s, 1 req Finnhub)
Étape 4 — Filtre événements micro        ~80→50        (~10s, earnings/div)
Étape 5 — Analyse options détaillée      ~50→top X     (~2min, rate limited)
```

### 2.1 Interface publique

```python
@dataclass
class ScreenerResult:
    symbol: str
    score: float                      # 0-100
    spot_price: float
    iv_rank_proxy: float              # 0-100
    term_structure_ratio: float
    avg_option_spread_pct: float
    avg_option_volume: float
    avg_open_interest: float
    strike_count: int
    weekly_expiries_available: bool
    next_earnings_date: date | None
    next_ex_div_date: date | None
    events_in_near_zone: list[str]    # noms événements [today, near_expiry]
    events_in_sweet_zone: list[str]   # noms événements [near_expiry, far_expiry]
    has_event_bonus: bool
    disqualification_reason: str | None

class UnderlyingScreener:
    def screen(self, top_n=5, near_expiry_range=(5,21),
               far_expiry_range=(25,70), progress_callback=None) -> list[ScreenerResult]: ...
```

---

## 3. Module EventCalendar (partagé avec le scanner)

### 3.1 Objectif

Source unique d'événements générateurs de volatilité. Partagé entre screener ET scanner (placé dans `events/` à la racine). Le scanner l'utilisera pour scorer les paires d'expirations de chaque combinaison (merge ultérieur de SPEC_OPTIONS_SCANNER.md).

### 3.2 Modèles

```python
class EventImpact(Enum):
    CRITICAL = 3    # FOMC rate decision, NFP
    HIGH = 2        # CPI, GDP, PCE Core
    MODERATE = 1    # FOMC Minutes, ISM, PPI

class EventScope(Enum):
    MACRO = "macro"   # tous les sous-jacents (indices)
    MICRO = "micro"   # un seul sous-jacent (earnings, FDA)

@dataclass
class MarketEvent:
    date: date
    name: str
    impact: EventImpact
    scope: EventScope
    symbol: str | None = None  # None pour macro
```

### 3.3 Source 1 — Table statique FOMC

```python
# events/fomc_calendar.py
FOMC_DECISIONS_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]
FOMC_MINUTES_2026 = [
    "2026-02-19", "2026-04-09", "2026-05-27", "2026-07-08",
    "2026-08-19", "2026-10-07", "2026-11-25",
]
```

### 3.4 Source 2 — API Finnhub

```python
# events/finnhub_calendar.py
TRACKED_EVENTS = {
    "Nonfarm Payrolls":         ("NFP", EventImpact.CRITICAL),
    "Non Farm Payrolls":        ("NFP", EventImpact.CRITICAL),
    "CPI MoM":                  ("CPI", EventImpact.HIGH),
    "CPI YoY":                  ("CPI", EventImpact.HIGH),
    "Core CPI MoM":             ("Core CPI", EventImpact.HIGH),
    "GDP Growth Rate QoQ":      ("GDP", EventImpact.HIGH),
    "GDP Growth Rate QoQ Adv":  ("GDP Advance", EventImpact.HIGH),
    "Core PCE Price Index MoM": ("PCE Core", EventImpact.HIGH),
    "Core PCE Price Index YoY": ("PCE Core", EventImpact.HIGH),
    "ISM Manufacturing PMI":    ("ISM Mfg", EventImpact.MODERATE),
    "ISM Services PMI":         ("ISM Svc", EventImpact.MODERATE),
    "PPI MoM":                  ("PPI", EventImpact.MODERATE),
}

def fetch_macro_events(from_date, to_date, api_key) -> list[MarketEvent]:
    """1 requête Finnhub + fusion FOMC statiques. Fallback FOMC si API down."""
```

### 3.5 Interface unifiée

```python
class EventCalendar:
    def __init__(self, finnhub_api_key: str | None = None): ...
    def load(self, from_date: date, to_date: date) -> None: ...
    def get_events_in_range(self, start, end, min_impact=EventImpact.MODERATE) -> list[MarketEvent]: ...
    def classify_events_for_pair(self, near_expiry: date, far_expiry: date) -> dict:
        """
        Retourne:
        - danger_zone: list[MarketEvent]   [today, near_expiry]
        - sweet_zone: list[MarketEvent]    [near_expiry+1, far_expiry]
        - has_critical_in_danger: bool
        - has_high_in_sweet: bool
        - event_score_factor: float

        event_score_factor :
        Base 1.0.
        Par CRITICAL/HIGH en danger : × 0.4
        Par MODERATE en danger : × 0.7
        Par CRITICAL/HIGH en sweet : + 0.05 (plafonné +0.15)
        Par MODERATE en sweet : + 0.02 (inclus dans plafond)
        """
```

### 3.6 Impact scanner (pour merge ultérieur)

- **Combinator** : itère sur plusieurs paires d'expirations, `classify_events_for_pair()` pour chacune.
- **Scorer** : `event_score_factor` comme multiplicateur sur le score de chaque combinaison.
- **Hors-scope de cette spec.**

---

## 4. Étape 1 — Univers statique

```python
# screener/universe.py
ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "EEM",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLC",
    "XBI", "SMH", "GDX", "GLD", "SLV", "TLT", "HYG",
    "EWZ", "FXI", "USO", "ARKK", "KWEB", "SOXX", "IBB", "KRE",
    # VIX EXCLU : options sur VIX futures, pas sur l'indice.
    # Pricing, settlement (cash), mean-reversion incompatibles avec BS.
]
STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AVGO", "ORCL", "CRM", "AMD", "ADBE", "INTC", "CSCO", "QCOM",
    "NFLX", "UBER", "SHOP", "SQ", "SNOW", "PLTR", "COIN", "MU",
    "MRVL", "ANET", "PANW", "CRWD",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN", "GILD", "MRNA", "BIIB",
    "XOM", "CVX", "COP", "SLB", "OXY",
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS", "TGT", "LOW",
    "BA", "CAT", "GE", "DE", "HON", "UPS", "RTX", "LMT",
    "BABA", "NIO", "PYPL", "F", "GM", "T", "VZ", "AAL", "DAL", "UAL",
]
UNIVERSE = ETFS + STOCKS  # ~128 tickers
```

---

## 5-8. Étapes 2 à 5

### 5. Filtre stock rapide

Batch yfinance, élimine prix < $50, volume < 1M/jour. ~5s.

### 6. Chargement calendrier événements

1 requête Finnhub pour [today, today+far_max+7j]. ~2s. Fallback FOMC si API down.

### 7. Filtre événements micro

Élimine earnings dans [today, near_max+buffer]. Flagge ex-dividendes. ~10s. ETFs passent toujours.

### 8. Analyse options détaillée

Pour chaque des ~50 tickers restants :
1. `select_expirations()` avec `event_calendar` → choisit la meilleure paire (near, far) en favorisant un événement en sweet zone
2. Chaînes d'options near + far via yfinance
3. Calcul IV ATM (retourne 0 hors-séance → filtre `iv_data_missing`)
4. HV 30j, IV Rank proxy = `clip((IV/HV - 0.6) / 1.2 * 100, 0, 100)`
5. Liquidité : spread, volume, OI, strike count
6. Profil événementiel : `classify_events_for_pair(near, far)`

Rate limited 0.5s/ticker. ~2 minutes total.

---

## 9. Scoring et classement

### 9.1 Filtres éliminatoires

```python
DISQUALIFICATION_RULES = {
    "spread_too_wide":        lambda m: m.avg_bid_ask_spread_pct > 0.10,
    "no_volume":              lambda m: (m.avg_volume_near + m.avg_volume_far) / 2 < 100,
    "no_open_interest":       lambda m: (m.avg_oi_near + m.avg_oi_far) / 2 < 500,
    "not_enough_strikes":     lambda m: min(m.strike_count_near, m.strike_count_far) < 10,
    "iv_data_missing":        lambda m: m.iv_atm_near <= 0 or m.iv_atm_far <= 0,
    "critical_event_in_near": lambda m: any(
        e.impact == EventImpact.CRITICAL for e in m.events_in_danger_zone),
}
```

### 9.2 Score composite (5 composantes)

| # | Composante | Poids | Formule |
|---|-----------|-------|---------|
| 1 | IV Rank proxy | 0.30 | `1.0 - abs(iv_rank - 45) / 55` |
| 2 | Term structure | 0.25 | 1.0 si ratio ≤ 1.00, décroît linéairement → 0 à 1.30 |
| 3 | Liquidité | 0.20 | mix volume + OI + spread inversé |
| 4 | Densité strikes | 0.10 | mix strike count + weeklies |
| 5 | **Profil événementiel** | **0.15** | `clip((event_score_factor - 0.5) / 1.0, 0, 1)` |

Pénalités multiplicatives : ×0.3 ex-div, ×0.5 IV Rank>70, ×0.7 backwardation>1.15.

---

## 10. Intégration UI

- `is_us_market_open()` : NYSE 9:30-16:00 ET, lun-ven
- Avertissement hors-séance sur fiabilité IV
- Résultats avec ★ pour event bonus
- Bouton "Utiliser" injecte dans `session_state["symbols_input"]`
- Expander détails : tableau Ticker/Score/IV Rank/Term Str/Spread/Events★

---

## 11. Structure

```
screener/
├── __init__.py
├── universe.py
├── stock_filter.py
├── event_filter.py
├── options_analyzer.py
├── scorer.py
├── screener.py
└── models.py

events/                        # PARTAGÉ screener + scanner
├── __init__.py
├── calendar.py
├── fomc_calendar.py
├── finnhub_calendar.py
└── models.py
```

---

## 12. Configuration (ajout à config.py)

```python
# Screener
SCREENER_MIN_PRICE = 50.0
SCREENER_MIN_AVG_VOLUME = 1_000_000
SCREENER_EARNINGS_BUFFER_DAYS = 2
SCREENER_REQUEST_DELAY = 0.5
SCREENER_DEFAULT_TOP_N = 5
SCREENER_NEAR_EXPIRY_RANGE = (5, 21)
SCREENER_FAR_EXPIRY_RANGE = (25, 70)
SCREENER_MAX_SPREAD_PCT = 0.10
SCREENER_MIN_AVG_OPTION_VOLUME = 100
SCREENER_MIN_AVG_OPEN_INTEREST = 500
SCREENER_MIN_STRIKE_COUNT = 10

# Scoring (somme = 1.0)
SCREENER_SCORE_WEIGHT_IV_RANK = 0.30
SCREENER_SCORE_WEIGHT_TERM_STRUCTURE = 0.25
SCREENER_SCORE_WEIGHT_LIQUIDITY = 0.20
SCREENER_SCORE_WEIGHT_DENSITY = 0.10
SCREENER_SCORE_WEIGHT_EVENTS = 0.15
SCREENER_PENALTY_EX_DIV = 0.3
SCREENER_PENALTY_HIGH_IV_RANK = 0.5
SCREENER_PENALTY_BACKWARDATION = 0.7

# EventCalendar
FINNHUB_API_KEY = None  # env var FINNHUB_API_KEY, fallback FOMC statiques
EVENT_PENALTY_CRITICAL_IN_NEAR = 0.4
EVENT_PENALTY_MODERATE_IN_NEAR = 0.7
EVENT_BONUS_HIGH_IN_SWEET = 0.05
EVENT_BONUS_MODERATE_IN_SWEET = 0.02
EVENT_BONUS_CAP = 0.15
```

---

## 13. Tests (tous mockés, sans réseau)

```
test_screener_scoring.py :
  T1: IV Rank optimal (45→1.0, 0→~0.18, 100→0.0)
  T2: Term structure (0.95→1.0, 1.15→0.5, 1.30→0.0)
  T3: Pénalités (ex-div+IVR80 → 0.15)
  T4: Classement (5 métriques → bon ordre)
  T5: Score événementiel (factor 1.15→0.65, 1.0→0.50, 0.4→0.0)

test_screener_filters.py :
  T6: Spread (0.15→disqualifié, 0.05→qualifié)
  T7: Earnings (10j→éliminé, 80j→conservé)
  T8: CRITICAL near (FOMC 5j→éliminé)
  T9: Sélection expirations (FOMC j30 → paire optimale)

test_event_calendar.py :
  T10: FOMC statique chargé
  T11: classify sweet (FOMC entre near/far → factor>1.0)
  T12: classify danger (FOMC avant near → factor<1.0)
  T13: Fallback sans Finnhub (FOMC seuls, pas d'erreur)

test_screener_integration.py :
  T14: Pipeline 10 tickers mockés → top 3 correct, FOMC sweet mieux classés
```

---

## 14. Feuille de route

**V1** : univers 128 tickers, IV Rank proxy, EventCalendar (FOMC+Finnhub), scoring 5 composantes, avertissement hors-séance, tests mockés.

**V2** : IV Rank réel (SQLite), cache IV hors-séance, univers dynamique, mini-graphique term structure, auto-update FOMC.

**V3** : screening background, alertes top 5, données IBKR, événements micro FDA.

---

## Annexe A — Décisions de conception

| # | Question | Décision | Justification |
|---|----------|----------|---------------|
| A1 | IV hors-séance : bisection ? | Non (V1). Filtre + warning UI. | +5-10min inacceptable. Screener = outil pré-trading. |
| A2 | VIX dans l'univers ? | Exclu définitivement. | Options VIX futures ≠ equity. BS inadapté. |
| A3 | Événements : éliminatoires ou bonus ? | Les deux. CRITICAL near → éliminatoire. HIGH+ sweet → bonus. | FOMC near = gap risk. FOMC sweet = prime vol legs longs. |
| A4 | Finnhub obligatoire ? | Non. Fallback FOMC statiques. | App fonctionne sans config. FOMC = ~80% valeur événements indices. |
| A5 | EventCalendar partagé ? | Oui, `events/` racine. | DRY. Source unique = cohérence screener/scanner. |
