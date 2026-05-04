# BUG-026 — Saisie directe backtest fetche toute la chaîne d'options

**Status:** FIXED
**Date:** 2026-05-04

## Symptom
La saisie directe d'un combo en mode Backtest prend 3+ minutes, comme un scan complet.

## Reproduction steps
1. Page Backtest, saisir un combo directement (expander "Saisir un combo directement")
2. Cliquer "Analyser"
3. Attendre 3-5 min → toujours en cours

## Root cause
`resolve_combo_backtest()` dans `ui/combo_parser.py` appelle
`provider.get_options_chain(symbol, as_of=as_of)` — ce qui fetche
**tous les contrats** de la chaîne (listing + prix individuels),
soit ~500 appels API pour QQQ.

Alors qu'on n'a besoin que des 4 legs spécifiés → 5 appels au total
(1 spot + 4 prix de legs).

## Fix applied
Remplacement de `get_options_chain` par des appels ciblés :
- `provider.get_underlying_close(symbol, as_of, scan_time)` — 1 appel spot
- `provider.get_contract_close("O:" + occ, as_of, scan_time)` — 1 appel par leg
- IV recalculée par bisection (comme dans `get_options_chain`)

Résultat : 5 appels au lieu de ~500. Durée : <1s si déjà en cache, ~2s sinon.

## Spec section impacted
`docs/specs/option_scanner_spec_v2.md` — §8 Saisie directe combo
