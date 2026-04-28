# FEAT-016 — Replay historique à précision horaire

## Statut
DONE (2026-04-28)

## Contexte

Le replay journalier (FEAT-013) ne permettait de voir le P&L qu'une fois par jour
(close EOD). Un trader voulant identifier l'heure optimale de clôture (ex: pic
à 10h30 vs close du jour) n'avait pas les données nécessaires.

Avec le plan Massive payant, les barres 1h sont disponibles via
`/v2/aggs/ticker/{}/range/1/hour/{from}/{to}`.

## Architecture

### `backtesting/replay.py`

**`_prefetch_hourly_range(provider, ticker, from_date, to_date)`**
- Utilise `provider._paginated()` (pas `_get()`) car Polygon retourne ~86 barres/page
  même avec `limit=5000` — le `next_url` doit être suivi (BUG-014)
- Filtre NYSE : `weekday < 5` ET `9 <= hour_ET <= 15`
- Retourne `dict[datetime_ET_naive → (close, volume)]`

**`_leg_value_hourly(leg, dt_et, spot_today, leg_bars, rate, spot_at_leg_expiry)`**
- Identique à `_leg_value_today` mais les clés du dict leg_bars sont des `datetime`
- Fallback BS si pas de barre horaire pour ce contrat à cette heure

**`backtest_combo_hourly(combination, as_of, days_forward, ...)`**
- Pré-fetche underlying + tous les legs en barres 1h (via `_paginated`)
- Spot à l'expiration : dernière barre horaire du jour d'expiration dans underlying_bars
- Réutilise `BacktestPoint` avec `BacktestPoint.date = datetime ET naive`
- ~5 appels API total (1 underlying + N legs, chacun paginé si nécessaire)

### `ui/page_backtest.py`

**`_replay_y_config(points, combo)`** (helper partagé)
- Détecte `net_debit < $1` → mode dollar (évite les % infinis sur coût quasi-nul)
- Pré-formate toutes les valeurs hover en strings Python (évite le bug format
  specifier de Plotly en unified hover mode)
- Retourne `(y_vals, y_label, y_tick_fmt, y_tick_sfx, hover_y, hover_sec, spots_fmt)`

**`_add_expiry_vlines(fig, combo, as_of, last_x, is_hourly)`** (helper partagé)
- Sépare `add_vline` et `add_annotation` pour éviter le crash `_mean(X)` de Plotly
  sur un axe datetime

**`_plot_replay_hourly(points, combo, as_of)`**
- Rangeslider horizontal Plotly (`rangeslider=dict(visible=True, thickness=0.08)`)
- `rangebreaks=[dict(bounds=["sat","mon"]), dict(bounds=[16,9], pattern="hour")]`
  → masque weekends et heures hors NYSE (16h-9h) sur l'axe X
- Titre dynamique : `X barres / Y jours (dd/mm → dd/mm/yyyy)`
- Affiche toutes les données sans zoom initial forcé

**Slider "Jours à replayer"**
- Clé unique par combo : `f"bt_days_{idx}_{combo.close_date}"` — reset garanti
  au changement de combo, sans `st.session_state.pop` fragile
- Défaut = `max(5, min(60, (combo.close_date - as_of).days))`

**Deux boutons côte à côte**
- "Lancer le replay (précision journalière)" [primary]
- "Lancer le replay (précision horaire)" [secondary]
- `st.session_state.bt_replay = (mode, points)` où mode ∈ {"daily", "hourly"}

## Limitations

- Barres horaires d'options OTM peu liquides → souvent mode "theoretical" (BS avec IV figée)
- Underlying (ex: TSLA) toujours en mode "market" (barres disponibles en continu)
- Le replay horaire consomme 2-3 pages Polygon par ticker (paginé à ~86 barres/page)
  → mise en cache SQLite après le premier run

## Bugs corrigés lors du développement

| Bug | Description |
|-----|-------------|
| BUG-007 | 429 en rafale sur le replay journalier (manque de pré-fetch) |
| BUG-007b/c | `add_vline` + annotation crash Plotly sur axe date |
| BUG-008 | DTE calculés sur `date.today()` au lieu de `as_of` en backtest |
| BUG-009 | Jours restants / ex-div utilisaient `date.today()` |
| BUG-010 | Weekends et fériés affichés dans le replay |
| BUG-011 | Slider session state stale + tickformat manquant + rangebreaks |
| BUG-012 | Clé slider non unique + mode dollar manquant + format hover |
| BUG-013 | Zoom initial masquait les données > 5j dans le replay horaire |
| BUG-014 | Pagination `next_url` non suivie → replay tronqué à 86 barres (~7j) |
