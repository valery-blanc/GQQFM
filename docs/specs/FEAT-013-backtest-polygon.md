# FEAT-013 — Backtesting historique via Polygon.io (free tier)

## Statut
DONE (étapes 1-4)

## Contexte

Pas de backtesting jusqu'ici : le scanner ne tournait que sur les chaînes
courantes de Yahoo. L'utilisateur voulait pouvoir lancer un scan à une date
passée, choisir une combinaison, et observer son P&L jour par jour sur les
30 jours suivants.

## Architecture

### Provider historique — `data/provider_polygon.py`

`PolygonHistoricalProvider` implémente l'interface `DataProvider` avec un
paramètre `as_of: date` :

- `get_underlying_close(symbol, as_of)` : close du sous-jacent au jour
  (avec fallback jusqu'à 5 j en arrière pour les fériés)
- `list_contracts(symbol, as_of, expiry_min, expiry_max)` : tous les contrats
  actifs ce jour-là, paginé via `next_url`
- `get_contract_close(contract_ticker, as_of)` : close + volume EOD du contrat
- `get_options_chain(symbol, as_of, …, progress_callback)` : pipeline complet,
  produit un `OptionsChain` strictement compatible avec le scanner existant

### Limites du free tier (documentées)

| Limite | Effet |
|---|---|
| 5 calls/min | Throttle 13 s entre calls + retry 30 s sur 429 |
| Pas de bid/ask | `bid = ask = mid = close EOD` (spread = 0) |
| Pas d'IV / Greeks | Bisection BS depuis (close, spot, K, T, r) |
| Pas de dividend yield historique | `div_yield = 0.0` |
| Volume = 0 sur certains contrats | Exclus (close stale) |
| 2 ans d'historique max | Date picker borné à `today - 2 ans` |

### Cache SQLite — `data/cache_polygon.py`

Cache key = `path?sorted_params` (sans `apiKey`). TTL infini puisque les
données historiques sont immuables. Stocké dans `data/.polygon_cache.db`
(gitignored).

### Replay — `backtesting/replay.py`

`backtest_combo(combination, as_of, days_forward, ...)` :

- Pour chaque jour calendaire D dans `[as_of, as_of + days_forward]` :
  - Skip weekends sans appel API (carry-forward)
  - Fetch spot du jour (avec fallback férié)
  - Pour chaque leg :
    - Si `D >= leg.expiration` → valeur intrinsèque au spot du jour d'expiration
    - Sinon : tente le close EOD du contrat (mode `"market"`)
    - Fallback : reprice BS scalaire avec IV figée à l'entrée (mode `"theoretical"`)
- P&L = `Σ direction × qty × (value_today − entry_price) × 100`

Mode global du jour : `"market"` si tous les non-expirés ont une bar,
`"theoretical"` sinon, `"expired"` si tout est expiré, `"no_data"` weekend.

### UI — `ui/page_backtest.py`

Toggle radio "Live / Backtest" en haut de la sidebar. En mode backtest :

1. Date picker `as_of`
2. Bouton **Lancer le scan** (mêmes templates / critères / DTE que le live)
3. **Progress bar Streamlit** alimentée par `progress_callback` :
   - 0-5 % : spot + listing contrats
   - 5-95 % : aggregates per contract avec ETA (cold)
   - 95-100 % : compute scan
4. Résultats : tableau combos + graphe P&L profile (identique au live)
5. Sélection combo → bouton **Lancer le replay** → graphe Plotly journalier
   - Couleurs par mode (vert market, orange theoretical, gris weekend, bleu expired)
   - Marqueurs verticaux à chaque expiration de leg
   - Axe Y secondaire avec spot
   - Métriques peak / trough / final P&L
   - Tableau détaillé jour par jour dans un expander

## Limitation V1

- Multi-ticker non supporté en backtest (pour limiter les calls API).
  Seul le 1er ticker est scanné, warning affiché.
- `event_calendar = None` en backtest (Finnhub free tier ne donne pas
  d'historique d'events macro).
- Le risk-free rate utilise la constante (pas le ^IRX historique). Erreur
  négligeable sur 30 j d'horizon.

## Coût en calls API

Pour un scan SPY typique (strikes ±20 %, expiries 14-90 j) :

| Étape | # calls | Durée cold | Durée cached |
|---|---|---|---|
| Spot @ as_of | 1 | 13 s | 0 |
| List contracts (paginé) | 1-3 | ~30 s | 0 |
| Aggregates per contract | 100-200 | 22-44 min | 0 |
| Replay 30 j | 30 × (1 + N legs) | varie | mostly cached |

Une fois cachée, une 2ème exécution sur la même `(symbol, as_of)` est
quasi instantanée (< 5 s).

## Fichiers créés / modifiés

**Nouveaux** :
- `data/provider_polygon.py`
- `data/cache_polygon.py`
- `backtesting/__init__.py`
- `backtesting/replay.py`
- `ui/page_backtest.py`
- `polygon.key` (clé utilisateur, gitignored)

**Modifiés** :
- `.gitignore` : ajout `polygon.key`, `data/.polygon_cache.db`
- `ui/app.py` : routage Live / Backtest selon `params["mode"]`
- `ui/components/sidebar.py` : radio Mode + date_input as_of conditionnel

## Plan futur (V2 backtesting)

- Scanner multi-ticker en backtest (avec warning de coût en min)
- Historique ^IRX pour le rate
- Reconstruction de l'IV à chaque jour (au lieu de figer à l'entrée)
- Calcul des Greeks historiques jour par jour
- Export CSV des résultats de backtest
