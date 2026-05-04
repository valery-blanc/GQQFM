# BUG-024 — Polygon 429 on paginated cursor pages (no retry)

**Status:** FIXED
**Date:** 2026-05-04

## Symptom
Replay horaire sur la page Tracker (ou Backtest) lève :
`429 Client Error: Too Many Requests for url: https://api.polygon.io/v2/aggs/ticker/NVDA/range/1/hour/…?cursor=…`

## Root cause
`_paginated()` dans `provider_polygon.py` fetche les pages curseur via `requests.get`
directement, sans passer par `_get()`.  
Conséquence : ni la logique de retry 429, ni le cache SQLite ne s'appliquent aux
pages curseur (seule la première page bénéficiait du cache).

Le cache étant correctement partagé entre les pages (même fichier SQLite
`data/.polygon_cache.db`), le vrai problème était uniquement le bypass de retry.

## Fix applied
Ajout de `_get_full_url(url)` dans `PolygonHistoricalProvider` :
- Même logique que `_get` (cache SQLite → throttle → fetch → retry 429 → store)
- Accepte une URL complète (e.g. next_url Polygon) au lieu d'un path relatif

`_paginated` utilise maintenant `_get_full_url` pour toutes les pages curseur.

## Spec section impacted
`docs/specs/option_scanner_spec_v2.md` — §13 Provider Polygon
