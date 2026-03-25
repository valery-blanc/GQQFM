# FEAT-003 — Colonne Legs multi-lignes dans le tableau de résultats

**Statut** : DONE

## Contexte

La colonne "Legs" dans le tableau de résultats affichait tous les legs sur une seule ligne
séparés par " | ", ce qui était difficile à lire pour des combos à 3-4 legs.

## Comportement

Chaque leg s'affiche sur sa propre ligne dans la cellule, au format :

```
S3 call AAPL 09AUG2024 245.50
L3 call AAPL 16AUG2024 250
L1 call AAPL 16AUG2024 255.5
```

Format d'un leg : `{D}{qty} {option_type} {ticker} {JJMMMAAAA} {strike}`
- `D` = `L` (long) ou `S` (short)
- `option_type` = `call` ou `put` (minuscules)
- ticker = symbole du sous-jacent (affiché uniquement si scan multi-ticker)
- date = format `09AUG2024` (`%d%b%Y` en majuscules)
- strike = format `g` (supprime les `.0` inutiles, ex: `250` et non `250.0`)

La police du tableau est réduite à 82% de la taille normale via CSS injection.

## Changements de code

- `ui/components/results_table.py` :
  - Construction de `legs_summary` par jointure `\n` de chaque leg formaté
  - Suppression de la colonne "Ticker" séparée (ticker intégré dans chaque ligne de Legs)
  - Ajout CSS `font-size: 0.82em` sur le dataframe Streamlit
