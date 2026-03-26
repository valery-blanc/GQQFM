# FEAT-005 — Intégration EventCalendar dans le scanner

**Statut :** DONE
**Date :** 2026-03-25
**Spec de référence :** SPEC_SCANNER_DELTA.md

## Résumé

Intègre le module `events/` (construit dans FEAT-004) dans le pipeline principal
du scanner. Chaque combinaison reçoit désormais un `event_score_factor` basé sur
le profil événementiel de sa paire d'expirations. Ce facteur est appliqué en
multiplicateur sur le score final.

## Changements par fichier

### `data/models.py`
- `Combination` : ajout de `event_score_factor: float = 1.0` et
  `events_in_sweet_zone: list[str] = field(default_factory=list)`.
- Rétro-compatible : les deux champs ont des valeurs par défaut.

### `config.py`
- Ajout de `SCANNER_NEAR_EXPIRY_RANGE: tuple = (5, 21)` et
  `SCANNER_FAR_EXPIRY_RANGE: tuple = (25, 70)`.
- Distincts des constantes `SCREENER_*` (même valeurs par défaut,
  mais découplés pour permettre une future configuration indépendante).

### `engine/combinator.py`
- Nouveau paramètre `event_calendar: EventCalendar | None = None`.
- Nouvelle fonction interne `_select_event_pairs()` :
  sélectionne les top-3 paires d'expirations par `event_score_factor`
  parmi les candidats near ∈ SCANNER_NEAR_EXPIRY_RANGE /
  far ∈ SCANNER_FAR_EXPIRY_RANGE avec far-near ≥ 10j.
  Les paires avec `has_critical_in_danger=True` sont exclues.
- Templates `use_adjacent_expiry_pairs=False` (calendar strangle, double
  calendar, reverse iron condor) : multi-paires via `_select_event_pairs`.
  Fallback sur `(expirations[0], expirations[-1])` si aucune paire éligible.
- Templates `use_adjacent_expiry_pairs=True` (diagonales) :
  comportement existant + calcul de `event_score_factor` par paire.
- Si `event_calendar=None` : comportement identique à avant (factor=1.0).

### `scoring/scorer.py`
- Nouveau paramètre optionnel `event_score_factors: xp.ndarray | None = None`.
- Score final = score_base × event_score_factor (appliqué après normalisation).
- Si `None` : score inchangé (rétro-compatible).

### `ui/app.py`
- Import de `EventCalendar` et chargement unique dans `run_multi_scan`
  avant la boucle sur les tickers.
- `run_scan` reçoit `event_calendar=None` en paramètre.
- Extraction du tenseur `event_factors` depuis les combos filtrées,
  passé à `score_combinations`.

### `ui/components/results_table.py`
- Colonne "Events" ajoutée si au moins une combo a `events_in_sweet_zone`.
  Affiche `"—"` pour les combos sans événement.

### `ui/components/chart.py`
- Annotation dorée (★) sur le graphique P&L si la combo sélectionnée
  a des `events_in_sweet_zone`.

## Tests

- `tests/test_combinator_events.py` — 7 tests (Tests 1-5 + extras) :
  rétro-compat, calendrier vide, sweet zone, danger zone CRITICAL,
  danger zone MODERATE, multi-paires.
- `tests/test_scorer_events.py` — 4 tests (Tests 6-9) :
  factor None, factor 1.15, factor 0.7, classement.

## Notes

- Le module `events/` n'est pas modifié (FEAT-004 déjà complet).
- `finnhub-python` SDK non ajouté — implémentation `requests` déjà fonctionnelle.
- `SCANNER_NEAR_EXPIRY_RANGE` et `SCREENER_NEAR_EXPIRY_RANGE` sont des constantes
  distinctes avec les mêmes valeurs par défaut.
