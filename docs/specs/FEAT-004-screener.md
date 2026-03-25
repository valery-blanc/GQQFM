# FEAT-004 — Screener automatique de sous-jacents

**Statut :** DONE
**Date :** 2026-03-25
**Spec de référence :** SPEC_UNDERLYING_SCREENER2.md

## Résumé

Nouveau module `screener/` + `events/` permettant à l'utilisateur d'identifier
automatiquement les meilleurs sous-jacents pour les calendar strategies.
Un bouton dans la sidebar lance le screening (~2 min) et injecte les tickers résultants
dans le champ de saisie existant.

## Modules créés

### `events/` — calendrier d'événements (partagé screener + scanner futur)
- `models.py` : `EventImpact` (CRITICAL/HIGH/MODERATE), `EventScope`, `MarketEvent`
- `fomc_calendar.py` : dates FOMC 2026 statiques (décisions + minutes)
- `finnhub_calendar.py` : `fetch_macro_events()` via API Finnhub, fallback FOMC
- `calendar.py` : `EventCalendar` — charge les événements, classe les paires d'expirations

### `screener/` — pipeline d'analyse
- `models.py` : `OptionsMetrics` (interne), `ScreenerResult` (public)
- `universe.py` : ~128 tickers (29 ETFs + ~100 stocks US liquides)
- `stock_filter.py` : filtre rapide batch yfinance (prix ≥ $50, vol 5j ≥ 1M/j)
- `event_filter.py` : filtre earnings/ex-div, ETFs passent toujours
- `options_analyzer.py` : HV30, ATM IV, liquidité, select_expirations()
- `scorer.py` : score composite 5 composantes + pénalités multiplicatives
- `screener.py` : `UnderlyingScreener.screen()`, pipeline en entonnoir

## Formules retenues

### Liquidité (poids 0.20)
```python
spread_score = clip(1 - avg_spread / 0.10, 0, 1)
volume_score = clip(log(avg_volume / 100) / log(50000 / 100), 0, 1)
oi_score     = clip(log(avg_oi / 500) / log(100000 / 500), 0, 1)
liquidity    = 0.4 * spread_score + 0.3 * volume_score + 0.3 * oi_score
```
Justification : log scale pour volume/OI différencie les ordres de grandeur.
Spread pondéré à 0.4 car coût direct à l'ouverture ET à la clôture.

### Densité (poids 0.10)
```python
strike_score = clip((avg_strikes - 10) / (50 - 10), 0, 1)
weekly_score = clip(weekly_count / 4, 0, 1)
density      = 0.7 * strike_score + 0.3 * weekly_score
```
`weekly_count` = nombre d'expirations weeklies dans near_range (pas un booléen).

### select_expirations() — tie-breaker
1. Maximiser event_score_factor
2. En cas d'égalité : maximiser (far_days - near_days) avec near_days ≥ 7
3. near_days < 7 accepté si c'est la seule paire disponible

## event_score_factor
- Base : 1.0
- Par CRITICAL/HIGH en danger zone : × 0.4 (composé)
- Par MODERATE en danger zone : × 0.7 (composé)
- Par CRITICAL/HIGH en sweet zone : + 0.05 chacun (plafonné +0.15)
- Par MODERATE en sweet zone : + 0.02 chacun (inclus dans plafond)

## Limitations V1
- IV hors-séance = 0 (pas de fallback bisection, trop lent sur 50+ tickers)
- Avertissement UI si marché fermé
- FOMC 2027 non inclus dans table statique (à mettre à jour annuellement)
