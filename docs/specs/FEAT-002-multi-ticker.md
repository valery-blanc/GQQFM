# FEAT-002 — Multi-ticker : scan sur plusieurs sous-jacents simultanément

**Statut** : DONE

## Contexte

L'utilisateur souhaite pouvoir scanner plusieurs sous-jacents en une seule session, avec les résultats agrégés et classés par score.

## Comportement

- La sidebar accepte une liste de tickers séparés par des virgules : `SPY,AAPL,NVDA`
- Un seul clic sur "Lancer le scan" traite tous les tickers
- Les résultats sont agrégés et triés par score (top 100 toutes sources confondues)
- La colonne "Ticker" est affichée dans le tableau de résultats quand plusieurs tickers sont fournis

## Changements de code

### `ui/components/sidebar.py`
- Input renommé "Sous-jacent(s)" avec help text mentionnant la virgule
- Retourne `"symbols": list[str]` au lieu de `"symbol": str`

### `ui/app.py`
- `run_scan(params, symbol)` : scan d'un ticker unique
- `run_multi_scan(params)` : boucle sur `params["symbols"]`, appelle `run_scan` par ticker, agrège, trie, retourne top 100
- Stockage par combo : `pnl_per_combo`, `spot_ranges`, `spots`, `symbols`

### `ui/components/results_table.py`
- Paramètre `symbols: list[str] | None = None`
- Affiche colonne "Ticker" si symbols fourni

## Notes

- Les résultats sont classés par score composite (gain/loss ratio, probabilité de perte, rendement espéré)
- Maximum 100 résultats retournés toutes sources confondues
