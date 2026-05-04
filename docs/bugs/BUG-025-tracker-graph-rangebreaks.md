# BUG-025 — Tracker graph shows weekends and off-hours

**Status:** FIXED
**Date:** 2026-05-04

## Symptom
Sur la page Tracker prix réels, le graphe P&L réel vs historique affiche des
plages vides pour les weekends et les heures hors marché (avant 9h30 ET et après 16h).
Les graphes de la page Backtest n'ont pas ce problème (rangebreaks déjà en place).

## Root cause
`_plot_comparison()` dans `ui/page_tracker.py` n'applique pas de `rangebreaks`
dans `fig.update_layout`, contrairement aux graphes intraday de `page_backtest.py`.

## Fix applied
Ajout de `rangebreaks` dans `xaxis` de `_plot_comparison` :
- `bounds=["sat", "mon"]` — weekends
- `bounds=[16, 9.5], pattern="hour"` — heures hors NYSE (sub-horaire, même pattern que backtest 5min/15min)

## Spec section impacted
`docs/specs/option_scanner_spec_v2.md` — §12 Tracker
