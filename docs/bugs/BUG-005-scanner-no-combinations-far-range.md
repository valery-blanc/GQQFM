---
id: BUG-005
title: "Aucune combinaison trouvée" pour SPY avec les paramètres par défaut
status: FIXED
date: 2026-03-26
---

## Symptôme

Avec SPY et les paramètres par défaut, le scanner retourne :
> "Aucune combinaison trouvée pour les sous-jacents donnés."

## Reproduction

1. Lancer l'app, ticker = SPY, template = CalendarStrangle, critères par défaut
2. Cliquer "Lancer le scan"

## Cause racine

`SCANNER_FAR_EXPIRY_RANGE = (25, 70)` dans `config.py`.

À la date du bug (2026-03-26), les expirations SPY disponibles dans la fenêtre
far [25, 70] jours étaient :
- April 24 (+29j), April 30 (+35j), May 1 (+36j), May 15 (+50j), May 22 (+57j),
  May 29 (+64j) — dernière dans la fenêtre

La prochaine expiration mensuelle majeure, June 18 (+84j), était **exclue**
par la limite de 70 jours.

Avec seulement 59 jours d'écart near/far (mai 29 near vs mars 31 near ≈ 59j
spread), les calendar strangles générés avaient :
- `max_loss = -174%` (bien en-dessous du critère par défaut −50%)
- `max_gain = +28%` (OK mais insuffisant avec ce max_loss)

Aucune combinaison ne passait les filtres → 0 résultats.

## Fix appliqué

`config.py` :
```python
# Avant
SCANNER_FAR_EXPIRY_RANGE: tuple = (25, 70)

# Après
SCANNER_FAR_EXPIRY_RANGE: tuple = (25, 90)
```

La limite portée à 90 jours inclut June 18 (+84j), restoring the 84-day spread
near/far qui existait avant FEAT-005. Les calendar strangles retrouvent leur
profil de gain/perte normal :
- `max_loss ≈ -50%` (dans les limites)
- `max_gain ≈ 80%`

## Impact

Uniquement le scanner (SCANNER_FAR_EXPIRY_RANGE). Le screener utilise
`SCREENER_FAR_EXPIRY_RANGE` (constante séparée, non modifiée ici).
