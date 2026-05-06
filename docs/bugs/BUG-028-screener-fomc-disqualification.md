# BUG-028 — Screener élimine 100 % des tickers les jours d'événement macro CRITICAL

**Status:** FIXED (à valider en séance)
**Date:** 2026-05-06
**Severity:** Critique — rend le screener inutilisable les jours FOMC / NFP / CPI

## Symptôme
Le 2026-05-06, le screener retourne uniquement des tickers en *fallback* (BUG-027), tous
marqués `disqualification_reason="critical_event_in_near"`. Aucun qualifié réel.
Reproduit en pleine séance, données fraîches.

## Reproduction
1. Date système 2026-05-06 (jour FOMC).
2. Lancer le screener (UI ou `screener.screen()`).
3. Tous les tickers — y compris SPY, QQQ, AAPL — finissent dans `all_metrics_disq`.
4. Le top retourné vient à 100 % du fallback BUG-027.

## Root cause

`screener/scorer.py:28-30` :
```python
"critical_event_in_near": lambda m: any(
    ev.impact == EventImpact.CRITICAL for ev in m.events_in_danger_zone
),
```

`events/calendar.py:120` définit la danger zone comme `[today, near_expiry]` —
elle inclut donc *aujourd'hui*. Le 2026-05-06 est un FOMC (`events/fomc_calendar.py:15`)
classé `CRITICAL`. Toute paire (near, far) avec near_expiry > today inclut donc
un événement CRITICAL → tous les tickers sont disqualifiés.

C'est un **défaut de conception** : un événement macro corrélé (FOMC, NFP, CPI)
affecte le marché entier et n'est pas une raison de disqualifier un ticker
individuel — c'est juste un facteur de risque global qui doit pénaliser le score,
pas vider l'univers.

## Impact connexe
Sans ce bug, BUG-027 (fallback en cas de < top_n qualifiés) serait rarement
déclenché. Avec ce bug, BUG-027 masque le problème en remplissant systématiquement
le top par des tickers explicitement disqualifiés.

## Fix appliqué (FEAT-023 § Étape 1)

1. Distinguer **événements ticker-spécifiques** (earnings, ex-div, FDA decision)
   des **événements macro corrélés** (FOMC, NFP, CPI, GDP).
2. La règle `critical_event_in_near` ne s'applique plus qu'aux événements
   ticker-spécifiques. Les events macro restent dans `events_in_danger_zone`
   et continuent à pénaliser le score via `event_score_factor` (déjà ×0.4 par
   `EVENT_PENALTY_CRITICAL_IN_NEAR`).
3. Earnings continuent à être éliminatoires en amont (étape 4 — `event_filter.py`),
   ce comportement est conservé.

### Mécanisme

`events/models.py` : ajout d'un champ `scope: EventScope` sur `MarketEvent` :
- `MACRO` : FOMC, NFP, CPI, GDP, jobless claims, etc.
- `MICRO` : earnings, ex-div, splits, FDA decisions, M&A vote.

`screener/scorer.py` : `critical_event_in_near` filtre uniquement les events
`scope == MICRO`. Si `scope == MACRO` et impact CRITICAL → uniquement pénalité
score (déjà géré par `event_score_factor`).

## Spec section impactée
- `docs/specs/option_scanner_spec_v2.md` § 14 Screener — règles éliminatoires
- `docs/specs/FEAT-023-screener-refonte.md` § Étape 1
