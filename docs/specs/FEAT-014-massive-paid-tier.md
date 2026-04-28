# FEAT-014 — Massive (ex-Polygon) plan payant : throttle supprimé + heure intraday + ^IRX historique

## Statut
DONE

## Contexte

Polygon.io a changé de nom en **Massive**. L'utilisateur a souscrit au plan
$29/mois (Starter). Ce plan débloque :
- **Appels illimités** (vs 5/min en free tier)
- **Minute aggregates** sur les options (données intraday)
- **2 ans d'historique** (identique au free tier)

Ce qui reste absent sur ce plan :
- Pas de bid/ask historiques → spread = 0, mid = close
- Pas de Greeks/IV historiques via l'API → bisection BS conservée
- Indices (^IRX) non couverts → yfinance utilisé pour le RFR historique

## Changements

### `data/provider_polygon.py`

- `_RATE_LIMIT_SECONDS = 0.0` (suppression du throttle 13s)
- `_RATE_LIMIT_RETRY_SECONDS = 5.0` (réduit de 30s à 5s)
- Ajout `_minute_bar_at(ticker, as_of, scan_time)` : fetche toutes les minutes
  aggregates du jour (1 call, mis en cache), retourne le bar le plus proche de
  l'heure cible (tolérance ±15 min). `scan_time` au format `"HH:MM"` ET.
- `get_underlying_close(symbol, as_of, scan_time=None)` : utilise `_minute_bar_at`
  si `scan_time` fourni, sinon EOD avec fallback férié.
- `get_contract_close(contract_ticker, as_of, scan_time=None)` : idem.
- `get_options_chain(…, scan_time=None)` : propage `scan_time` à tous les fetches.
  Filtre `volume == 0` désactivé en mode intraday (minute sans trade ≠ contrat stale).
- ETA dynamique : calculé sur la latence réseau réelle observée (moyenne glissante),
  affichage en secondes ou minutes selon la durée restante.
- `get_risk_free_rate(as_of)` : délègue à `fetch_historical_risk_free_rate`.
- `SCAN_TIME_OPTIONS` : dict label → "HH:MM" exposé pour la sidebar.
- Docstring mis à jour (mention Massive, plan payant).

### `data/risk_free_rate.py`

- Ajout `fetch_historical_risk_free_rate(as_of: date) -> tuple[float, str]` :
  fetche ^IRX via yfinance pour la date exacte de simulation, avec lookback 7j
  pour gérer fériés et weekends.

### `ui/page_backtest.py`

- `run_backtest_scan` : récupère `scan_time` depuis `params`, appelle
  `fetch_historical_risk_free_rate(as_of)` pour le RFR au lieu du taux live,
  affiche le taux fetché dans la progress bar.
- Message d'accueil mis à jour : suppression "20-30 min", ajout mention plan payant.
- `scan_time` transmis à `provider.get_options_chain`.

### `ui/components/sidebar.py`

- `max_combinations` : valeur par défaut `50_000` → `100_000`.
- `as_of` : valeur par défaut `max_as_of - 60j` → `2026-02-05` (clampée au range).
- Ajout `selectbox` "Heure du scan (ET)" en mode backtest (options de 09:30 à 16:00,
  défaut 10:00). Valeur retournée dans `params["scan_time"]`.
- Texte d'aide du radio Mode mis à jour.

## Impact sur l'expérience

| Avant (free tier) | Après (plan payant) |
|---|---|
| AAPL scan : ~130 min | AAPL scan : ~3-5 min |
| SPY scan : ~833 min | SPY scan : ~25-30 min |
| Heure fixe (close EOD) | Choix libre 09:30–16:00 ET |
| RFR = taux du jour | RFR = ^IRX historique au jour de la sim |
| ETA affiché en minutes fixes | ETA dynamique sur latence réelle |

## Cache SQLite

Les clés de cache incluent le path et les params Polygon. Les minute aggregates
(`range/1/minute/…?limit=500`) ont des clés distinctes des daily aggregates
(`range/1/day/…`). Re-scanner la même `(symbol, as_of, scan_time)` est instantané
après le premier fetch.
