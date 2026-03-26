---
id: PERF-001
title: Parallélisation du screener (ThreadPoolExecutor + batch HV30)
status: DONE
date: 2026-03-26
---

## Contexte

Le screener mettait ~5 minutes pour analyser ~86 tickers après le filtre événements.
Profilage (2026-03-26) :

| Étape | Durée séquentielle | Cause |
|-------|-------------------|-------|
| Step 2 `fast_filter_stocks` | 7.7s | batch yfinance (déjà optimisé) |
| Step 4 `filter_by_events` | 44s | 96 × fetch yfinance séquentiel |
| Step 5 `analyze_ticker` × 86 | ~234s | 4 req yfinance/ticker × 0.5s délai |
| **Total** | **~286s (~5 min)** | |

Goulot : 100% Yahoo Finance I/O — le GPU ne peut pas aider ici.

## Solutions implémentées

### Solution 1 — ThreadPoolExecutor

**Step 4 (`screener/event_filter.py`)** :
- `filter_by_events` : extraction de `_fetch_events(sym)` + parallélisation avec
  `ThreadPoolExecutor(max_workers=SCREENER_MAX_WORKERS)`
- Collecte via `as_completed`, reconstruction dans l'ordre original de `symbols`
- Le filtrage (cutoff earnings) reste dans le thread principal

**Step 5 (`screener/screener.py`)** :
- Boucle séquentielle remplacée par `ThreadPoolExecutor`
- Fonction locale `_analyze_one(sym)` soumise pour chaque ticker
- Résultats (disqualification + append `all_metrics`) traités dans le thread principal
  via `as_completed` → pas d'écriture concurrente

**Délai par thread :** `time.sleep(request_delay)` reste à 0.5s **à l'intérieur**
de `analyze_ticker` — chaque thread respecte son propre rate-limit, Yahoo ne voit
pas de burst.

### Solution 2 — Batch HV30

**`screener/options_analyzer.py`** :
- Nouvelle fonction `batch_compute_hv30(symbols)` : 1 seul `yf.download(symbols,
  period="3mo")` pour tous les tickers au lieu de N appels séquentiels
- `analyze_ticker` accepte `hv30_precomputed: float | None` : si fourni, saute
  `compute_hv30()` et son `time.sleep(request_delay)`

**`screener/screener.py`** : appel à `batch_compute_hv30(candidates)` avant le
ThreadPoolExecutor, résultat passé à chaque `analyze_ticker`.

## Config

```python
SCREENER_MAX_WORKERS: int = 5   # ajouté dans config.py
```

## Gain mesuré

Testé le 2026-03-26 : screener nettement plus rapide, confirmé par l'utilisateur.
Gain estimé : ~5× sur les étapes 4 et 5 (parallélisation) + 1 requête éliminée
par ticker (batch HV30).

## Fichiers modifiés

- `config.py` — `SCREENER_MAX_WORKERS`
- `screener/event_filter.py` — `_fetch_events` + parallélisation
- `screener/options_analyzer.py` — `batch_compute_hv30` + `hv30_precomputed`
- `screener/screener.py` — batch HV30 + `ThreadPoolExecutor` étape 5
