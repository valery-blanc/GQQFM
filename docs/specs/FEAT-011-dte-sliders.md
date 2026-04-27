# FEAT-011 — Échéances DTE configurables + défauts durcis

## Statut
DONE

## Contexte

Les défauts précédents (`SCANNER_NEAR_EXPIRY_RANGE = (5, 21)`) autorisaient
des shorts à 5-7 jours, ce qui :
- Met la position dans le **gamma cliff** de la dernière semaine (sensibilité
  démesurée aux mouvements du spot)
- Laisse seulement 4 jours à la thèse pour se déclencher (avec sortie J-3)
- Sort de la sweet zone théta/gamma standard (21-35 j)

L'utilisateur a constaté empiriquement des échéances de 7 j sur ses essais.

## Comportement

### Défauts durcis (`config.py`)

| Constante | Avant | Après |
|---|---|---|
| `SCANNER_NEAR_EXPIRY_RANGE` | `(5, 21)` | `(14, 35)` |
| `SCANNER_FAR_EXPIRY_RANGE` | `(25, 90)` | `(35, 90)` |

### Sliders sidebar (UI)

Deux nouveaux range-sliders dans l'expander **Avancé** :
- **Short leg (jours)** — bornes [2, 60], défaut depuis `config`, help-text
  qui explique le gamma cliff
- **Long leg (jours)** — bornes [20, MAX_DAYS_TO_EXPIRY], défaut depuis `config`

Les valeurs choisies sont remontées dans le dict `params` (`near_expiry_range`,
`far_expiry_range`) et passées à `generate_combinations`.

### `engine/combinator.py`

- Nouvelle fonction `_build_default_pairs(expirations, near_range, far_range)` :
  construit les paires (near, far) respectant les plages absolues quand
  `event_calendar` est `None`. Remplace l'ancien fallback `(expirations[0], expirations[-1])`
  qui ignorait les ranges.
- `generate_combinations` accepte `near_expiry_range` et `far_expiry_range`
  (None → utilise `config`).
- Branche `use_adjacent_expiry_pairs=True` filtre maintenant aussi par DTE
  absolu (pas seulement par gap relatif).

### Chargement EventCalendar (`ui/app.py`)

Avant : `to_date` calé sur `config.SCANNER_FAR_EXPIRY_RANGE[1]` (constante).
Après : utilise `params["far_expiry_range"][1]` pour étendre la requête
Finnhub si l'utilisateur élargit la fenêtre.

## Spec technique

```python
generate_combinations(
    template,
    chain,
    event_calendar=None,
    max_combinations=...,
    min_volume=0,
    max_net_debit=float("inf"),
    max_iterations=2_000_000,
    near_expiry_range=None,   # ← nouveau, défaut = config.SCANNER_NEAR_EXPIRY_RANGE
    far_expiry_range=None,    # ← nouveau, défaut = config.SCANNER_FAR_EXPIRY_RANGE
)
```

## Impact sur l'existant

- `test_combinator_events.py::test_generates_combos_for_multiple_pairs`
  utilise des expirations 7/14/30/42 → passe maintenant `near_expiry_range=(5,21)`
  et `far_expiry_range=(25,70)` en explicite pour préserver le test
- Aucun autre test impacté
- Le screener (`SCREENER_NEAR_EXPIRY_RANGE` / `SCREENER_FAR_EXPIRY_RANGE`) est
  indépendant et n'a pas été touché — garder `(5, 21)` / `(25, 70)` permet
  au screener de détecter une plus large variété de sous-jacents

## Fichiers modifiés

- `config.py`
- `engine/combinator.py`
- `ui/components/sidebar.py`
- `ui/app.py`
- `tests/test_combinator_events.py`
- `docs/specs/option_scanner_spec_v2.md` (à mettre à jour : section 8 sidebar)
- `docs/tasks/TASKS.md`
