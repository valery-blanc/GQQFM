# FEAT-022 — Nom du combo affiché sur la page Tracker

**Status:** IN PROGRESS
**Date:** 2026-04-29

## Context
Sur la page Tracker, on voit les legs mais pas le nom court du combo au format
"L1 call SPY 17JUL2026 715 | L2 put SPY 17JUL2026 690 | ..."
Ce format est exactement ce dont on a besoin pour copier-coller vers la page scan
via la FEAT-021 (saisie directe de combo).

## Behavior
Pour chaque combo tracké, afficher son nom au format résultats :
```
L1 call SPY 17JUL2026 715 | L2 put SPY 17JUL2026 690 | S1 call SPY 15MAY2026 745 | S2 put SPY 15MAY2026 672
```
Avec un bouton ou une zone permettant de copier ce texte facilement (st.code ou st.text_input readonly).

## Technical spec
Fonction `combo_to_label(combo: dict) -> str` dans `ui/page_tracker.py` :
- Pour chaque leg : `{L/S}{quantity} {option_type} {symbol} {DDMMMYYYY} {strike}`
- Direction +1 → "L", -1 → "S"
- Jointure " | "
- Affichage via `st.code(label)` (monospace, sélectionnable) dans l'expander du combo

## Impact on existing code
- `ui/page_tracker.py` : ajout de la fonction + affichage dans l'expander
