# BUG-007 — Replay 429 + crash Plotly add_vline

## Statut : FIXED (2026-04-28)

## Symptômes

1. **429 Too Many Requests** lors du lancement du replay journalier
   ```
   Erreur replay : 429 Client Error: Too Many Requests for url:
   https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/2026-02-13/2026-02-13
   ```

2. **TypeError: unsupported operand type(s) for +: 'int' and 'datetime.date'**
   dans `_plot_replay` → `add_vline` → Plotly `_mean(X)` → `sum([date, date])`

3. **TypeError: unsupported operand type(s) for +: 'int' and 'str'**
   après passage à `leg.expiration.isoformat()` → même cause, Plotly `_mean`
   opère sur les x-values de la trace

## Causes

**429** : le replay faisait 1 appel API par jour × par leg (≈110 calls pour 30j × 4 legs),
tous en rafale sans cache warm, déclenchant le rate limiting même sur plan payant.

**Plotly crash** : `add_vline(annotation_text=...)` déclenche `_mean(X)` en interne
pour positionner l'annotation. `sum([date1, date2])` → `0 + date1` → TypeError.
Même après passage à ISO string : `sum(["2026-02-27", "2026-02-27"])` → `0 + str`.

## Fix

**429** (`backtesting/replay.py`) : pré-fetch de la plage complète `[as_of, as_of+N]`
en 1 appel par ticker (`_prefetch_daily_range`) → 5 appels total au lieu de ~110.

**Plotly** (`ui/page_backtest.py`) : séparer `add_vline` (sans annotation) et
`add_annotation` séparé → évite `_mean(X)` entièrement.
