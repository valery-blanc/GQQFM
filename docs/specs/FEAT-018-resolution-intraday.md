# FEAT-018 — Résolution intraday configurable (1h / 15min / 5min)

**Statut :** DONE · Commit : 1010d43 · 2026-04-28

---

## Contexte

Le replay horaire (FEAT-016) était limité à une résolution d'1 heure. Pour les
combos dont la durée de détention est courte (quelques jours), une résolution
15 min ou 5 min est utile pour observer les micro-mouvements intraday.

---

## Comportement implémenté

### Résolutions disponibles

| Label | Multiplier | Timespan | API Polygon |
|---|---|---|---|
| 1h | 1 | hour | `/v2/aggs/ticker/{sym}/range/1/hour/…` |
| 15min | 15 | minute | `/v2/aggs/ticker/{sym}/range/15/minute/…` |
| 5min | 5 | minute | `/v2/aggs/ticker/{sym}/range/5/minute/…` |

### Modifications `backtesting/replay.py`

- `_prefetch_hourly_range` → `_prefetch_intraday_range(multiplier, timespan)`
  (signature généralisée, même logique de pagination next_url)
- Filtre NYSE corrigé : **9h30–16h** (était 9h–15h, incorrect)
- `backtest_combo_hourly(resolution="1h")` → propage `multiplier`/`timespan`
- Constante : `RESOLUTIONS = {"1h": (1,"hour"), "15min": (15,"minute"), "5min": (5,"minute")}`

### Modifications `ui/page_backtest.py`

- Sélecteur résolution (`st.radio`) affiché avant les boutons replay
- Bouton intraday affiche la résolution choisie dans son libellé
- `_plot_replay_hourly(resolution=)` : titre et `rangebreaks` adaptés
  - Sub-horaire : rangebreak heures de 9,5 à 24h (vs 9 à 24h pour l'horaire)
- Fix layout : suppression `domain=[0.06, 1.0]` (graphe occupe toute la hauteur)
- `height` réduit à 620 + rangeslider `thickness=0.04` (plus compact)

---

## Impact sur l'existant

- `backtesting/__init__.py` : export `backtest_combo_hourly` mis à jour
- Rétro-compatibilité totale : `resolution="1h"` est le défaut, comportement inchangé
