# BUG-027 — Screener retourne 0 résultats (filtres éliminatoires trop stricts)

**Status:** FIXED
**Date:** 2026-05-04

## Symptom
Le screener automatique retourne 0 résultats. Le menu déroulant (5-10) n'est pas respecté.

## Root cause
Les règles éliminatoires dans `check_disqualification` rejettent tous les tickers quand :
- `iv_data_missing` : IV=0 hors-séance (bid/ask=0, lastPrice fallback échoue sur certains tickers)
- `no_volume` : volume option = 0 le week-end (yfinance ne retourne pas le volume du dernier jour)
- `spread_too_wide` : spread calculé à 0.0 hors-séance → passe, mais IV échoue
- `not_enough_strikes` : certains tickers ont peu d'expirations dans la fenêtre

Si tous les tickers sont éliminés, `all_metrics` est vide → `results[:top_n]` = [].

## Fix applied
Dans `screener.py` : les tickers disqualifiés sont conservés dans `all_metrics_disq`.
Si après disqualification `len(results) < top_n`, on complète avec les meilleurs tickers
disqualifiés (triés par score) jusqu'à atteindre `top_n`.

Dans `sidebar.py` : les tickers en fallback (disqualifiés mais retenus) sont affichés
avec un indicateur ⚠ et la raison, pour que l'utilisateur sache interpréter le résultat.

Le screener garantit désormais toujours `min(top_n, nb_analysés)` résultats.

## Spec section impacted
`docs/specs/option_scanner_spec_v2.md` — §14 Screener
