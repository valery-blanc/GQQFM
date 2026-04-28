# Options P&L Profile Scanner — Spécifications Techniques

> Version : FEAT-016 (2026-04-28)

## 1. Vue d'ensemble

### 1.1 Objectif

Logiciel de scan d'options qui, pour un ou plusieurs sous-jacents donnés, recherche automatiquement des combinaisons d'options dont le profil de P&L à la clôture correspond à des critères cibles définis par l'utilisateur. Le cas d'usage principal est la recherche de profils "smile" (pertes capées, probabilité de perte faible, potentiel de gros gains aux extrêmes). Le nombre de legs par combinaison est variable (2 à 4) selon le template.

### 1.2 Exemple de référence

Sous-jacent : S&P 500 (spot ~2943.68)
Ouverture : 08/07/2019, clôture : 19/07/2019 (expiration des options courtes)

| Leg | Type | Quantité | Strike | Expiration | Direction |
|-----|------|----------|--------|------------|-----------|
| 1 | Call | 3 | ~3031 (OTM) | 16 Aug 2019 | Achat (long) |
| 2 | Put | 3 | ~2837 (OTM) | 16 Aug 2019 | Achat (long) |
| 3 | Call | 2 | ~2970 (légèrement OTM) | 19 Jul 2019 | Vente (short) |
| 4 | Put | 2 | ~2900 (légèrement OTM) | 19 Jul 2019 | Vente (short) |

Profil résultant : courbe en U, perte max ~-6%, ~72% de probabilité de profit, gains >90% aux extrêmes.

### 1.3 Contraintes techniques

- **GPU** : NVIDIA RTX 5070 Ti (16 Go GDDR7, architecture Blackwell, ~8960 CUDA cores)
- **OS** : Windows (avec CUDA toolkit)
- **Langage** : Python 3.11+
- **Accélération GPU** : CuPy (drop-in NumPy sur CUDA)

---

## 2. Architecture

### 2.1 Diagramme des modules

```
┌──────────────────────────────────────────────────────────┐
│                      UI (Streamlit)                       │
│  Saisie sous-jacent, critères, affichage résultats/P&L   │
└──────────────┬───────────────────────────┬───────────────┘
               │                           │
     ┌─────────▼─────────┐      ┌─────────▼──────────┐
     │  Data Provider     │      │  Visualisation     │
     │  (chaînes options) │      │  (Plotly charts)   │
     └─────────┬─────────┘      └────────────────────┘
               │
     ┌─────────▼─────────┐
     │  Combinator        │    ← CPU : génère les combinaisons
     │  (templates)       │      par template de stratégie
     └─────────┬─────────┘
               │  tenseurs de combinaisons
     ┌─────────▼──────────────────────────┐
     │  GPU Engine (CuPy)                  │
     │  ┌─────────────┐ ┌───────────────┐ │
     │  │ BS Pricer    │ │ P&L Computer  │ │
     │  │ (vectorisé)  │ │ (batch)       │ │
     │  └─────────────┘ └───────────────┘ │
     │  ┌─────────────────────────────┐   │
     │  │ Scorer / Filter             │   │
     │  │ (critères P&L sur GPU)      │   │
     │  └─────────────────────────────┘   │
     └────────────────────────────────────┘
```

### 2.2 Pipeline de traitement

```
1. Charger chaîne d'options du sous-jacent        [CPU, I/O]
2. Générer combinaisons par template               [CPU]
3. Transférer vers GPU en batch                    [CPU→GPU]
4. Calculer P&L pour toutes les combinaisons       [GPU, massivement parallèle]
5. Scorer et filtrer                               [GPU]
6. Transférer les résultats filtrés                [GPU→CPU]
7. Trier et afficher                               [CPU]
```

---

## 3. Module Data Provider

### 3.1 Source de données

Utiliser l'API Yahoo Finance via le package `yfinance` pour le prototype. L'interface doit être abstraite pour permettre d'autres sources (IBKR, Tradier, Polygon.io) ultérieurement.

### 3.2 Interface

```python
@dataclass
class OptionContract:
    """Un contrat d'option individuel."""
    contract_symbol: str       # ex: "MSFT240816C00490000"
    option_type: str           # "call" ou "put"
    strike: float              # prix d'exercice
    expiration: date           # date d'expiration
    bid: float                 # prix bid
    ask: float                 # prix ask
    mid: float                 # (bid + ask) / 2
    implied_vol: float         # volatilité implicite (en décimal, ex: 0.25)
    volume: int                # volume du jour
    open_interest: int         # open interest
    delta: float | None        # grec delta (si disponible)

@dataclass
class OptionsChain:
    """Chaîne d'options complète pour un sous-jacent."""
    underlying_symbol: str     # ex: "MSFT"
    underlying_price: float    # prix spot actuel
    contracts: list[OptionContract]
    expirations: list[date]    # expirations disponibles
    strikes: list[float]       # strikes disponibles
    fetch_timestamp: datetime

class DataProvider(Protocol):
    """Interface abstraite pour les fournisseurs de données."""

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
        """Récupère la chaîne d'options filtrée."""
        ...

    def get_risk_free_rate(self) -> float:
        """Taux sans risque actuel.
        V1 (MVP) : constante par défaut = 0.045 (4.5%), définie dans config.py.
        L'impact sur le P&L d'options < 90 jours est négligeable.
        Modifiable par l'utilisateur dans la sidebar (section avancée, pliée).
        V2 : fetch ^IRX (T-bill 13 semaines) via yfinance.
        """
        ...
```

### 3.3 Filtrage initial des données

Avant de passer au Combinator, filtrer les options pour réduire le bruit :
- Exclure les options avec bid = 0 **ET** lastPrice = 0 (pas de marché du tout)
- Exclure les options avec spread bid-ask > 20% du mid (trop illiquides)
- Exclure les options avec open_interest < 10 **ET** volume < 10 (OR, pas AND)
- Limiter les expirations à celles entre 2 jours et 90 jours dans le futur
- Limiter les strikes à ±20% du spot

**Re-pricing hors-séance (BUG-003) :** quand toutes les options d'une expiration ont bid=ask=0 :
1. **Calcul IV consensus** depuis les options OTM (moins sensibles aux mouvements de spot).
   On prend la médiane des IV (bisection depuis lastPrice) parmi celles dans [0.05, 1.5].
2. **Re-pricing BS** : on utilise Black-Scholes avec IV_consensus et spot_courant pour
   calculer le prix mid de TOUTES les options de cette expiration.
   Les `lastPrice` ITM stales (ex: AAPL $245c à $10.80 quand AAPL était $256, maintenant $252)
   sont ainsi corrigés par rapport au spot courant.
3. Fallback si IV consensus non disponible : utiliser lastPrice + bisection IV.
4. Si IV bisection < 0.01 en fallback : exclure le contrat.

Les prix hors-séance restent indicatifs. Les résultats peuvent différer des prix live.

---

## 4. Module Combinator

### 4.1 Principe : recherche par templates

Plutôt que d'énumérer toutes les combinaisons possibles (explosion combinatoire), on définit des templates de stratégies dont le profil P&L peut correspondre aux critères recherchés. Pour chaque template, on fait varier les paramètres (strikes, expirations, quantités). Le nombre de legs est **variable** selon le template (2 à 4 legs).

### 4.2 Structure d'un template

```python
@dataclass
class Leg:
    """Définition d'un leg dans une combinaison."""
    option_type: str           # "call" ou "put"
    direction: int             # +1 = achat (long), -1 = vente (short)
    quantity: int              # nombre de contrats
    strike: float              # prix d'exercice
    expiration: date           # date d'expiration
    entry_price: float         # prix d'entrée (mid price, BS-repricé hors-séance)
    implied_vol: float         # vol implicite à l'entrée
    contract_symbol: str       # symbole yfinance (ex: "AAPL240816C00490000")
    volume: int
    open_interest: int

@dataclass
class Combination:
    """Une combinaison de 2 à 4 legs."""
    legs: list[Leg]            # 2 à 4 legs selon le template
    net_debit: float           # débit net (coût d'entrée), EN DOLLARS, multiplicateur ×100 INCLUS
                               # Formule : net_debit = Σ (direction × quantity × entry_price × 100)
                               # TOUJOURS > 0 (les crédits nets sont filtrés par le Combinator).
                               # Le capital engagé = net_debit.
    close_date: date           # date de clôture = min(expiration des legs short)
                               # Déterminée AUTOMATIQUEMENT par le Combinator.
    template_name: str         # nom du template source
    event_score_factor: float = 1.0              # multiplicateur événementiel (FEAT-005)
                               # > 1.0 si événement favorable en sweet zone
                               # < 1.0 si événement MODERATE en danger zone
                               # Paires avec CRITICAL en danger zone exclues par le Combinator (FEAT-006)
    events_in_sweet_zone: list[str] = []         # noms des événements favorables (sweet zone)
    event_warning: str | None = None             # FEAT-006 : warning si CRITICAL en danger zone
                               # ou near expiry très court — affiché dans combo_detail + chart

@dataclass
class TemplateDefinition:
    """Définition d'un template de stratégie."""
    name: str
    description: str
    legs_spec: list[LegSpec]            # spécification de chaque leg
    constraints: list[Callable]         # contraintes inter-legs
    use_adjacent_expiry_pairs: bool = False
    # Si True : le Combinator itère sur TOUTES les paires (NEAR, FAR) dont l'écart
    # est entre 5 et 45 jours. Utile pour les diagonales à expirations proches.
    # Si False (templates 1-3) :
    #   - Sans event_calendar : utilise expirations[0] (NEAR) et expirations[-1] (FAR).
    #   - Avec event_calendar : sélectionne les top-3 paires par event_score_factor
    #     parmi near ∈ SCANNER_NEAR_EXPIRY_RANGE et far ∈ SCANNER_FAR_EXPIRY_RANGE.
    #     Paires avec CRITICAL en danger zone exclues (has_critical_in_danger=True).
    #     Fallback sur (expirations[0], expirations[-1]) si aucune paire éligible.

@dataclass
class LegSpec:
    """Spécification d'un leg dans un template."""
    option_type: str                    # "call" ou "put"
    direction: int                      # +1 ou -1
    quantity_range: range               # ex: range(1, 6) pour 1 à 5 contrats
    strike_range: tuple[float, float]   # (min_factor, max_factor) × spot
    strike_step: float                  # pas de variation (facteur, non utilisé par le combinator
                                        # actuel mais conservé pour la spec)
    expiry_selector: str                # "NEAR" ou "FAR"
```

### 4.3 Templates à implémenter (V1)

#### Template 1 : Calendar Strangle (cas de référence)

```
Nom : "calendar_strangle"
Description : Achat de straddle/strangle long terme + vente de straddle/strangle court terme

Leg 1 (Long Call Far) :
  - type: call, direction: +1
  - quantity: 1 à 5
  - strike: OTM (spot × [1.01 à 1.10] par pas de 0.005)
  - expiry: la plus lointaine disponible parmi les expirations sélectionnées

Leg 2 (Long Put Far) :
  - type: put, direction: +1
  - quantity: 1 à 5
  - strike: OTM (spot × [0.90 à 0.99] par pas de 0.005)
  - expiry: même expiration que Leg 1

Leg 3 (Short Call Near) :
  - type: call, direction: -1
  - quantity: 1 à quantity(Leg 1)   ← ne peut pas vendre plus qu'on n'achète
  - strike: OTM mais plus proche du spot que Leg 1 (spot × [1.005 à 1.05])
  - expiry: la plus proche disponible (et < expiry Leg 1)

Leg 4 (Short Put Near) :
  - type: put, direction: -1
  - quantity: 1 à quantity(Leg 2)   ← ne peut pas vendre plus qu'on n'achète
  - strike: OTM mais plus proche du spot que Leg 2 (spot × [0.95 à 0.995])
  - expiry: même expiration que Leg 3

Contraintes :
  - strike(Leg 2) < strike(Leg 4) < spot < strike(Leg 3) < strike(Leg 1)
  - expiry(Leg 3) = expiry(Leg 4) < expiry(Leg 1) = expiry(Leg 2)
  - quantity(Leg 3) <= quantity(Leg 1)
  - quantity(Leg 4) <= quantity(Leg 2)
  - net_debit > 0 (la position coûte de l'argent à l'ouverture, pas de crédit net)
```

#### Template 2 : Double Calendar Spread

```
Nom : "double_calendar"
Description : Calendar spread sur les calls + calendar spread sur les puts

Leg 1 (Long Call Far) :
  - type: call, direction: +1
  - quantity: 1 à 5
  - strike: K_call (variable)
  - expiry: FAR

Leg 2 (Short Call Near) :
  - type: call, direction: -1
  - quantity: même que Leg 1
  - strike: K_call (même strike que Leg 1)
  - expiry: NEAR

Leg 3 (Long Put Far) :
  - type: put, direction: +1
  - quantity: 1 à 5
  - strike: K_put (variable, K_put < K_call)
  - expiry: FAR

Leg 4 (Short Put Near) :
  - type: put, direction: -1
  - quantity: même que Leg 3
  - strike: K_put (même strike que Leg 3)
  - expiry: NEAR

Contraintes :
  - K_put < spot < K_call
  - expiry(NEAR) < expiry(FAR)
```

#### Template 3 : Reverse Iron Condor (variante calendar)

```
Nom : "reverse_iron_condor_calendar"
Description : Iron condor inversé avec expirations différentes

Leg 1 (Long Call Far) :
  - type: call, direction: +1
  - strike: K1 (OTM near, spot × [1.005 à 1.03])
  - expiry: FAR

Leg 2 (Long Put Far) :
  - type: put, direction: +1
  - strike: K2 (OTM near, spot × [0.97 à 0.995])
  - expiry: FAR

Leg 3 (Short Call Near) :
  - type: call, direction: -1
  - strike: K3 (OTM far, K3 > K1)
  - expiry: NEAR

Leg 4 (Short Put Near) :
  - type: put, direction: -1
  - strike: K4 (OTM far, K4 < K2)
  - expiry: NEAR

Contraintes :
  - K4 < K2 < spot < K1 < K3
  - quantity(short) <= quantity(long) par type
```

#### Template 4 : Call Diagonal Backspread (FEAT-001)

```
Nom : "call_diagonal_backspread"
Description : Vente N calls NEAR + achat N+1 calls FAR OTM

Leg 1 (Short Call Near) :
  - type: call, direction: -1
  - quantity: 1 à 5
  - strike: spot × [0.97 à 1.05]
  - expiry: NEAR

Leg 2 (Long Call Far) :
  - type: call, direction: +1
  - quantity: 2 à 6  (= short_qty + 1, vérifié en contrainte)
  - strike: spot × [1.00 à 1.10]  (> NEAR strike)
  - expiry: FAR

Contraintes :
  - short_call.strike < long_call.strike
  - long_call.quantity > short_call.quantity
  - short_call.expiration < long_call.expiration
  - use_adjacent_expiry_pairs=True (paires 5–45 jours)
```

#### Template 5 : Call Ratio Diagonal (FEAT-001)

```
Nom : "call_ratio_diagonal"
Description : Vente N calls NEAR + achat N calls FAR + achat 1 call FAR OTM

Leg 1 (Short Call Near) :
  - type: call, direction: -1
  - quantity: 1 à 5
  - strike: spot × [0.97 à 1.05]
  - expiry: NEAR

Leg 2 (Long Call Far principal) :
  - type: call, direction: +1
  - quantity: 1 à 5  (= short_qty, vérifié en contrainte)
  - strike: spot × [1.00 à 1.08]
  - expiry: FAR

Leg 3 (Long Call Far OTM supplémentaire) :
  - type: call, direction: +1
  - quantity: toujours 1
  - strike: spot × [1.02 à 1.12]  (> Leg 2 strike)
  - expiry: FAR

Contraintes :
  - short.strike < main_long.strike < otm_long.strike
  - main_long.expiration == otm_long.expiration (même FAR)
  - short.expiration < main_long.expiration
  - short.quantity == main_long.quantity
  - otm_long.quantity == 1
  - use_adjacent_expiry_pairs=True (paires 5–45 jours)
```

### 4.4 Génération des combinaisons

```python
def generate_combinations(
    template: TemplateDefinition,
    chain: OptionsChain,
    max_combinations: int = 500_000,
    max_iterations: int = 2_000_000,   # cap anti-blocage
) -> list[Combination]:
    """
    Génère toutes les combinaisons valides pour un template donné.

    Algorithme :
    1. Si template.use_adjacent_expiry_pairs : construire la liste de toutes
       les paires (NEAR, FAR) séparées de 5 à 45 jours.
       Sinon : utiliser (expirations[0], expirations[-1]).
    2. Pour chaque paire d'expirations :
       a. Pour chaque leg_spec, identifier les contrats candidats.
       b. Générer le produit cartésien des candidats.
       c. Pour chaque sélection : construire les legs, vérifier les contraintes,
          calculer net_debit (>0 requis), ajouter à all_combos.
       d. Stopper si max_iterations atteint (par paire) ou max_combinations total.
    3. Retourne la liste des Combination valides.
    """
```

**Règle : net_debit > 0 obligatoire** — les positions en crédit net sont exclues
(le capital engagé est le débit net × 100).

**Règle : max_iterations** — protège contre les templates à contraintes sélectives
(ex: double_calendar avec même strike) qui nécessiteraient des milliards d'itérations.

### 4.5 Estimation de la taille de l'espace de recherche

Pour un sous-jacent typique avec ~15 strikes utilisables × 3 expirations :
- Leg 1 : ~8 strikes × 1 expiry × 5 qty = 40 candidats
- Leg 2 : ~8 strikes × 1 expiry × 5 qty = 40 candidats
- Leg 3 : ~5 strikes × 1 expiry × 5 qty = 25 candidats
- Leg 4 : ~5 strikes × 1 expiry × 5 qty = 25 candidats
- Produit brut : 40 × 40 × 25 × 25 = 1 000 000
- Après contraintes : ~100 000 à 500 000 combinaisons (gérable par le GPU)

---

## 5. Module GPU Engine

### 5.1 Technologie : CuPy avec fallback NumPy

CuPy est un drop-in replacement de NumPy qui exécute les opérations sur GPU via CUDA. On l'utilise car :
- API identique à NumPy (courbe d'apprentissage quasi nulle)
- Supporte les kernels CUDA custom si nécessaire
- Compatible RTX 5070 Ti (CUDA compute capability 12.x, Blackwell)
- Gère automatiquement la mémoire GPU

Installation : `pip install cupy-cuda12x`

**Backend abstraction (engine/backend.py) :**

Le code moteur ne doit JAMAIS importer directement `cupy` ou `numpy`.
Tout passe par un module `backend.py` qui expose un namespace unifié `xp` :

```python
# engine/backend.py
"""
Backend abstraction : GPU (CuPy) si disponible, sinon CPU (NumPy).
Tout le code moteur importe `xp` et `ndtr` depuis ce module.
"""
try:
    import cupy as xp
    from cupyx.scipy.special import ndtr
    GPU_AVAILABLE = True
except ImportError:
    import numpy as xp
    from scipy.stats import norm
    ndtr = norm.cdf    # fallback CPU pour la CDF normale
    GPU_AVAILABLE = False

def to_cpu(arr):
    """Convertit un array GPU en NumPy CPU (no-op si déjà NumPy)."""
    if GPU_AVAILABLE:
        return arr.get()
    return arr

def get_device_info() -> dict:
    """Retourne les infos GPU pour l'UI, ou None si pas de GPU."""
    if not GPU_AVAILABLE:
        return None
    props = xp.cuda.runtime.getDeviceProperties(0)
    mem = xp.cuda.runtime.memGetInfo()
    return {
        "name": props["name"].decode(),
        "vram_total_gb": props["totalGlobalMem"] / 1024**3,
        "vram_free_gb": mem[0] / 1024**3,
    }
```

**Règle pour les tests :**
- Tous les tests unitaires (test_black_scholes.py, test_pnl.py, etc.)
  DOIVENT tourner sans GPU (backend NumPy). Pas de skip si GPU absent.
- Un fichier test_gpu.py optionnel (marqué `@pytest.mark.gpu`) vérifie
  la cohérence des résultats GPU vs CPU (tolérance 1e-5).

### 5.2 Pricing américain Bjerksund-Stensland 1993 (FEAT-008)

Le pricer utilise l'approximation analytique de Bjerksund-Stensland 1993 pour
les options américaines, qui tient compte de la prime d'exercice anticipé :

- **Calls sans dividende (q ≈ 0)** : retourne le prix Black-Scholes européen
  (exercice anticipé jamais optimal sans dividende).
- **Calls avec dividende (q > 0)** : approximation B-S 1993 avec frontière
  d'exercice plate et 6 appels à la fonction φ auxiliaire.
- **Puts** : transformation put-call P(S,K,T,r,q,σ) = C(K,S,T,q,r,σ).
- **Puts à r ≈ 0** (BUG-006) : retourne le put européen. Quand le taux
  sans risque tend vers zéro, la transformation BS-1993 dégénère
  (β → 1.0 exactement). Théorie : un put américain à taux nul n'a aucune
  prime d'exercice anticipé (exercer maintenant donne K, mais K placé à
  r=0 ne produit aucun intérêt) — le fallback est donc *exact*.
- **Plancher** : max(valeur américaine, valeur intrinsèque), puis
  garde-fou `isfinite` final (NaN éventuel d'overflow float32 → intrinsèque).

Le rendement de dividende continu (`div_yield`) est récupéré automatiquement
depuis Yahoo Finance (`ticker.info["dividendYield"]`) et propagé dans chaque
Leg puis dans le tenseur GPU.

```python
def bs_american_price(
    option_type, spot, strike, time_to_expiry, vol, rate, div_yield
) -> xp.ndarray:
    """Prix américain B-S 1993, entièrement vectorisé GPU."""
```

Le pricing européen classique (`bs_price`) reste disponible comme référence
mais n'est plus utilisé dans le pipeline P&L principal.

### 5.2.1 Black-Scholes européen (référence)

```python
def bs_price(option_type, spot, strike, time_to_expiry, vol, rate) -> xp.ndarray:
    """Black-Scholes européen vectorisé. Utilisé comme fallback pour calls sans dividende."""
```

### 5.3 Calcul P&L batch sur GPU

C'est le coeur du moteur. On calcule le P&L de TOUTES les combinaisons simultanément.

```python
def compute_pnl_batch_gpu(
    combinations_tensor: dict,
    spot_range: xp.ndarray,
    vol_scenarios: list[float],
    risk_free_rate: float,
) -> xp.ndarray:
    """
    Calcul du P&L pour toutes les combinaisons sur une grille de spots.

    NOTE sur close_date :
    ---------------------
    La close_date n'est PAS un paramètre de cette fonction.
    Elle est déjà intégrée dans le tenseur "time_to_expiry_at_close"
    qui est pré-calculé par le Combinator pour chaque combinaison.

    Pour chaque combinaison, close_date = min(expiration des legs short).
    Le Combinator calcule time_to_expiry_at_close[c, l] =
        max(0, (leg_expiry - close_date).days / 365)
    pour chaque leg l de chaque combinaison c.
    Cela vaut 0.0 pour les legs qui expirent à ou avant close_date
    (typiquement les legs short), et > 0 pour les legs long.

    Paramètres:
    -----------
    combinations_tensor : dict contenant des arrays xp (GPU ou CPU)
        Chaque tenseur a shape (C, L) avec padding zéro pour les legs manquants.
        L = max(len(c.legs) for c in combinations) — dynamique, pas hardcodé à 4.
        Typiquement L=4 pour calendar_strangle/double_calendar, L=2 pour les diagonales.

        - "option_types": xp.ndarray, shape (C, L), dtype int8
            0 = call, 1 = put pour chaque leg (0 sur les positions paddées)
        - "directions": xp.ndarray, shape (C, L), dtype int8
            +1 = long, -1 = short (0 sur les positions paddées)
        - "quantities": xp.ndarray, shape (C, L), dtype int16
        - "strikes": xp.ndarray, shape (C, L), dtype float32
        - "entry_prices": xp.ndarray, shape (C, L), dtype float32
            prix d'entrée (mid) de chaque leg, EN DOLLARS PAR ACTION
            (PAS multiplié par 100 ici, le ×100 est appliqué dans le calcul P&L)
        - "implied_vols": xp.ndarray, shape (C, L), dtype float32
        - "time_to_expiry_at_close": xp.ndarray, shape (C, L), dtype float32
            temps restant (en années) entre close_date et expiration de chaque leg
            = 0.0 si le leg expire à ou avant close_date (et pour les legs paddés)

        C = nombre de combinaisons
        L = nombre max de legs parmi les combinaisons du batch

    spot_range : xp.ndarray, shape (M,)
        Grille de prix du sous-jacent à simuler.
        Typiquement 200 points entre spot × 0.85 et spot × 1.15

    vol_scenarios : list[float]
        Scénarios de volatilité pour les options encore vivantes à close_date.
        Ex: [0.8, 1.0, 1.2] = vol implicite × [80%, 100%, 120%]
        Ces facteurs sont multiplicatifs par rapport à la vol implicite de
        chaque leg à l'entrée.
        Le scénario index 1 (facteur 1.0 = vol inchangée) est TOUJOURS
        le scénario de référence utilisé par le scorer et les filtres.
        Les scénarios 0 et 2 ne servent qu'à la bande d'incertitude
        dans la visualisation.

    risk_free_rate : float

    Retourne:
    ---------
    xp.ndarray, shape (V, C, M)
        V = nombre de scénarios de vol
        C = nombre de combinaisons
        M = nombre de points de la grille spot

        Chaque valeur = P&L total de la combinaison, exprimé en
        unités monétaires (pas en %).

    Algorithme:
    -----------
    Pour chaque scénario de vol v:
        Pour chaque point spot s dans spot_range (vectorisé):
            Pour chaque combinaison c (vectorisé):
                Pour chaque leg l de c (vectorisé):
                    Si time_to_expiry_at_close[c, l] <= 0:
                        # Option expirée à close_date -> valeur intrinsèque
                        value = intrinsic_value(type, spot, strike)
                    Sinon:
                        # Option encore vivante -> Black-Scholes
                        adjusted_vol = implied_vol[c, l] * vol_scenarios[v]
                        value = bs_price(type, spot, strike, tte, adjusted_vol, rate)

                    pnl_leg = direction[c, l] * quantity[c, l] * (value - entry_price[c, l]) * 100
                    # × 100 car un contrat = 100 actions

                pnl_total[v, c, s] = somme des pnl_leg pour les L legs
                    # Les legs paddés (direction=0, qty=0) contribuent 0 automatiquement
    """
```

### 5.4 Layout mémoire GPU

Objectif : maximiser la coalescence mémoire et le parallélisme.

```
Mémoire GPU estimée pour un batch:
- C = 500 000 combinaisons
- M = 200 points de spot
- V = 3 scénarios de vol
- L legs par combinaison (dynamique ; L = max legs du batch, typiquement 2 à 4)
  Exemple ci-dessous avec L=4 (cas le plus consommateur).

Tenseurs d'entrée (combinaisons):
  option_types:    500K × L × 1 byte  =   2 MB  (L=4)
  directions:      500K × L × 1 byte  =   2 MB
  quantities:      500K × L × 2 bytes =   4 MB
  strikes:         500K × L × 4 bytes =   8 MB
  entry_prices:    500K × L × 4 bytes =   8 MB
  implied_vols:    500K × L × 4 bytes =   8 MB
  tte_at_close:    500K × L × 4 bytes =   8 MB
  Total entrée:                        ~  40 MB

Tenseur de sortie (P&L):
  pnl: 3 × 500K × 200 × 4 bytes      = 1.2 GB

Tenseurs intermédiaires BS:
  ~4× la taille du résultat (d1, d2, call, put) = ~4.8 GB

Total estimé: ~6 GB → rentre dans les 16 GB de la 5070 Ti

Si dépassement mémoire: traiter en sous-batches de 100K combinaisons.
```

### 5.5 Stratégie de batching

```python
MAX_GPU_MEMORY_USAGE = 12 * 1024**3   # 12 GB max (sur 16 GB disponibles)
BYTES_PER_COMBO_PER_SPOT = 4 * 4      # 4 bytes × 4 tenseurs intermédiaires
SAFETY_FACTOR = 2.5                    # marge pour les allocations implicites CuPy

def compute_batch_size(
    num_spots: int,
    num_vol_scenarios: int,
    num_legs: int = 4,
) -> int:
    """Calcule le nombre max de combinaisons par batch GPU."""
    bytes_per_combo = (
        num_spots * num_vol_scenarios * num_legs * BYTES_PER_COMBO_PER_SPOT * SAFETY_FACTOR
    )
    return int(MAX_GPU_MEMORY_USAGE / bytes_per_combo)
```

---

## 6. Module Scorer / Filter

### 6.1 Critères de scoring

Tous les calculs de scoring sont effectués **sur GPU** (les données P&L y sont déjà).

```python
@dataclass
class ScoringCriteria:
    """Critères de sélection définis par l'utilisateur."""

    # Pertes capées
    max_loss_pct: float = -6.0
    # Perte maximale autorisée en % du capital engagé (net_debit).
    # On utilise le scénario de vol median.
    # Formule : max_loss_pct = min(pnl_curve) / net_debit × 100
    # Valeur négative. Ex: -6.0 signifie que la perte max est 6% du capital.

    # Probabilité de perte
    max_loss_probability_pct: float = 25.0
    # Pourcentage maximum de la distribution de probabilité du sous-jacent
    # où le P&L est négatif.
    # Calcul : intégrale de la densité log-normale sur les zones où pnl < 0
    # L'utilisateur entre une valeur entre 0 et 100.

    # Potentiel de gain
    min_max_gain_pct: float = 50.0
    # Gain maximal minimum (en % du capital engagé) que la stratégie doit offrir.
    # Calculé aux extrêmes de la grille de spot (±15% du spot).
    # Formule : max_gain_pct = max(pnl_curve) / net_debit × 100

    # Ratio gain/perte
    min_gain_loss_ratio: float = 5.0
    # Ratio minimum entre le gain max et la perte max (en valeur absolue).
    # Formule : |max_gain| / |max_loss|

    # Budget
    max_net_debit: float = 10000.0
    # Débit net maximum (coût d'entrée) en devise.

    # Liquidité
    min_avg_volume: int = 50
    # Volume moyen minimum sur les 4 legs.

    # Forme de la courbe (optionnel, V2)
    curve_shape: str = "smile"
    # "smile" = courbe en U (le cas principal)
    # "skew_up" = gains principalement à la hausse
    # "skew_down" = gains principalement à la baisse
```

### 6.2 Calcul de probabilité

La probabilité utilise une distribution log-normale implicite, calibrée sur la vol implicite ATM du sous-jacent.

```python
def compute_loss_probability_gpu(
    pnl_curve: xp.ndarray,      # shape (C, M) - P&L pour le scénario de vol median
    spot_range: xp.ndarray,      # shape (M,)
    current_spot: float,
    atm_vol: float,              # vol implicite ATM
    days_to_close: int,          # nombre de jours jusqu'à la clôture
    risk_free_rate: float,
) -> xp.ndarray:
    """
    Calcule la probabilité de perte pour chaque combinaison.

    Algorithme:
    1. Construire la densité de probabilité log-normale sur spot_range:
       mu = ln(current_spot) + (rate - 0.5 * vol^2) * T
       sigma = vol * sqrt(T)
       pdf(S) = lognormal(S, mu, sigma)

    2. Identifier les zones de perte: mask = (pnl_curve < 0)

    3. Intégrer numériquement (trapèzes) la densité sur les zones de perte:
       loss_prob[c] = integral(pdf(S) * mask[c, :], dS)

    Retourne: xp.ndarray, shape (C,) - probabilité de perte par combinaison [0.0 à 1.0]
    """
```

### 6.3 Pipeline de filtrage (GPU)

```python
def filter_combinations_gpu(
    pnl_tensor: xp.ndarray,       # shape (V, C, M)
    spot_range: xp.ndarray,       # shape (M,)
    net_debits: xp.ndarray,       # shape (C,)
    criteria: ScoringCriteria,
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
) -> xp.ndarray:
    """
    Filtre les combinaisons qui satisfont tous les critères.

    Retourne: xp.ndarray d'indices des combinaisons valides (shape variable).

    Étapes (toutes sur GPU) :
    1. Sélectionner le scénario de vol median : pnl_mid = pnl_tensor[1]  # index 1 = vol × 1.0
    2. Calculer max_loss par combinaison : min(pnl_mid, axis=1) / net_debits
    3. Calculer max_gain par combinaison : max(pnl_mid, axis=1) / net_debits
    4. Calculer loss_probability : compute_loss_probability_gpu(...)
    5. Calculer gain_loss_ratio : max_gain / |max_loss|
    6. Appliquer tous les filtres simultanément avec un masque booléen:
       mask = (
           (max_loss_pct >= criteria.max_loss_pct) &
           (loss_prob <= criteria.max_loss_probability_pct / 100) &
           (max_gain_pct >= criteria.min_max_gain_pct) &
           (gain_loss_ratio >= criteria.min_gain_loss_ratio) &
           (net_debits <= criteria.max_net_debit)
       )
    7. Retourner xp.where(mask)[0]
    """
```

### 6.4 Scoring pour le classement des résultats

Les combinaisons qui passent le filtre sont ensuite classées par un score composite.

```python
def score_combinations(
    pnl_mid: xp.ndarray,           # shape (C_filtered, M)
    net_debits: xp.ndarray,        # shape (C_filtered,)
    loss_probs: xp.ndarray,        # shape (C_filtered,)
    criteria: ScoringCriteria,
) -> xp.ndarray:
    """
    Score composite pour classer les combinaisons filtrées.

    Score = (w1 * normalized_gain_loss_ratio
           + w2 * (1 - normalized_loss_prob)
           + w3 * normalized_expected_return)
           × event_score_factor   ← FEAT-005 : multiplicateur événementiel

    Les poids par défaut : w1=0.4, w2=0.3, w3=0.3
    event_score_factor=1.0 si event_calendar non fourni (rétro-compatible).

    L'expected return est calculé comme l'espérance du P&L pondéré par la
    distribution log-normale du sous-jacent.

    Retourne: xp.ndarray, shape (C_filtered,) - scores entre 0 et 1.
    """
```

---

## 7. Module Visualisation

### 7.1 Graphique P&L principal

Reproduire un graphique similaire à la courbe de référence (courbe_PNL.png).

```python
def plot_pnl_profile(
    combination: Combination,
    pnl_tensor: np.ndarray,        # shape (V, M) - les 3 scénarios de vol
    spot_range: np.ndarray,        # shape (M,)
    current_spot: float,
    loss_prob: float,
    max_loss_pct: float,
    max_gain_pct: float,
) -> plotly.Figure:
    """
    Génère le graphique P&L interactif.

    Éléments à afficher :
    1. Courbe P&L principale (scénario vol median) - ligne épaisse
    2. Bande d'incertitude entre vol_low et vol_high - zone ombrée
    3. Ligne horizontale à P&L = 0 (breakeven)
    4. Points breakeven annotés (intersection courbe/zéro)
    5. Zone de perte colorée en rouge clair
    6. Zone de profit colorée en vert clair
    7. Annotations :
       - Perte max : valeur et % du capital
       - Gain max : valeur et % du capital
       - Probabilité de perte
       - Net debit (coût d'entrée)
    8. Axe X : prix du sous-jacent (et % de variation en haut)
    9. Axe Y gauche : P&L en % du capital engagé
    10. Axe Y droit : P&L en valeur absolue

    Utiliser Plotly pour l'interactivité (hover, zoom).
    """
```

### 7.2 Tableau comparatif des résultats

Affichage des N meilleures combinaisons dans un tableau Streamlit :

| Rang | Template | Legs (résumé) | Net Debit | Max Loss % | Loss Prob % | Max Gain % | Gain/Loss Ratio | Score |
|------|----------|---------------|-----------|------------|-------------|------------|-----------------|-------|

Chaque ligne est cliquable pour afficher le graphique P&L détaillé.

---

## 8. Interface Utilisateur (Streamlit)

### 8.1 Layout

```
┌─────────────────────────────────────────────────────────────┐
│                    OPTIONS P&L SCANNER                       │
├──────────────────────────┬──────────────────────────────────┤
│  PANNEAU GAUCHE (sidebar)│  ZONE PRINCIPALE                 │
│                          │                                   │
│  Sous-jacent(s):         │  ┌─────────────────────────────┐ │
│  [SPY,AAPL,NVDA      ]  │  │ RÉSUMÉ DU SCAN              │ │
│  (séparés par virgules)  │  │ Combinaisons testées: 340K  │ │
│                          │  │ Résultats trouvés: 47       │ │
│  Templates:              │  │ Temps GPU: 1.2s             │ │
│  ☑ Calendar Strangle     │  └─────────────────────────────┘ │
│  ☑ Double Calendar       │                                   │
│  ☑ Rev. Iron Condor Cal. │  ┌─────────────────────────────┐ │
│  ☑ Call Diag. Backspread │  │ GRAPHIQUE P&L               │ │
│  ☑ Call Ratio Diagonal   │  │ (Plotly interactif, 4× tall)│ │
│                          │  │                             │ │
│  ── Critères ──          │  │     ╱              ╲       │ │
│  Perte max: [-50 ] %    │  │   ╱                  ╲    │ │
│  Proba perte: [25 ] %   │  │  ╱     ──────────     ╲   │ │
│  Gain min: [10  ] %     │  │ ╱                       ╲ │ │
│  Ratio G/L: [0.1 ]      │  └─────────────────────────────┘ │
│  Budget max: [10000] $   │                                   │
│                          │  ┌─────────────────────────────┐ │
│  ── Scénarios Vol ──     │  │ TABLEAU DES RÉSULTATS       │ │
│  Vol basse: [0.8 ] ×    │  │ (trié par score, cliquable) │ │
│  Vol haute: [1.2 ] ×    │  │ police 82%, legs avec |     │ │
│                          │  └─────────────────────────────┘ │
│  [🔍 LANCER LE SCAN]    │                                   │
│                          │  ┌─────────────────────────────┐ │
│  ── GPU Info ──          │  │ DÉTAILS DE LA COMBINAISON   │ │
│  Device: RTX 5070 Ti     │  │ Legs, Greeks, coûts détaillés│ │
│  VRAM: 12.1/16.0 GB     │  └─────────────────────────────┘ │
│  Batch size: 250K        │                                   │
└──────────────────────────┴──────────────────────────────────┘
```

### 8.2 Interactions

1. L'utilisateur entre un ou plusieurs tickers séparés par des virgules (ex: `SPY,AAPL,NVDA`) et ajuste les critères
2. Clic sur "Lancer le scan" :
   - Pour chaque ticker : chargement chaîne, génération combos, calcul GPU, filtrage
   - Agrégation de tous les résultats, tri par score, retour du top 100
3. Les résultats apparaissent dans le tableau (1 ligne = 1 combinaison, triées par score)
4. Clic sur une ligne → le graphique P&L se met à jour
5. Les détails de la combinaison sélectionnée s'affichent en bas

**Panneau "Plan de sortie" (FEAT-010)** — affiché dans `combo_detail`, sous les
4 métriques principales et au-dessus du tableau des legs. Les seuils sont
calibrés sur les **données réelles** de la combinaison (pas des % arbitraires) :

- **Target (spot ±3 %)** : `max(pnl_mid)` sur la portion de la grille spot
  comprise dans `[current_spot × 0.97, current_spot × 1.03]`. Représente le
  P&L plausible si le sous-jacent ne bouge que de ±3 % sur quelques jours
  (vol implicite inchangée — scénario médian de la bande de vol).
- **Stop loss (perte max struct.)** : `max_loss_pct` calculé par le scanner —
  c'est le pire cas que la structure permet, déjà connu à l'entrée. Pas de
  pourcentage arbitraire (les anciens "−50 % du débit" étaient inadaptés car
  souvent au-delà de la perte structurelle réelle).
- **Date butoir (J-3 short)** : `close_date − 3 jours calendaires`. Au-delà,
  le gamma de la jambe courte explose et le profil P&L affiché par le scanner
  devient caduc.
- **Jours restants** : décompte jusqu'à la date butoir, préfixé `⚠` si < 5 j

Si `combination.events_in_sweet_zone` est non vide, un bandeau `st.info` invite
à sortir dès le lendemain de l'event (l'IV crush est la thèse de la position).

`render_combo_detail` reçoit donc `pnl_tensor`, `spot_range` et `current_spot`
en plus des arguments précédents (passés depuis `ui/app.py`).

**Format de la colonne Legs (FEAT-003) :**

Chaque leg est affiché sur une ligne virtuelle séparée par ` | `, format :
`{direction}{qty} {type} {ticker} {date} {strike}`

Exemple 4 legs : `S1 call AAPL 01APR2026 245 | L2 call AAPL 15MAY2026 265 | ...`
- Direction : `L` = long (achat), `S` = short (vente)
- Date : format `JJMMMAAAA` en majuscules (ex: `01APR2026`)
- Strike : format `g` Python (pas de zéros superflus, ex: `245` pas `245.00`)
- Le ticker est toujours inclus dans chaque leg (même pour un seul sous-jacent)

**Affichage du tableau :** police réduite à 82% via injection CSS dans Streamlit
(`st.markdown("<style>div[data-testid='stDataFrame'] * { font-size: 0.82em !important; }</style>"`).

Note : `st.dataframe` (AG Grid de Streamlit) n'interprète pas `\n` comme retour à la ligne
dans les cellules — le séparateur ` | ` est la solution de rendu garantie.

**Valeurs par défaut des filtres :**
- Perte max : -50%
- Proba perte max : 25%
- Gain min : 10%
- Ratio G/L min : 0.1
- Volume moyen min : 0
- Budget max : $10 000

**Plages d'échéance (FEAT-011)** — sliders dans l'expander *Avancé* :
- **Short leg (DTE)** : défaut `(14, 35)` j. Bornes `[2, 60]`. En dessous de
  14 j, le gamma de la jambe courte explose (gamma cliff de la dernière
  semaine) et la position devient extrêmement sensible aux mouvements du spot.
- **Long leg (DTE)** : défaut `(35, 90)` j. Bornes `[20, MAX_DAYS_TO_EXPIRY=90]`.

Ces plages sont passées à `generate_combinations` via les paramètres
`near_expiry_range` / `far_expiry_range`. Si l'utilisateur élargit `far_max`,
le chargement de l'EventCalendar étend automatiquement la fenêtre Finnhub
(`to_date = today + far_max + 7`).

### 8.3 Précisions sur les scénarios de volatilité

Les 3 scénarios de vol sont toujours `[vol_basse, 1.0, vol_haute]`.
Le scénario median (facteur 1.0 = vol implicite inchangée) est FIXE
et non modifiable par l'utilisateur. C'est le scénario de référence
pour le scorer, les filtres, et le calcul de probabilité.

Les sliders "Vol basse" et "Vol haute" ne contrôlent que les bornes
de la bande d'incertitude affichée en zone ombrée sur le graphique P&L.
Ils n'affectent PAS le classement ni le filtrage des combinaisons.

---

## 9. Structure du projet

```
options-scanner/
├── README.md
├── requirements.txt
├── pyproject.toml
├── config.py                    # Configuration globale, constantes
│
├── data/
│   ├── __init__.py
│   ├── provider_base.py         # Protocol DataProvider
│   ├── provider_yfinance.py     # Implémentation Yahoo Finance
│   └── models.py                # OptionContract, OptionsChain
│
├── engine/
│   ├── __init__.py
│   ├── backend.py               # Abstraction GPU/CPU (xp = cupy ou numpy)
│   ├── black_scholes.py         # BS vectorisé via xp (GPU ou CPU)
│   ├── pnl.py                   # Calcul P&L batch via xp
│   └── combinator.py            # Génération combinaisons par template
│
├── templates/
│   ├── __init__.py              # ALL_TEMPLATES dict, imports de tous les templates
│   ├── base.py                  # TemplateDefinition, LegSpec
│   ├── calendar_strangle.py     # Template 1
│   ├── double_calendar.py       # Template 2
│   ├── reverse_iron_condor.py   # Template 3
│   ├── call_diagonal_backspread.py  # Template 4 (FEAT-001)
│   └── call_ratio_diagonal.py       # Template 5 (FEAT-001)
│
├── scoring/
│   ├── __init__.py
│   ├── probability.py           # Distribution log-normale, proba de perte
│   ├── filters.py               # Filtrage GPU par critères
│   └── scorer.py                # Score composite, classement
│
├── ui/
│   ├── __init__.py
│   ├── app.py                   # Application Streamlit principale
│   ├── components/
│   │   ├── sidebar.py           # Panneau gauche (saisie)
│   │   ├── chart.py             # Graphique P&L (Plotly)
│   │   ├── results_table.py     # Tableau des résultats
│   │   └── combo_detail.py      # Détails d'une combinaison
│   └── styles.css               # Styles custom Streamlit
│
└── tests/
    ├── conftest.py               # Fixtures communes (backend CPU forcé par défaut)
    ├── test_black_scholes.py    # Validation BS vs valeurs de référence (CPU)
    ├── test_pnl.py              # Cas de test P&L connus (CPU)
    ├── test_combinator.py       # Vérification des contraintes
    ├── test_scoring.py          # Vérification des filtres
    └── test_gpu.py              # Cohérence GPU vs CPU (@pytest.mark.gpu, optionnel)
```

---

## 10. Dépendances

```
# requirements.txt

# GPU (OPTIONNEL — le logiciel tourne en mode CPU si absent)
# cupy-cuda12x>=13.0         # Décommenter si GPU NVIDIA + CUDA 12.x disponible

# Data
yfinance>=0.2.36             # Chaînes d'options Yahoo Finance
requests>=2.31               # Appels HTTP

# Calcul (backend CPU, toujours requis)
numpy>=1.26
scipy>=1.12

# UI
streamlit>=1.31
plotly>=5.18

# Utils
pandas>=2.2                  # Manipulation de données tabulaires
python-dateutil>=2.8

# Tests
pytest>=8.0
pytest-cov>=4.0
```

### 10.1 Installation CUDA pour RTX 5070 Ti

```bash
# 1. Installer CUDA Toolkit 12.8+ (nécessaire pour Blackwell)
#    Télécharger depuis https://developer.nvidia.com/cuda-downloads
#    Choisir : Windows > x86_64 > 12.8

# 2. Vérifier l'installation
nvidia-smi          # doit afficher "RTX 5070 Ti"
nvcc --version      # doit afficher CUDA 12.8+

# 3. Installer CuPy (correspondant à la version CUDA)
pip install cupy-cuda12x

# 4. Vérifier CuPy
python -c "import cupy; print(cupy.cuda.runtime.getDeviceProperties(0)['name'])"
# Doit afficher : b'NVIDIA GeForce RTX 5070 Ti'
```

---

## 11. Tests de validation

**Règle : tous les tests (sauf test_gpu.py) doivent passer sans GPU installé.**
Le backend NumPy (CPU) est le backend par défaut des tests.

### 11.1 Black-Scholes : valeurs de référence

Valider le pricer contre des valeurs connues :

```
Test 1 - Call ATM :
  S=100, K=100, T=0.25, vol=0.20, r=0.05
  → Prix attendu : 4.6148 (tolérance ±0.001)

Test 2 - Put OTM :
  S=100, K=90, T=0.5, vol=0.25, r=0.03
  → Prix attendu : 2.0511 (tolérance ±0.001)

Test 3 - Vectorisation :
  1 000 000 de prix calculés simultanément
  → Résultat identique élément par élément (tolérance ±1e-5)

Test 4 - Cas limites :
  T=0 (expiration) → valeur intrinsèque exacte
  vol=0 → valeur intrinsèque actualisée
  S >> K (deep ITM call) → valeur ~= S - K*exp(-rT)
  S << K (deep OTM call) → valeur ~= 0
```

### 11.2 P&L : cas connu

Utiliser l'exemple de référence (section 1.2) avec des prix fictifs pour valider que le profil P&L a la forme attendue.

### 11.3 Performance

```
Benchmark cible (RTX 5070 Ti) :
- BS pricing de 100M d'évaluations : < 50 ms
- P&L de 500K combinaisons × 200 spots × 3 vol : < 2 secondes
- Filtrage de 500K combinaisons : < 10 ms
- Pipeline complet (hors I/O données) : < 5 secondes

Benchmark cible (CPU fallback, pour CI) :
- BS pricing de 1M d'évaluations : < 1 seconde
- P&L de 10K combinaisons × 200 spots × 3 vol : < 5 secondes
```

### 11.4 test_gpu.py (optionnel)

Marqué `@pytest.mark.gpu`, exécuté uniquement si `--run-gpu` est passé à pytest.

```
Test GPU-1 - Cohérence :
  Calculer BS de 100K options sur GPU et CPU
  → Différence max < 1e-5

Test GPU-2 - Performance :
  BS de 100M d'évaluations sur GPU
  → Temps < 50 ms
```

---

## 12. Configuration (config.py)

```python
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

# ── Screener (extrait) ──
SCREENER_REQUEST_DELAY: float = 0.5   # délai par thread (rate-limit Yahoo)
SCREENER_MAX_WORKERS: int = 5         # threads parallèles (PERF-001)
```

**Note :** les valeurs par défaut des filtres UI (perte max -50%, ratio G/L 0.1, etc.)
sont définies directement dans `ui/components/sidebar.py` (valeurs des widgets Streamlit),
pas dans `config.py`. `config.py` ne contient que les constantes moteur.

---

## 13. Feuille de route

### V1 (MVP) — COMPLÉTÉ
- ✅ Template Calendar Strangle
- ✅ Template Double Calendar
- ✅ Template Reverse Iron Condor Calendar
- ✅ Template Call Diagonal Backspread (FEAT-001)
- ✅ Template Call Ratio Diagonal (FEAT-001)
- ✅ Données Yahoo Finance (provider_yfinance.py)
- ✅ Re-pricing hors-séance : consensus IV + BS (BUG-003)
- ✅ GPU engine complet (BS + P&L batch + scoring, fallback CPU)
- ✅ UI Streamlit multi-ticker (FEAT-002)
- ✅ Colonne Legs multi-legs avec séparateur ` | ` (FEAT-003)
- ✅ Tests de validation (CPU, sans GPU requis)

### V2 (en cours / planifié)
- ✅ Screener automatique de sous-jacents (FEAT-004)
- ✅ Intégration EventCalendar dans le scanner — multi-paires + event_score_factor (FEAT-005)
- ✅ Algorithme 4 étapes _select_event_pairs — fallback structuré + event_warning (FEAT-006)
- ✅ Parallélisation screener — ThreadPoolExecutor + batch HV30 (~5min → ~1min) (PERF-001)
- Export des résultats (CSV, JSON)
- Sauvegarde/chargement des scans
- Amélioration du scoring (expected return pondéré, Sharpe ratio du P&L)

### V2 — Backtesting (FEAT-013 à FEAT-016)
- ✅ Provider historique Massive/Polygon (FEAT-013) — close EOD, cache SQLite
- ✅ Plan payant Massive $29/mois (FEAT-014) — appels illimités, heure intraday, ^IRX historique
  - `scan_time` : choix de l'heure (09:30–16:00 ET) via minute aggregates
  - RFR : ^IRX yfinance pour le jour de simulation
  - ETA dynamique basé sur la latence réseau réelle
  - `max_combinations` par défaut : 100 000 ; date par défaut : 2026-02-05
- ✅ Profil P&L à J-N avant expiration short (FEAT-015)
  - Slider 0-10j (défaut 3j) dans sidebar Avancé
  - `combinations_to_tensor(days_before_close=N)` — `exit_date = close_date - N`
  - Plan de sortie et jours restants cohérents avec le même J-N
- ✅ Replay horaire précision 1h (FEAT-016)
  - `backtest_combo_hourly` — barres 1h Massive, filtre NYSE 9h-15h ET, lun-ven
  - Pagination `next_url` suivie (`_paginated`) — Polygon retourne ~86 barres/page
  - `_plot_replay_hourly` : rangeslider + rangebreaks weekends/hors-NYSE
  - Mode dollar automatique si `net_debit < $1` (combos à coût quasi-nul)
  - Hover pré-formaté en strings Python (contourne bug format specifier Plotly)
  - Slider jours par défaut = `close_date - as_of` (durée réelle de la jambe courte)
  - Clé slider unique par combo (reset garanti au changement de combo)
- Multi-ticker backtest (actuellement limité au 1er ticker)
- Export CSV des résultats de backtest

### V3
- IBKR / Tradier live data
- Alertes en temps réel (scanner en continu)
- Gestion du smile de volatilité (vol surface au lieu d'une seule vol ATM)
- Reconstruction IV jour par jour dans le replay (actuellement IV figée à l'entrée)

---

## 14. Module Screener (FEAT-004)

### 14.1 Objectif

Identifie automatiquement les X meilleurs sous-jacents pour les stratégies calendar.
Le bouton "Trouver les meilleurs sous-jacents" dans la sidebar injecte les tickers
résultants dans le champ de saisie, qui alimente ensuite le scanner principal.

### 14.2 Architecture — pipeline en entonnoir

```
Étape 1 — Univers statique              ~128 tickers  (instantané)
Étape 2 — Filtre stock rapide           ~128→80       (~8s, batch yfinance)
Étape 3 — Chargement calendrier events   enrichissement (~2s, 1 req Finnhub)
Étape 4 — Filtre événements micro        ~80→50        (~10s, parallélisé — ThreadPoolExecutor)
Étape 5 — HV30 batch                    ~50 tickers   (~5s, 1 req yfinance multi-ticker)
Étape 6 — Analyse options détaillée      ~50→top X     (~45s, 5 threads × rate limited)
```

### 14.3 Module EventCalendar (`events/`)

Source unique d'événements de volatilité (macro + micro). Partagé entre screener
et scanner (merge ultérieur).

**Sources :**
- Table statique FOMC 2026 (`events/fomc_calendar.py`) : 8 décisions (CRITICAL) + 7 minutes (MODERATE)
- API Finnhub (`events/finnhub_calendar.py`) : NFP, CPI, GDP, PCE Core, ISM, PPI
- Fallback silencieux si API indisponible → FOMC statiques uniquement

**`classify_events_for_pair(near_expiry, far_expiry)` :**
- Danger zone `[today, near_expiry]` → pénalités multiplicatives
- Sweet zone `[near_expiry+1, far_expiry]` → bonus additifs

```
event_score_factor :
  Base 1.0
  Par CRITICAL/HIGH en danger : × 0.4 (composé)
  Par MODERATE en danger      : × 0.7 (composé)
  Par CRITICAL/HIGH en sweet  : + 0.05 (plafonné +0.15)
  Par MODERATE en sweet       : + 0.02 (inclus dans plafond)
```

### 14.4 Scoring (5 composantes)

| # | Composante | Poids | Formule |
|---|-----------|-------|---------|
| 1 | IV Rank proxy | 0.30 | `1.0 - abs(iv_rank - 45) / 55` |
| 2 | Term structure | 0.25 | 1.0 si ratio ≤ 1.00, décroît → 0 à 1.30 |
| 3 | Liquidité | 0.20 | `0.4×spread + 0.3×log(vol) + 0.3×log(OI)` |
| 4 | Densité | 0.10 | `0.7×strike_score + 0.3×weekly_score` |
| 5 | Événements | 0.15 | `clip((factor - 0.5) / 1.0, 0, 1)` |

**IV Rank proxy :** `clip((iv_atm_near / hv30 - 0.6) / 1.2 × 100, 0, 100)`
**Term structure ratio :** `iv_atm_far / iv_atm_near`
**Pénalités :** ×0.3 ex-div, ×0.5 IV Rank>70, ×0.7 backwardation>1.15

**Filtres éliminatoires :** spread>10%, volume<100, OI<500 (si données dispo),
strikes<10, IV=0, CRITICAL en danger zone.

**Comportement hors-séance (BUG-004) :** quand bid=ask=0 (marché fermé),
yfinance retourne IV≈0 et OI=0. Fallbacks appliqués :
- **IV** : recalculée depuis `lastPrice` via approximation ATM `C_time ≈ S×σ×√(T/2π)`
  (précision ±5%, suffisant pour le scoring relatif).
- **OI** : quand <5% des options ont OI>0, `no_open_interest` est désactivé
  (sentinelle `avg_oi=999_999`).
- **Spread** : quand aucun mid valide, `spread_pct=0.0` (non pénalisé).

### 14.5 select_expirations()

Priorité : event_score_factor DESC → near≥7j préféré → écart (far-near) max.

### 14.6 Limitations V1

- Avertissement UI si marché NYSE fermé (données IV calculées depuis lastPrice hors-séance)
- Table FOMC 2026 uniquement (mise à jour annuelle requise)
- Clé Finnhub optionnelle (env var `FINNHUB_API_KEY`)

### 14.7 Structure

```
events/
├── __init__.py
├── models.py          # EventImpact, EventScope, MarketEvent
├── fomc_calendar.py   # dates FOMC 2026 statiques
├── finnhub_calendar.py # API Finnhub + TRACKED_EVENTS
└── calendar.py        # EventCalendar (load, classify_events_for_pair)

screener/
├── __init__.py
├── models.py          # OptionsMetrics (interne), ScreenerResult (public)
├── universe.py        # ~128 tickers (29 ETFs + ~100 stocks)
├── stock_filter.py    # filtre rapide batch yfinance
├── event_filter.py    # filtre earnings/ex-div
├── options_analyzer.py # HV30, ATM IV, select_expirations()
├── scorer.py          # score composite + disqualification
└── screener.py        # UnderlyingScreener.screen()
```

---

## Annexe A — Clarifications et décisions de conception

Ce tableau résume les décisions prises lors de la revue des spécifications.

| # | Question | Décision | Impact |
|---|----------|----------|--------|
| A1 | **close_date** : comment est-elle déterminée ? | Automatique : `close_date = min(expiration des legs short)` pour chaque combinaison. Pré-calculée par le Combinator, encodée dans `time_to_expiry_at_close`. N'est PAS un paramètre de `compute_pnl_batch_gpu`. | `Combination.close_date`, `combinator.py` |
| A2 | **Fallback CPU** : les tests doivent-ils tourner sans GPU ? | Oui obligatoirement. Module `engine/backend.py` expose `xp` (CuPy ou NumPy). Tous les tests sauf `test_gpu.py` (`@pytest.mark.gpu`) utilisent le backend CPU. | `backend.py`, tous les fichiers `engine/` |
| A3 | **get_risk_free_rate()** : constante ou API live ? | **API live ^IRX (FEAT-012)** : `data/risk_free_rate.py` fetch le T-bill 13 semaines via yfinance, divise par 100, valide ∈ ]0, 20[ %. Cache 1 h dans la sidebar via `st.cache_data`. Fallback `config.DEFAULT_RISK_FREE_RATE = 0.045` si erreur réseau. Caption sidebar indique la source (`✓ ^IRX live` ou `⚠ fallback constante`). | `data/risk_free_rate.py`, `data/provider_yfinance.py`, `ui/components/sidebar.py` |
| A4 | **Scénario vol median** : modifiable ? | Non. Toujours `1.0` (vol inchangée). Seules les bornes (vol basse/haute) sont modifiables par l'utilisateur. Le scorer et les filtres utilisent exclusivement le scénario median. | `config.py`, `filters.py`, `chart.py` |
| A5 | **net_debit** : inclut-il le ×100 ? | Oui. `net_debit` est en dollars réels : `Σ (direction × qty × entry_mid × 100)`. TOUJOURS > 0 (les positions en crédit net sont filtrées par le Combinator). Capital engagé = `net_debit`. | `Combination`, `scorer.py`, `filters.py` |
| A6 | **use_adjacent_expiry_pairs** : pourquoi ce flag ? | Les templates diagonales (Call Diagonal Backspread, Call Ratio Diagonal) nécessitent de tester TOUTES les paires (NEAR, FAR) à 5–45 jours d'écart, pas seulement (expirations[0], expirations[-1]). Flag dans `TemplateDefinition`. | `templates/base.py`, `engine/combinator.py` |
| A7 | **Prix hors-séance (1re passe)** : comment gérer bid=ask=0 ? | Si bid=ask=0 pour un contrat, utiliser `lastPrice` comme proxy. Calculer l'IV par bisection (Black-Scholes inverse). Si IV < 0.01 : exclure le contrat. | `data/provider_yfinance.py` |
| A8 | **Re-pricing consensus IV (BUG-003)** : quand s'applique-t-il ? | Quand TOUTES les options d'une expiration ont bid=ask=0 (marché fermé). Calcul IV consensus depuis les options OTM (médiane des IV dans [0.05, 1.5]). Re-pricing BS de TOUTES les options de l'expiration. Corrige les `lastPrice` stales ITM. | `data/provider_yfinance.py` |
| A9 | **net_debit > 0 obligatoire** : why ? | Les combinaisons en crédit net ne correspondent pas au profil "smile" recherché (risque illimité côté court). Le Combinator filtre les combinaisons avec `net_debit <= 0`. | `engine/combinator.py` |
| A10 | **max_iterations=2_000_000** : protection contre quoi ? | Certains templates (double_calendar même strike) génèrent des espaces cartésiens gigantesques. Le cap arrête la boucle pour éviter un blocage UI de plusieurs minutes. Appliqué par paire d'expirations dans `generate_combinations`. | `engine/combinator.py` |
