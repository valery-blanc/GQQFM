# FEAT-001 — Nouveaux templates : Call Diagonal Backspread + Call Ratio Diagonal

**Statut** : DONE

## Contexte

Ajout de deux nouveaux templates de stratégies diagonales basées sur des calls. Ces templates sont adaptés pour profiter d'un mouvement haussier modéré à fort, avec risque limité.

## Templates ajoutés

### Call Diagonal Backspread (2 legs)

```
Structure : Short N calls NEAR + Long N+1 calls FAR, FAR strike > NEAR strike
Exemples : S3 C 255 MCD NEAR / L4 C 260 MCD FAR
           S3 C 200 GOOG NEAR / L4 C 205 GOOG FAR

Profil P&L : gains si le sous-jacent monte fortement avant FAR expiry
             perte limitée si le sous-jacent reste stable ou baisse peu
```

Fichier : `templates/call_diagonal_backspread.py`

### Call Ratio Diagonal (3 legs)

```
Structure : Short N calls NEAR + Long N calls FAR + Long 1 call FAR (strike plus élevé)
Exemples : S3 C 245 AAPL NEAR / L3 C 250 AAPL FAR / L1 C 255 AAPL FAR
           S3 C 190 BA NEAR   / L3 C 195 BA FAR   / L1 C 200 BA FAR

Profil P&L : le leg L1 supplémentaire plafonne la perte en cas de fort mouvement haussier,
             gains max si le sous-jacent monte modérément vers FAR expiry
```

Fichier : `templates/call_ratio_diagonal.py`

## Spec technique

- `use_adjacent_expiry_pairs=True` : itère sur toutes les paires d'expirations séparées de 5 à 45 jours
- Strikes relatifs au spot (plages en facteur × spot)
- Contraintes d'ordre des strikes et des quantités vérifiées dans `_constraints()`

## Changements de code

- `templates/call_diagonal_backspread.py` : nouveau fichier
- `templates/call_ratio_diagonal.py` : nouveau fichier
- `templates/__init__.py` : ajout des imports et enregistrement dans `ALL_TEMPLATES`
- `templates/base.py` : ajout de `use_adjacent_expiry_pairs: bool = False` dans `TemplateDefinition`
- `engine/combinator.py` : support de `use_adjacent_expiry_pairs` + réécriture pour corriger BUG-001
- `engine/pnl.py` : `combinations_to_tensor` — nombre de legs dynamique (max legs, pas hardcodé à 4)

## Bugs découverts et corrigés lors de l'implémentation

- BUG-001 : indentation combinator (voir docs/bugs/BUG-001-combinator-indentation.md)
- BUG-002 : aucune combo avec expirations extrêmes (voir docs/bugs/BUG-002-new-templates-no-combos.md)
