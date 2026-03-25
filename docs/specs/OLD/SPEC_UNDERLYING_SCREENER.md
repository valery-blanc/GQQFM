# Module Underlying Screener — Spécifications Techniques

## 1. Vue d'ensemble

### 1.1 Objectif

Module complémentaire au scanner d'options (SPEC_OPTIONS_SCANNER.md) qui identifie automatiquement les X meilleurs sous-jacents du moment pour les stratégies de type calendar strangle. L'utilisateur clique sur un bouton dans la sidebar, le module analyse un univers pré-défini, et injecte les tickers résultants (séparés par des virgules) dans le champ de saisie sous-jacent de l'application principale.

### 1.2 Interaction utilisateur

```
SIDEBAR (ajout au layout existant)
──────────────────────────────
  Sous-jacent: [           ]  ← champ texte, accepte "SPY" ou "SPY,QQQ,AAPL"

  Nombre de résultats: [5 ▼]  ← dropdown : 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
                                  défaut : 5

  [🔍 Trouver les meilleurs    ← bouton
     sous-jacents]

  Pendant l'exécution :
  ┌──────────────────────────┐
  │ ◌ Analyse en cours...    │
  │ ████████░░░░ 67%         │
  │ Étape 3/4 : Analyse      │
  │ options de NVDA...       │
  └──────────────────────────┘

  Après exécution :
  ┌──────────────────────────┐
  │ ✓ 5 sous-jacents trouvés │
  │ en 2m 14s                │
  │                          │
  │ 1. QQQ   (score 87)     │
  │ 2. AAPL  (score 82)     │
  │ 3. NVDA  (score 79)     │
  │ 4. IWM   (score 74)     │
  │ 5. META  (score 71)     │
  │                          │
  │ [Utiliser ces résultats] │ ← injecte "QQQ,AAPL,NVDA,IWM,META"
  │ [Détails du screening ▼] │   dans le champ sous-jacent
  └──────────────────────────┘
```

Quand l'utilisateur clique "Utiliser ces résultats" :
- Le champ sous-jacent est mis à jour avec les tickers séparés par des virgules
- Le scanner principal peut ensuite être lancé : il itère sur chaque ticker de la liste
- L'utilisateur peut aussi modifier la liste manuellement avant de lancer le scan

### 1.3 Contraintes

- Le screening tourne **côté CPU** (pas de GPU, ce sont des appels API + calculs légers)
- Source de données : **yfinance** (gratuit, pas de clé API)
- Temps d'exécution cible : **< 3 minutes** pour l'analyse complète
- Rate limiting yfinance : respecter ~2 requêtes/seconde pour éviter les bans
- Le module est **indépendant** du GPU engine et peut être testé seul

---

## 2. Architecture

### 2.1 Pipeline en entonnoir

```
┌─────────────────────────────────────────────────────────┐
│  Étape 1 — Univers statique                    ~150     │
│  Liste codée en dur de tickers connus pour               │
│  avoir des options liquides.                             │
│  Données : aucune requête API.                           │
│  Durée : instantané.                                     │
└────────────────────────┬────────────────────────────────┘
                         │ ~150 tickers
┌────────────────────────▼────────────────────────────────┐
│  Étape 2 — Filtre stock rapide                 ~150→80  │
│  Requête batch yfinance : prix, volume action.           │
│  Élimine : prix < $50, volume < 1M/jour.                │
│  Durée : ~5s (1 requête batch).                          │
└────────────────────────┬────────────────────────────────┘
                         │ ~80 tickers
┌────────────────────────▼────────────────────────────────┐
│  Étape 3 — Filtre événements                   ~80→50   │
│  Requête yfinance : earnings date, ex-dividend date.     │
│  Élimine : earnings ou ex-div dans la fenêtre.           │
│  Durée : ~10s.                                           │
└────────────────────────┬────────────────────────────────┘
                         │ ~50 tickers
┌────────────────────────▼────────────────────────────────┐
│  Étape 4 — Analyse options détaillée           ~50→X    │
│  Requête individuelle par ticker : chaînes d'options     │
│  pour 2 expirations (near + far).                        │
│  Calcul : IV, term structure, liquidité, densité strikes.│
│  Scoring + classement → top X.                           │
│  Durée : ~2 minutes (rate limited).                      │
└────────────────────────┬────────────────────────────────┘
                         │ top X tickers
                         ▼
              Injection dans le champ sous-jacent
```

### 2.2 Intégration avec le scanner principal

Le screener est un module autonome. Son interface avec le scanner est minimale :

```python
@dataclass
class ScreenerResult:
    """Résultat du screening pour un sous-jacent."""
    symbol: str
    score: float                # score composite, 0-100
    spot_price: float
    iv_rank_proxy: float        # 0-100
    term_structure_ratio: float # IV_near / IV_far
    avg_option_spread_pct: float
    avg_option_volume: float
    avg_open_interest: float
    strike_count: int           # nb strikes dans ±10%
    weekly_expiries_available: bool
    next_earnings_date: date | None
    next_ex_div_date: date | None
    disqualification_reason: str | None  # None si qualifié

class UnderlyingScreener:
    """Point d'entrée du module."""

    def screen(
        self,
        top_n: int = 5,
        near_expiry_range: tuple[int, int] = (5, 21),
        far_expiry_range: tuple[int, int] = (25, 70),
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> list[ScreenerResult]:
        """
        Lance le screening complet et retourne les top_n résultats.

        Paramètres:
        -----------
        top_n : int
            Nombre de sous-jacents à retourner (1 à 10).

        near_expiry_range : tuple[int, int]
            Fenêtre en jours pour l'expiration near (min_days, max_days).
            Défaut : entre 5 et 21 jours.

        far_expiry_range : tuple[int, int]
            Fenêtre en jours pour l'expiration far (min_days, max_days).
            Défaut : entre 25 et 70 jours.

        progress_callback : Callable[[str, float], None] | None
            Callback pour la barre de progression Streamlit.
            Reçoit (message: str, progress: float entre 0.0 et 1.0).
            Exemple : progress_callback("Analyse options de NVDA...", 0.67)

        Retourne:
        ---------
        list[ScreenerResult] trié par score décroissant, longueur = top_n.
        """
```

Le champ sous-jacent du scanner principal doit être modifié pour accepter une liste :

```python
# Avant (SPEC_OPTIONS_SCANNER.md, sidebar) :
symbol = st.text_input("Sous-jacent", value="SPY")

# Après :
symbols_str = st.text_input("Sous-jacent(s)", value="SPY",
                             help="Un ticker ou plusieurs séparés par des virgules")
symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
# Le scanner itère ensuite sur chaque symbol dans symbols.
```

---

## 3. Étape 1 — Univers statique

### 3.1 Liste des tickers

Liste maintenue dans `screener/universe.py`. Critères d'inclusion : options weeklies disponibles, historique de volume options élevé, couverture sectorielle diversifiée.

```python
# screener/universe.py

"""
Univers de sous-jacents candidats pour le screening.
Mis à jour manuellement, trimestriellement.
Dernière mise à jour : 2026-Q1.
"""

# ── ETFs majeurs (options très liquides, pas d'earnings) ──
ETFS = [
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000
    "DIA",   # Dow Jones
    "EEM",   # Emerging Markets
    "XLF",   # Financials
    "XLE",   # Energy
    "XLK",   # Technology
    "XLV",   # Healthcare
    "XLI",   # Industrials
    "XLP",   # Consumer Staples
    "XLY",   # Consumer Discretionary
    "XLU",   # Utilities
    "XLC",   # Communication Services
    "XBI",   # Biotech
    "SMH",   # Semiconductors
    "GDX",   # Gold Miners
    "GLD",   # Gold
    "SLV",   # Silver
    "TLT",   # 20+ Year Treasuries
    "HYG",   # High Yield Corporate Bonds
    "EWZ",   # Brazil
    "FXI",   # China
    "USO",   # Oil
    "ARKK",  # ARK Innovation
    "KWEB",  # China Internet
    "SOXX",  # Semiconductors (alt)
    "IBB",   # Biotech (alt)
    "KRE",   # Regional Banks
    "VIX",   # Volatility (options sur VIX futures, à traiter séparément)
]

# ── Actions S&P 100 + mega-caps populaires en options ──
STOCKS = [
    # Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AVGO", "ORCL", "CRM", "AMD", "ADBE", "INTC", "CSCO", "QCOM",
    "NFLX", "UBER", "SHOP", "SQ", "SNOW", "PLTR", "COIN", "MU",
    "MRVL", "ANET", "PANW", "CRWD",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
    "AXP", "V", "MA",
    # Santé
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "BMY", "AMGN",
    "GILD", "MRNA", "BIIB",
    # Énergie
    "XOM", "CVX", "COP", "SLB", "OXY",
    # Consommation
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS",
    "TGT", "LOW",
    # Industrie
    "BA", "CAT", "GE", "DE", "HON", "UPS", "RTX", "LMT",
    # Autres
    "BABA", "NIO", "PYPL", "F", "GM", "T", "VZ",
    "AAL", "DAL", "UAL",
]

UNIVERSE = ETFS + STOCKS  # ~130 tickers
```

### 3.2 Maintenance

La liste n'a pas besoin d'être parfaite ni exhaustive. Les sous-jacents avec des options illiquides seront éliminés à l'étape 4. L'objectif est simplement de couvrir les candidats plausibles sans en oublier de majeurs.

En V2, on pourrait générer dynamiquement cette liste en scrapant les "most active options" de CBOE ou Barchart, mais pour la V1 une liste statique suffit.

---

## 4. Étape 2 — Filtre stock rapide

### 4.1 Données requises

Pour chaque ticker : prix actuel et volume moyen journalier.

### 4.2 Implémentation

```python
import yfinance as yf

def filter_stocks_fast(
    tickers: list[str],
    min_price: float = 50.0,
    min_avg_volume: int = 1_000_000,
    progress_callback: Callable | None = None,
) -> list[dict]:
    """
    Filtre rapide en batch via yfinance.

    Utilise yf.Tickers() pour une seule requête batch.
    Retourne les tickers qui passent les critères avec leurs données de base.

    Retourne:
    ---------
    list[dict] avec clés : symbol, price, avg_volume
    """
    if progress_callback:
        progress_callback("Chargement des données de marché...", 0.05)

    # Requête batch : une seule requête HTTP pour tous les tickers
    tickers_obj = yf.Tickers(" ".join(tickers))

    results = []
    for symbol in tickers:
        try:
            info = tickers_obj.tickers[symbol].info
            price = info.get("regularMarketPrice", 0) or 0
            avg_vol = info.get("averageVolume", 0) or 0

            if price >= min_price and avg_vol >= min_avg_volume:
                results.append({
                    "symbol": symbol,
                    "price": price,
                    "avg_volume": avg_vol,
                })
        except Exception:
            continue  # ticker invalide ou API error → skip

    if progress_callback:
        progress_callback(f"{len(results)} tickers passent le filtre prix/volume", 0.15)

    return results
```

### 4.3 Gestion des erreurs

- Si yfinance échoue sur un ticker individuel → le skipper silencieusement
- Si yfinance est totalement inaccessible → erreur claire à l'utilisateur
- Log les tickers skippés pour debug (mais ne pas afficher dans l'UI)

---

## 5. Étape 3 — Filtre événements

### 5.1 Données requises

Pour chaque ticker restant : date du prochain earnings, date du prochain ex-dividend.

### 5.2 Implémentation

```python
from datetime import date, timedelta

def filter_events(
    candidates: list[dict],
    near_expiry_range: tuple[int, int],
    far_expiry_range: tuple[int, int],
    earnings_buffer_days: int = 2,
    progress_callback: Callable | None = None,
) -> list[dict]:
    """
    Élimine les tickers dont un earnings ou ex-dividend tombe
    dans la fenêtre d'expirations visée.

    La "fenêtre dangereuse" est définie comme :
        [aujourd'hui, aujourd'hui + far_expiry_range[1] + earnings_buffer_days]

    Pour les earnings : éliminatoire (le ticker est exclu).
    Pour les ex-dividend : non éliminatoire mais flaggé (pénalité dans le score).

    Paramètres:
    -----------
    candidates : list[dict]
        Sortie de filter_stocks_fast(). Chaque dict a au minimum : symbol, price.

    near_expiry_range, far_expiry_range : tuple[int, int]
        Fenêtres en jours.

    earnings_buffer_days : int
        Marge en jours autour de la date d'earnings.
        Si earnings tombe dans [near_start - buffer, far_end + buffer] → exclu.

    Retourne:
    ---------
    list[dict] : candidats enrichis avec :
        - next_earnings_date: date | None
        - next_ex_div_date: date | None
        - has_earnings_in_window: bool
        - has_ex_div_in_window: bool
    Les tickers avec has_earnings_in_window=True sont EXCLUS de la liste.
    """
    today = date.today()
    window_start = today + timedelta(days=near_expiry_range[0])
    window_end = today + timedelta(days=far_expiry_range[1])

    results = []
    for i, candidate in enumerate(candidates):
        if progress_callback:
            progress_callback(
                f"Vérification événements {candidate['symbol']}...",
                0.15 + 0.15 * (i / len(candidates))
            )

        try:
            ticker = yf.Ticker(candidate["symbol"])
            cal = ticker.calendar  # dict avec earnings dates

            # --- Earnings ---
            earnings_date = None
            # yfinance retourne calendar["Earnings Date"] comme liste de dates
            # ou ticker.earnings_dates comme DataFrame
            if cal and "Earnings Date" in cal:
                edates = cal["Earnings Date"]
                if isinstance(edates, list) and len(edates) > 0:
                    earnings_date = edates[0]
                    if hasattr(earnings_date, "date"):
                        earnings_date = earnings_date.date()

            has_earnings = False
            if earnings_date:
                buffered_start = window_start - timedelta(days=earnings_buffer_days)
                buffered_end = window_end + timedelta(days=earnings_buffer_days)
                has_earnings = buffered_start <= earnings_date <= buffered_end

            if has_earnings:
                continue  # ÉLIMINÉ

            # --- Ex-dividend ---
            ex_div_date = None
            ex_div = ticker.info.get("exDividendDate")
            if ex_div:
                from datetime import datetime
                if isinstance(ex_div, (int, float)):
                    ex_div_date = datetime.fromtimestamp(ex_div).date()
                elif hasattr(ex_div, "date"):
                    ex_div_date = ex_div.date()

            has_ex_div = False
            if ex_div_date:
                has_ex_div = window_start <= ex_div_date <= window_end

            candidate["next_earnings_date"] = earnings_date
            candidate["next_ex_div_date"] = ex_div_date
            candidate["has_ex_div_in_window"] = has_ex_div
            results.append(candidate)

        except Exception:
            continue  # skip on error

    if progress_callback:
        progress_callback(f"{len(results)} tickers sans earnings dans la fenêtre", 0.30)

    return results
```

### 5.3 Note sur les ETFs

Les ETFs (SPY, QQQ, etc.) n'ont pas d'earnings au sens classique. `yfinance` ne retourne pas de date d'earnings pour eux, ce qui est correct — ils passeront toujours ce filtre. Leurs dividendes sont trimestriels et faibles (< 0.5% du prix), donc la pénalité ex-div est légère.

---

## 6. Étape 4 — Analyse options détaillée

### 6.1 Vue d'ensemble

C'est l'étape la plus longue et la plus importante. Pour chaque ticker restant (~50), on récupère la chaîne d'options pour 2 expirations (near et far) et on calcule les métriques de scoring.

### 6.2 Sélection des expirations near et far

```python
def select_expirations(
    available_expirations: list[date],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
) -> tuple[date, date] | None:
    """
    Parmi les expirations disponibles, sélectionne la paire (near, far)
    optimale pour un calendar strangle.

    Logique :
    1. Filtrer les expirations dans near_range → candidats near
    2. Filtrer les expirations dans far_range → candidats far
    3. Parmi les candidats near, choisir celle la plus proche
       du milieu de near_range (compromis theta/gamma).
    4. Parmi les candidats far, choisir celle la plus proche
       du milieu de far_range.
    5. Vérifier qu'il y a au moins 10 jours entre near et far.

    Retourne None si aucune paire valide n'existe.
    """
```

### 6.3 Métriques calculées

Pour chaque ticker qualifié :

```python
@dataclass
class OptionsMetrics:
    """Métriques d'options calculées pour un ticker."""
    symbol: str

    # Expirations sélectionnées
    near_expiry: date
    far_expiry: date

    # Vol implicite
    iv_atm_near: float          # IV ATM pour l'expiration near
    iv_atm_far: float           # IV ATM pour l'expiration far
    term_structure_ratio: float  # iv_atm_near / iv_atm_far

    # IV Rank proxy (V1 : IV/HV ratio)
    hv_30d: float               # Volatilité historique réalisée 30 jours
    iv_hv_ratio: float          # iv_atm_far / hv_30d
    iv_rank_proxy: float        # 0-100, dérivé de iv_hv_ratio

    # Liquidité options (moyennes sur les options ATM ±2 strikes)
    avg_bid_ask_spread_pct: float  # spread moyen en % du mid
    avg_volume_near: float         # volume moyen options near
    avg_volume_far: float          # volume moyen options far
    avg_oi_near: float             # open interest moyen near
    avg_oi_far: float              # open interest moyen far

    # Densité de strikes
    strike_count_near: int      # nb strikes dans ±10% du spot, expiry near
    strike_count_far: int       # nb strikes dans ±10% du spot, expiry far

    # Expirations disponibles
    weekly_count: int           # nb d'expirations weeklies dans near_range
```

### 6.4 Calcul de l'IV ATM

L'IV ATM n'est pas toujours disponible directement via yfinance. Approche :

```python
def compute_iv_atm(
    chain: pd.DataFrame,
    spot: float,
) -> float:
    """
    Calcule l'IV ATM comme la moyenne de l'IV du call et du put
    les plus proches du spot (straddle ATM).

    Algorithme :
    1. Trouver le strike K le plus proche de spot.
    2. Récupérer impliedVolatility du call à K et du put à K.
    3. Si l'un des deux est manquant ou nul, utiliser l'autre.
    4. Retourner la moyenne.

    yfinance fournit impliedVolatility dans le DataFrame
    retourné par ticker.option_chain(expiry).calls / .puts
    """
```

### 6.5 Calcul de l'IV Rank proxy (V1)

L'IV Rank vrai nécessite un historique de l'IV sur 1 an, que yfinance ne fournit pas. Pour la V1, on utilise le ratio IV/HV comme proxy :

```python
def compute_iv_rank_proxy(
    iv_atm: float,
    hv_30d: float,
) -> float:
    """
    Proxy de l'IV Rank basé sur le ratio IV/HV.

    Interprétation :
    - IV/HV < 0.8 : vol implicite sous la réalisée → IV Rank très bas (~10-20)
    - IV/HV ~ 1.0 : équilibre → IV Rank moyen (~40-50)
    - IV/HV > 1.5 : vol implicite bien au-dessus → IV Rank élevé (~70-90)

    Mapping linéaire clippé :
    iv_rank_proxy = clip((iv_hv_ratio - 0.6) / (1.8 - 0.6) * 100, 0, 100)

    Ce mapping est approximatif mais suffisant pour le classement relatif.
    """
    ratio = iv_atm / max(hv_30d, 0.01)
    return max(0.0, min(100.0, (ratio - 0.6) / 1.2 * 100))
```

### 6.6 Calcul de la volatilité historique 30 jours

```python
def compute_hv_30d(symbol: str) -> float:
    """
    Volatilité historique réalisée sur 30 jours de trading.

    Algorithme :
    1. Récupérer l'historique de prix (close) sur 45 jours calendaires
       via yf.Ticker(symbol).history(period="3mo").
    2. Calculer les log-returns quotidiens.
    3. Écart-type des 30 derniers log-returns.
    4. Annualiser : HV = std * sqrt(252).
    """
```

### 6.7 Calcul de la liquidité options

```python
def compute_options_liquidity(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    spot: float,
    atm_range_pct: float = 0.02,
) -> dict:
    """
    Calcule les métriques de liquidité sur les options ATM (±2% du spot).

    Filtre les options dont le strike est entre spot*(1-atm_range_pct) et
    spot*(1+atm_range_pct).

    Retourne :
    - avg_spread_pct : moyenne de (ask - bid) / mid pour les options ATM
    - avg_volume : volume moyen
    - avg_oi : open interest moyen
    - strike_count : nombre de strikes dans ±10% du spot
    """
```

### 6.8 Boucle d'analyse avec rate limiting

```python
import time

def analyze_options_detailed(
    candidates: list[dict],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
    request_delay: float = 0.5,
    progress_callback: Callable | None = None,
) -> list[OptionsMetrics]:
    """
    Analyse détaillée des options pour chaque candidat.

    Rate limiting :
    - Pause de request_delay secondes entre chaque ticker
    - Chaque ticker nécessite ~2 requêtes API
      (option_chain near + option_chain far + history)
    - Pour 50 tickers à 0.5s de délai : ~75 secondes
    - yfinance cache certaines requêtes automatiquement

    En cas d'erreur sur un ticker :
    - Log l'erreur
    - Skip le ticker (ne pas interrompre le screening)
    - Continuer avec le suivant

    Retourne:
    ---------
    list[OptionsMetrics] pour les tickers analysés avec succès.
    """
    results = []

    for i, candidate in enumerate(candidates):
        if progress_callback:
            pct = 0.30 + 0.65 * (i / len(candidates))
            progress_callback(
                f"Analyse options de {candidate['symbol']} ({i+1}/{len(candidates)})...",
                pct
            )

        try:
            ticker = yf.Ticker(candidate["symbol"])
            spot = candidate["price"]

            # Sélectionner les expirations
            available = [date.fromisoformat(e) for e in ticker.options]
            expiry_pair = select_expirations(available, near_range, far_range)
            if expiry_pair is None:
                continue  # pas assez d'expirations → skip

            near_exp, far_exp = expiry_pair

            # Chaînes d'options
            chain_near = ticker.option_chain(near_exp.isoformat())
            chain_far = ticker.option_chain(far_exp.isoformat())

            # Calculs
            iv_near = compute_iv_atm(chain_near.calls.join(chain_near.puts, rsuffix='_p'), spot)
            iv_far = compute_iv_atm(chain_far.calls.join(chain_far.puts, rsuffix='_p'), spot)

            if iv_near <= 0 or iv_far <= 0:
                continue  # données IV manquantes

            hv = compute_hv_30d(candidate["symbol"])

            liq_near = compute_options_liquidity(chain_near.calls, chain_near.puts, spot)
            liq_far = compute_options_liquidity(chain_far.calls, chain_far.puts, spot)

            metrics = OptionsMetrics(
                symbol=candidate["symbol"],
                near_expiry=near_exp,
                far_expiry=far_exp,
                iv_atm_near=iv_near,
                iv_atm_far=iv_far,
                term_structure_ratio=iv_near / iv_far,
                hv_30d=hv,
                iv_hv_ratio=iv_far / max(hv, 0.01),
                iv_rank_proxy=compute_iv_rank_proxy(iv_far, hv),
                avg_bid_ask_spread_pct=(liq_near["avg_spread_pct"] + liq_far["avg_spread_pct"]) / 2,
                avg_volume_near=liq_near["avg_volume"],
                avg_volume_far=liq_far["avg_volume"],
                avg_oi_near=liq_near["avg_oi"],
                avg_oi_far=liq_far["avg_oi"],
                strike_count_near=liq_near["strike_count"],
                strike_count_far=liq_far["strike_count"],
                weekly_count=sum(
                    1 for e in available
                    if near_range[0] <= (e - date.today()).days <= near_range[1]
                ),
            )

            results.append(metrics)

        except Exception as e:
            # Log mais ne pas interrompre
            import logging
            logging.warning(f"Screener: skip {candidate['symbol']}: {e}")
            continue

        time.sleep(request_delay)  # rate limiting

    if progress_callback:
        progress_callback(f"{len(results)} tickers analysés avec succès", 0.95)

    return results
```

---

## 7. Scoring et classement

### 7.1 Filtres éliminatoires (appliqués avant le scoring)

Un ticker est disqualifié si au moins un de ces critères est vrai :

```python
DISQUALIFICATION_RULES = {
    "spread_too_wide":
        lambda m: m.avg_bid_ask_spread_pct > 0.10,
        # Spread moyen > 10% du mid → illiquide

    "no_volume":
        lambda m: (m.avg_volume_near + m.avg_volume_far) / 2 < 100,
        # Volume moyen < 100 contrats/jour → pas de marché

    "no_open_interest":
        lambda m: (m.avg_oi_near + m.avg_oi_far) / 2 < 500,
        # Open interest moyen < 500 → positions insuffisantes

    "not_enough_strikes":
        lambda m: min(m.strike_count_near, m.strike_count_far) < 10,
        # Moins de 10 strikes dans ±10% → granularité insuffisante

    "iv_data_missing":
        lambda m: m.iv_atm_near <= 0 or m.iv_atm_far <= 0,
        # Données IV manquantes → impossible de scorer
}
```

### 7.2 Score composite

```python
def compute_score(
    metrics: OptionsMetrics,
    has_ex_div_in_window: bool,
) -> float:
    """
    Score composite entre 0 et 100.

    Composantes (toutes normalisées 0-1 avant pondération) :

    1. IV Rank proxy (poids 0.35)
       - Optimal à ~45%
       - score_iv = 1.0 - abs(iv_rank_proxy - 45) / 55
       - Clippé [0, 1]

    2. Term structure (poids 0.30)
       - Optimal quand ratio ≈ 0.90-1.00 (léger contango)
       - Pire quand ratio > 1.15 (backwardation forte)
       - score_ts = 1.0 si ratio <= 1.00
       - score_ts décroît linéairement de 1.0 à 0.0 entre 1.00 et 1.30
       - score_ts = 0.8 si ratio < 0.80 (contango trop fort = aussi suspect)

    3. Liquidité options (poids 0.20)
       - Combinaison de volume, OI, et spread inversé
       - score_liq = (
             0.3 * min(avg_volume / 2000, 1.0)
           + 0.3 * min(avg_oi / 5000, 1.0)
           + 0.4 * max(1.0 - avg_spread_pct / 0.10, 0.0)
         )

    4. Densité strikes + weeklies (poids 0.15)
       - score_density = (
             0.6 * min(avg_strike_count / 30, 1.0)
           + 0.4 * min(weekly_count / 4, 1.0)
         )

    Pénalités multiplicatives :
    - × 0.3  si has_ex_div_in_window
    - × 0.5  si iv_rank_proxy > 70  (risque IV crush)
    - × 0.7  si term_structure_ratio > 1.15  (backwardation)

    Score final = sum(composantes pondérées) × pénalités × 100
    """

    # --- Composante 1 : IV Rank ---
    score_iv = max(0.0, 1.0 - abs(metrics.iv_rank_proxy - 45) / 55)

    # --- Composante 2 : Term structure ---
    r = metrics.term_structure_ratio
    if r <= 1.00:
        score_ts = 1.0 if r >= 0.80 else 0.8
    else:
        score_ts = max(0.0, 1.0 - (r - 1.00) / 0.30)

    # --- Composante 3 : Liquidité ---
    avg_vol = (metrics.avg_volume_near + metrics.avg_volume_far) / 2
    avg_oi = (metrics.avg_oi_near + metrics.avg_oi_far) / 2
    score_liq = (
        0.3 * min(avg_vol / 2000, 1.0)
      + 0.3 * min(avg_oi / 5000, 1.0)
      + 0.4 * max(1.0 - metrics.avg_bid_ask_spread_pct / 0.10, 0.0)
    )

    # --- Composante 4 : Densité ---
    avg_strikes = (metrics.strike_count_near + metrics.strike_count_far) / 2
    score_density = (
        0.6 * min(avg_strikes / 30, 1.0)
      + 0.4 * min(metrics.weekly_count / 4, 1.0)
    )

    # --- Pondération ---
    raw_score = (
        0.35 * score_iv
      + 0.30 * score_ts
      + 0.20 * score_liq
      + 0.15 * score_density
    )

    # --- Pénalités ---
    penalty = 1.0
    if has_ex_div_in_window:
        penalty *= 0.3
    if metrics.iv_rank_proxy > 70:
        penalty *= 0.5
    if metrics.term_structure_ratio > 1.15:
        penalty *= 0.7

    return round(raw_score * penalty * 100, 1)
```

### 7.3 Classement final

```python
def rank_and_select(
    all_metrics: list[OptionsMetrics],
    event_flags: dict[str, bool],   # symbol → has_ex_div_in_window
    top_n: int,
) -> list[ScreenerResult]:
    """
    1. Appliquer les filtres éliminatoires (section 7.1).
    2. Calculer le score pour chaque ticker qualifié.
    3. Trier par score décroissant.
    4. Retourner les top_n premiers.

    Pour les tickers éliminés, stocker disqualification_reason
    (pour affichage dans le panneau "Détails du screening").
    """
```

---

## 8. Intégration UI (Streamlit)

### 8.1 Composant sidebar

```python
# ui/components/sidebar.py (ajout au composant existant)

def render_screener_section():
    """
    Rendu du bloc screener dans la sidebar.
    Retourne le contenu du champ sous-jacent (str).
    """
    st.markdown("---")
    st.subheader("Screening automatique")

    top_n = st.selectbox(
        "Nombre de sous-jacents",
        options=list(range(1, 11)),
        index=4,  # défaut = 5
        key="screener_top_n",
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        run_screener = st.button(
            "🔍 Trouver les meilleurs sous-jacents",
            key="run_screener",
            use_container_width=True,
        )

    if run_screener:
        screener = UnderlyingScreener()
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        def progress_cb(msg: str, pct: float):
            progress_bar.progress(pct)
            status_text.text(msg)

        results = screener.screen(
            top_n=top_n,
            progress_callback=progress_cb,
        )

        progress_bar.progress(1.0)
        status_text.text(f"✓ {len(results)} sous-jacents trouvés")

        # Stocker les résultats dans session_state
        st.session_state["screener_results"] = results

    # Affichage des résultats (persistant via session_state)
    if "screener_results" in st.session_state:
        results = st.session_state["screener_results"]

        for i, r in enumerate(results):
            st.markdown(
                f"**{i+1}. {r.symbol}** — score {r.score:.0f}"
            )

        if st.button("Utiliser ces résultats", key="use_screener_results"):
            tickers = ",".join(r.symbol for r in results)
            st.session_state["symbols_input"] = tickers

        with st.expander("Détails du screening"):
            # Tableau détaillé des métriques
            import pandas as pd
            df = pd.DataFrame([{
                "Ticker": r.symbol,
                "Score": r.score,
                "IV Rank~": f"{r.iv_rank_proxy:.0f}",
                "Term Str.": f"{r.term_structure_ratio:.2f}",
                "Spread%": f"{r.avg_option_spread_pct:.1%}",
                "Vol Opts": f"{(r.avg_option_volume):.0f}",
                "Strikes": r.strike_count,
            } for r in results])
            st.dataframe(df, hide_index=True)
```

### 8.2 Modification du champ sous-jacent

```python
# Le champ sous-jacent utilise session_state pour être mis à jour
# par le screener :

default_symbols = st.session_state.get("symbols_input", "SPY")

symbols_str = st.text_input(
    "Sous-jacent(s)",
    value=default_symbols,
    key="symbols_input",
    help="Un ticker ou plusieurs séparés par des virgules. "
         "Utilisez le screening automatique ci-dessous pour "
         "trouver les meilleurs candidats.",
)

symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
```

### 8.3 Boucle du scanner sur plusieurs tickers

Quand le scanner principal est lancé avec plusieurs tickers :

```python
# ui/app.py (modification du flux principal)

if st.button("🔍 LANCER LE SCAN"):
    all_results = []

    for i, symbol in enumerate(symbols):
        st.markdown(f"### Scanning {symbol} ({i+1}/{len(symbols)})")

        # 1. Charger la chaîne d'options pour ce symbol
        chain = data_provider.get_options_chain(symbol)

        # 2. Générer les combinaisons
        combos = combinator.generate_combinations(template, chain)

        # 3. Calcul GPU
        pnl = engine.compute_pnl_batch_gpu(combos_tensor, ...)

        # 4. Filtrer et scorer
        filtered = scorer.filter_and_score(pnl, criteria)

        # 5. Accumuler les résultats avec le symbol source
        for result in filtered:
            result.underlying_symbol = symbol
            all_results.append(result)

    # 6. Trier tous les résultats cross-tickers par score global
    all_results.sort(key=lambda r: r.score, reverse=True)

    # 7. Afficher
    display_results(all_results)
```

---

## 9. Structure du module

```
screener/
├── __init__.py
├── universe.py               # Liste statique des tickers candidats (UNIVERSE)
├── stock_filter.py            # Étape 2 : filtre prix/volume (filter_stocks_fast)
├── event_filter.py            # Étape 3 : filtre earnings/dividendes (filter_events)
├── options_analyzer.py        # Étape 4 : analyse détaillée (analyze_options_detailed)
│                               #   Inclut : compute_iv_atm, compute_hv_30d,
│                               #            compute_options_liquidity,
│                               #            select_expirations
├── scorer.py                  # Scoring + classement (compute_score, rank_and_select)
├── screener.py                # Orchestrateur (UnderlyingScreener.screen)
└── models.py                  # Dataclasses (ScreenerResult, OptionsMetrics)
```

---

## 10. Configuration (ajout à config.py)

```python
# ── Screener ──
SCREENER_MIN_PRICE = 50.0
SCREENER_MIN_AVG_VOLUME = 1_000_000
SCREENER_EARNINGS_BUFFER_DAYS = 2
SCREENER_REQUEST_DELAY = 0.5             # secondes entre chaque requête yfinance
SCREENER_DEFAULT_TOP_N = 5
SCREENER_NEAR_EXPIRY_RANGE = (5, 21)     # jours
SCREENER_FAR_EXPIRY_RANGE = (25, 70)     # jours

# Seuils éliminatoires
SCREENER_MAX_SPREAD_PCT = 0.10           # 10%
SCREENER_MIN_AVG_OPTION_VOLUME = 100
SCREENER_MIN_AVG_OPEN_INTEREST = 500
SCREENER_MIN_STRIKE_COUNT = 10

# Scoring
SCREENER_SCORE_WEIGHT_IV_RANK = 0.35
SCREENER_SCORE_WEIGHT_TERM_STRUCTURE = 0.30
SCREENER_SCORE_WEIGHT_LIQUIDITY = 0.20
SCREENER_SCORE_WEIGHT_DENSITY = 0.15

SCREENER_PENALTY_EX_DIV = 0.3
SCREENER_PENALTY_HIGH_IV_RANK = 0.5      # appliqué si IV Rank > 70
SCREENER_PENALTY_BACKWARDATION = 0.7     # appliqué si term ratio > 1.15
```

---

## 11. Tests

Tous les tests tournent sans connexion réseau grâce à des fixtures mockées.

### 11.1 test_screener_scoring.py

```
Test 1 - Score IV Rank optimal :
  iv_rank_proxy=45 → score_iv = 1.0
  iv_rank_proxy=0  → score_iv ≈ 0.18
  iv_rank_proxy=100 → score_iv = 0.0

Test 2 - Score term structure :
  ratio=0.95 → score_ts = 1.0 (contango normal)
  ratio=1.00 → score_ts = 1.0
  ratio=1.15 → score_ts = 0.5
  ratio=1.30 → score_ts = 0.0 (backwardation forte)

Test 3 - Pénalités :
  ticker avec ex-div + IV Rank 80 → penalty = 0.3 × 0.5 = 0.15
  ticker clean → penalty = 1.0

Test 4 - Classement :
  Donner 5 métriques fictives avec des scores connus
  Vérifier que rank_and_select retourne le bon ordre
```

### 11.2 test_screener_filters.py

```
Test 5 - Filtre éliminatoire spread :
  spread=0.15 → disqualifié, reason="spread_too_wide"
  spread=0.05 → qualifié

Test 6 - Filtre earnings :
  earnings dans 10 jours, near_range=(5,21) → éliminé
  earnings dans 80 jours, far_range=(25,70) → conservé

Test 7 - Sélection expirations :
  Expirations disponibles = [5j, 12j, 19j, 26j, 33j, 47j]
  near_range=(5,21), far_range=(25,70)
  → near=12j (milieu de 5-21), far=33j (milieu de 25-70)
```

### 11.3 test_screener_integration.py

```
Test 8 - Pipeline complet avec données mockées :
  Mocker yfinance pour retourner des données fictives
  pour 10 tickers (3 avec earnings, 2 illiquides, 5 bons).
  Vérifier que screen(top_n=3) retourne 3 résultats
  triés par score décroissant, excluant les earnings/illiquides.
```

---

## 12. Feuille de route du module

### V1 (livré avec le MVP)
- Univers statique (~130 tickers)
- IV Rank proxy via ratio IV/HV
- Scoring et classement
- Intégration UI (bouton + injection tickers)
- Tests avec mocks

### V2
- IV Rank réel via stockage SQLite quotidien de l'IV ATM
- Univers dynamique (scraping "most active options" CBOE)
- Cache des résultats de screening (valide 1 heure)
- Affichage d'un mini-graphique term structure dans les détails

### V3
- Screening en tâche de fond (rafraîchissement automatique)
- Alertes quand un nouveau sous-jacent entre dans le top 5
- Intégration de données IBKR pour des IV plus précises
