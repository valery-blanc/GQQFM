# FEAT-024 — Vrai IV Rank 52w via Polygon historique

**Status:** SPEC + IMPL
**Date:** 2026-05-06
**Liens:** FEAT-023 § Étape 3 (approximation HV-based à remplacer)

## Contexte
Le screener calendar utilise un IV Rank 52w **approximé** depuis HV historique :
`HV_sliding × (current_iv / current_hv30)`. Imprécis — il sous-estime l'IV
historique en période de stress et confond bruit de mesure avec vraie IV.

Polygon (plan options payant) expose les prix historiques de chaque contrat
d'option. On peut reconstruire un **vrai** historique d'IV ATM en :
1. Sampling weekly (52 points / an = 1× par semaine)
2. Pour chaque date d'échantillon : trouver le call ATM ~30 DTE, récupérer son
   close, inverser via Black-Scholes pour obtenir l'IV
3. Cacher le résultat en parquet, refresh incrémental quotidien

## Spec technique

### Module `screener/iv_rank_polygon.py`

```python
def fetch_iv_atm_history(
    symbol: str,
    polygon: PolygonHistoricalProvider,
    weeks_back: int = 52,
    cadence_days: int = 7,
    target_dte: int = 30,
) -> list[tuple[date, float]]
```

Pour `weeks_back × 7 / cadence_days` dates espacées :
- récupère le spot de la date d'échantillon (`get_underlying_close`)
- liste les contrats `[date+target_dte-7, date+target_dte+7]`, type=call
- choisit le strike le plus proche du spot (ATM)
- récupère le close du contrat à la date d'échantillon (`get_contract_close`)
- inverse l'IV via `_implied_vol` (BS bisection)
- retourne la liste `[(date, iv_atm), ...]`

### Cache `data/iv_history_cache.parquet`

Schéma : `symbol, sample_date, iv_atm, dte, strike, contract_ticker`

- Lecture/écriture via pandas `read_parquet`/`to_parquet` (ou pickle si parquet
  pose problème).
- À chaque appel, vérifier les dates manquantes pour le `weeks_back` demandé
  et fetcher seulement les nouvelles.
- Stale après 7 jours sur la dernière entrée → refresh.

### Score IV Rank

```python
def compute_iv_rank_52w(
    iv_history: list[tuple[date, float]],
    current_iv: float,
) -> float
```

- Si moins de 30 points dans `iv_history` → 50.0 (neutre)
- Sinon : `(current_iv - min) / (max - min) × 100`, clipped 0-100

### Intégration dans le screener

`screener/screener.py` :
- Si Polygon API key disponible → utilise FEAT-024 (vrai IV Rank)
- Sinon → fallback sur l'approximation HV-based existante (FEAT-023)
- Une seule passe batch sur l'univers pour amortir les appels

### Limitations V1

- **Cadence weekly** : 52 points/an au lieu de 252. Plus fin = 5× plus d'appels
  Polygon. Acceptable pour un V1, à ajuster si besoin.
- **Strike ATM constant** : on ne suit pas le déplacement du spot pendant la
  semaine. Acceptable car on prend des contrats différents à chaque date.
- **Pas de smile** : on ne mesure que l'IV ATM. Pour le skew, FEAT-025 future.

## Plan de tests

- `test_iv_rank_polygon.py` :
  - Mock Polygon : vérifie le sampling weekly, le choix du strike ATM
  - Empty history → 50.0
  - Series synthétique : current_iv = max → rank ~ 100, etc.

## Déploiement

- Lancer une fois le screener pour amorcer le cache (~ 5-10 min sur 128 tickers)
- Cache local persisté dans `data/iv_history_cache.parquet`
- Rafraîchissement automatique aux jours ouvrés suivants (ne fetche que les
  nouvelles dates manquantes)
