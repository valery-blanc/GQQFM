# FEAT-015 — Profil P&L à J-N avant expiration short

## Statut
DONE (2026-04-28)

## Contexte

Le profil P&L affiché par le scanner était calculé exactement à l'expiration des
jambes courtes (J=0). Or en pratique les traders sortent 2-5 jours avant pour
éviter le gamma risk et le pin risk. Afficher le profil à J=0 surestimait
systématiquement le gain atteignable.

Exemple observé : combo TSLA affiché à +74% si spot=$409. Replay montrait +9%
le jour où le spot était à $409 (J-10 avant expiration). Le profil correct à J-3
aurait montré ~55%, beaucoup plus proche de la réalité observée.

## Changements

### `engine/pnl.py`

- `combinations_to_tensor(combinations, days_before_close=0)` : nouveau paramètre
- Pour chaque leg : `exit_date = combo.close_date - timedelta(days=days_before_close)`
- `tte_at_close[i, j] = max(0, (leg.expiration - exit_date).days) / 365.0`
- Effet : les jambes courtes ont encore N jours de valeur temps dans le profil,
  ce qui réduit le pic théorique mais le rend réaliste

### `ui/components/sidebar.py`

- Slider "Profil P&L à J-N (avant expiration short)" dans l'expander Avancé
- Plage 0-10j, défaut 3j
- Retourné dans `params["days_before_close"]`

### `ui/app.py` + `ui/page_backtest.py`

- `combinations_to_tensor(all_combinations, days_before_close=params.get("days_before_close", 3))`
- `days_before_close` stocké dans le dict résultats et passé à `render_combo_detail`

### `ui/components/combo_detail.py`

- `render_combo_detail(..., days_before_close=3)` : nouveau paramètre
- `_render_exit_plan(..., days_before_close=3)` : idem
- `deadline = combination.close_date - timedelta(days=days_before_close)`
- Label dynamique : "Date butoir (J-3 short)" → "Date butoir (J-N short)"

## Impact

À J-3 par défaut, les jambes courtes ont encore 3 jours de valeur temps dans
le calcul. Le pic du profil P&L est réduit de 10-20% typiquement, mais correspond
à ce que le marché offrirait réellement à cette date.

Avec J=0 : surestimation systématique du gain théorique
Avec J=3 : cible réaliste, cohérente avec les pratiques de gestion du risque
