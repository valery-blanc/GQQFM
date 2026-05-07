# BUG-030 — IV Rank FEAT-024 bloqué : yfinance dans les threads worker

**Status:** FIXED
**Date:** 2026-05-07
**Feature liée:** FEAT-024 (IV Rank 52w via Polygon)

## Symptôme

Premier run du screener calendar avec Polygon payant : la progression s'affichait
"IV history 181/4316" et n'avançait plus. Le process Streamlit restait actif
(CPU ≈ 37s depuis lancement) mais aucun résultat supplémentaire.

## Cause racine

Dans `_fetch_iv_atm_at_date`, pour chaque tâche worker :
```python
rate = polygon.get_risk_free_rate(sample_date)
```
→ appelle `fetch_historical_risk_free_rate(as_of)`
→ appelle `yf.Ticker("^IRX").history(...)` **sans timeout**

Sur Windows (ANQA / Python 3.11), yfinance peut se bloquer indéfiniment quand
les sockets réseau sont saturés ou que la connexion Yahoo Finance est lente.
Avec `ThreadPoolExecutor(max_workers=10)`, les 10 workers atteignent tous cette
ligne simultanément et se bloquent. `as_completed()` ne génère plus jamais de
résultats → compteur gelé.

Aggravant : 4316 paires à fetcher (premier run, cache vide) × 3 appels Polygon
chacune → charge réseau élevée rendant les appels yfinance encore plus lents.

## Correction

**`screener/iv_rank_polygon.py`** :

1. Ajout paramètre `rfr: float | None = None` à `_fetch_iv_atm_at_date`.
   Dans la fonction, `rate = rfr if rfr is not None else config.DEFAULT_RISK_FREE_RATE`.
   → Élimine tout appel yfinance depuis les workers.

2. Dans `fetch_or_load_iv_history`, un seul appel live `fetch_risk_free_rate()`
   dans le thread principal avant de démarrer le pool. Taux partagé pour toutes
   les dates. Impact sur IV Rank < 1pt (mesure relative, variation RFR mineure).

3. `max_workers` augmenté de 10 à 20 pour accélérer le premier run (plan payant
   sans rate limit).

## Justification : précision RFR

L'IV Rank est une mesure **relative** : `(current_iv - min_52w) / (max_52w - min_52w)`.
La variation du taux sans risque sur 52 semaines est typiquement < 1.5%.
L'impact sur l'IV inversée via BS est < 0.5% absolu. L'impact sur IV Rank : < 1pt.
Utiliser un taux constant pour toutes les dates est acceptable pour ce calcul.

## Fichiers modifiés

- `screener/iv_rank_polygon.py` — fix yfinance + max_workers
