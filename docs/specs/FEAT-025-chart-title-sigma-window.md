# FEAT-025 — Titre graphe standard + fenêtre ±1σ dans les résultats

**Statut :** DONE  
**Date :** 2026-05-07

## Contexte

Deux améliorations d'affichage sur la page Live :

1. Le titre du graphe P&L utilisait un format abrégé non standard (ex : `L1 C 720 17MAY26`).
   L'utilisateur veut le format standard lisible et copiable (ex : `L1 call SPY 17JUL2026 720`).

2. Le tableau des résultats n'affichait pas la fenêtre de prix correspondant à ±1σ,
   ce qui rendait les colonnes "Gain ±xx%" difficiles à interpréter.

3. Bonus : le nom de colonne `Gain ±xx%` était dynamique par combo (σ différent par
   combo), créant de multiples colonnes vides dans le DataFrame.

## Comportement implémenté

### Titre du graphe

- Format : `L1 call SPY 17JUL2026 720 | S1 call SPY 15MAY2026 749 | …`
- Le template name est supprimé du titre du graphe (il reste visible dans le tableau)
- `symbol` est passé à `plot_pnl_profile` et intégré dans chaque leg

### Nom de combo sélectionnable (st.code)

- Au-dessus du graphe : `st.code(combo_name_std, language=None)` affiche le nom
  avec un bouton "Copier" intégré Streamlit

### Fenêtre ±1σ sous l'en-tête des résultats

- Format : `fenêtre ±1σ (top combo) = $[749, 852],  σ = 2.8%`
- Basé sur `metrics[0]` (top combo par score) et `spots[0]`
- Le spot du top combo est passé via `spot=top_spot` à `render_results_table`

### Correction nom de colonne

- `f"Gain {range_lbl} %"` → `"Gain ±1σ %"` (nom fixe)
- Supprime la prolifération de colonnes vides

## Fichiers modifiés

- `ui/components/chart.py` — `plot_pnl_profile` : paramètre `symbol`, format titre
- `ui/components/results_table.py` — paramètre `spot`, affichage σ, nom colonne fixe
- `ui/app.py` — `st.code` combo name, passe `symbol` et `spot`
