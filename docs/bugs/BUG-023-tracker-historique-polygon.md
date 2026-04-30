# BUG-023 — Courbe historique tracker : tous points rouges (options sans préfixe O:)

**Statut :** FIXED  
**Date :** 2026-04-30  
**Fichiers impactés :** `backtesting/replay.py`, `ui/page_tracker.py`

---

## Symptôme

Sur la page Tracker, la courbe historique Polygon affichait tous les points en rouge
(mode = "theoretical", Black-Scholes) même pour des combos récents avec données disponibles.
La résolution était également 1h au lieu de 5min comme la courbe réelle.

## Cause racine (3 bugs distincts)

### Bug 1 — Préfixe `O:` manquant pour les options Polygon (critique)
Les contract_symbols stockés dans le tracker (`SPY260717C00720000`) n'ont pas de préfixe `O:`.
L'API Polygon exige `O:SPY260717C00720000` pour les options. Sans lui, Polygon cherche
un ticker boursier inexistant → 0 barres → tout le combo repasse en Black-Scholes.

Le cache SQLite (TTL infini) a verrouillé ces résultats vides, bloquant les appels
ultérieurs même après correction.

**Fix :** Helper `_polygon_option_ticker()` dans `replay.py` qui ajoute `O:` si absent.
Appliqué dans `backtest_combo` et `backtest_combo_hourly` avant chaque `_prefetch_*_range`.
20 entrées de cache corrompues purgées manuellement.

### Bug 2 — Désalignement des timestamps pour les barres 1h
Barres 1h SPY (underlying) : Polygon démarre à l'ouverture marché → `09:30, 10:30, 11:30…`
Barres 1h options : Polygon utilise des heures rondes → `10:00, 11:00, 14:00…`
`leg_bars.get(dt_et)` cherchait `10:30` dans un dict avec `10:00` → toujours None.

**Fix :** Fallback nearest-neighbor `_nearest_leg_bar()` avec tolérance 35min dans
`_leg_value_hourly`. Pour les barres 5min (bornes identiques), le fallback ne s'active pas.

### Bug 3 — Résolution 1h au lieu de 5min
`_run_backtest_overlay` utilisait `resolution="1h"` par défaut alors que la courbe
réelle (Polygon day.close) a une résolution de 5min.

**Fix :** Changement du défaut à `resolution="5min"` dans `_run_backtest_overlay`.

## Comportement post-fix

- Ligne bleue continue pour tous les points historiques
- Points rouges uniquement pour les créneaux 5min sans volume sur l'option (BS fallback légitime)
- Message d'avertissement si toute la période ou un bloc est sans données Polygon :
  `"Données Polygon non disponibles pour les options — période complète : jj/mm HH:MM → jj/mm HH:MM"`
- Les contiguous gaps partiels génèrent un avertissement par bloc
