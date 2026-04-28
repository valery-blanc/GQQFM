# BUG-008 — DTE calculés sur date.today() au lieu de as_of en backtest

## Statut : FIXED (2026-04-28)

## Symptôme

En mode backtest, le scanner proposait des combos avec des jambes expirant
le lendemain du scan (DTE=1) malgré une plage near=14-35j configurée.
Ex: scan TSLA @ 2026-02-05, short leg expiry 2026-02-06.

## Cause

`generate_combinations`, `_build_default_pairs` et `_select_event_pairs`
utilisaient tous `today = date.today()` (2026-04-28) comme référence pour
calculer le DTE des expirations disponibles.

En backtest, toutes les expirations historiques ont un DTE NÉGATIF par rapport
à aujourd'hui → le filtre DTE retourne un ensemble vide → fallback sur
`[(expirations[0], expirations[-1])]` → expirations[0] = lendemain du scan.

## Fix (`engine/combinator.py`)

- `_select_event_pairs(..., today=None)` : paramètre optionnel, défaut `date.today()`
- `_build_default_pairs(..., today=None)` : idem
- `generate_combinations(..., as_of=None)` : `today = as_of or date.today()`
- `ui/page_backtest.py` : passage `as_of=as_of` à `generate_combinations`

**Règle à retenir** : tout calcul de DTE dans le scanner doit utiliser `as_of`
(date de simulation) en backtest, jamais `date.today()`.
