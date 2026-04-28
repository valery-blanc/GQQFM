# BUG-009 — Jours restants et ex-div utilisent date.today() en backtest

## Statut : FIXED (2026-04-28)

## Symptômes

- Panneau "Plan de sortie" affichait "Jours restants : dépassée" pour toutes
  les combos en mode backtest (la date butoir était dans le passé par rapport
  à aujourd'hui, alors qu'elle était dans le futur par rapport à as_of)
- Le check ex-dividende utilisait `today = date.today()` et pouvait signaler
  des ex-div passés ou manquer des ex-div dans la fenêtre de simulation

## Cause

`_render_exit_plan` calculait :
```python
days_left = (deadline - date.today()).days  # toujours négatif en backtest
```

`_check_ex_div_warning` utilisait aussi `today = date.today()` pour borner la
fenêtre de recherche d'ex-dividende.

## Fix (`ui/components/combo_detail.py`)

- `_render_exit_plan(..., as_of=None)` : `days_left = (deadline - (as_of or date.today())).days`
- `_check_ex_div_warning(..., as_of=None)` : `today = as_of or date.today()`
- `render_combo_detail(..., as_of=None)` : propagation vers les deux fonctions
- `ui/page_backtest.py` : passage `as_of=as_of` à `render_combo_detail`
