# BUG-010 à BUG-014 — Corrections replay (2026-04-28)

## BUG-010 — Weekends affichés dans le replay

**Symptôme** : les samedis et dimanches apparaissaient dans le graphe de replay
avec des valeurs carry-forward, donnant l'impression que le marché était ouvert.

**Fix** (`backtesting/replay.py`) : supprimer le `points.append(BacktestPoint(...,
mode="no_data"))` pour weekends et fériés — simple `continue` sans ajout au
tableau. Les jours non-trading sont absents du graphe.

---

## BUG-011 — Slider session state stale + tickformat + rangebreaks

**Symptômes** :
1. Slider "Jours à replayer" ne se mettait pas à jour au changement de combo
2. Valeurs des ticks Y-axis non arrondies à 2 décimales
3. Heures de fermeture et weekends visibles sur l'axe X du replay horaire

**Fixes** (`ui/page_backtest.py`) :
1. `st.session_state.pop("bt_days_forward", None)` lors du changement de combo et
   du nouveau scan (solution partielle — voir BUG-012 pour le fix définitif)
2. `tickformat=".2f"` ajouté sur les deux axes Y des deux graphes replay
3. `rangebreaks=[dict(bounds=["sat","mon"]), dict(bounds=[16,9], pattern="hour")]`
   sur le graphe horaire ; `rangebreaks=[dict(bounds=["sat","mon"])]` sur le journalier

---

## BUG-012 — Slider clé non unique + % infinis + format hover Plotly

**Symptômes** :
1. Slider ne se remettait toujours pas à jour au changement de combo
2. Combos à coût quasi-nul (net_debit ≈ 0) affichaient des % de l'ordre de 10^15
3. Valeurs dans le popup hover non arrondies (ex: `$410.0592972625185`)

**Causes** :
1. `key="bt_days_forward"` partagée entre tous les combos → session state persistait
2. Division par `net_debit ≈ 0` → pnl_pct = ±∞
3. Plotly ignore les format specifiers (`:+,.2f`) sur `customdata` en unified hover

**Fixes** (`ui/page_backtest.py`) :
1. Clé `f"bt_days_{idx}_{combo.close_date}"` — unique par combo, reset garanti
2. `_replay_y_config` : si `abs(net_debit) < 1`, mode dollar (Y-axis en $, hover en $)
3. Toutes les valeurs hover pré-formatées en strings Python avant passage à Plotly

---

## BUG-013 — Zoom initial masquait les données du replay horaire

**Symptôme** : le replay horaire semblait n'avoir que 7 jours de données alors
qu'il en avait 22. L'utilisateur ne voyait que les 5 premiers jours (zoom initial).

**Cause** : `range=[x_start, x_end]` dans `fig.update_layout` forçait le zoom
sur les 5 premiers jours de trading.

**Fix** (`ui/page_backtest.py`) : suppression du paramètre `range`. Toutes les
données sont visibles par défaut. Le rangeslider permet de zoomer.
Titre du graphe affiche maintenant `X barres / Y jours (dd/mm → dd/mm/yyyy)`.

---

## BUG-014 — Pagination next_url manquante dans _prefetch_hourly_range

**Symptôme** : le replay horaire ne retournait que ~86 barres (~6-7 jours) quel
que soit le nombre de jours demandé.

**Cause** : Polygon retourne les aggs horaires paginés à ~86 barres par page
avec un `next_url`, même avec `limit=5000`. `_prefetch_hourly_range` appelait
`provider._get()` (1 seule page) au lieu de `provider._paginated()`.

**Vérification** (test API direct) :
```
resultsCount: 86
queryCount: 5000
next_url: https://api.polygon.io/v2/aggs/.../cursor=bGlt...
```

**Fix** (`backtesting/replay.py`) : `_prefetch_hourly_range` utilise
`provider._paginated(path, params)` qui suit automatiquement les `next_url`.

**Règle à retenir** : pour les endpoints Polygon qui retournent un `next_url`,
toujours utiliser `_paginated()`, jamais `_get()` seul.
