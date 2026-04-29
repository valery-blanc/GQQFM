# FEAT-017 — Gain max réaliste ±1σ + ratio market/theoretical replay

**Statut :** DONE · Commit : 98b2367 · 2026-04-28

---

## Contexte

Le gain max absolu d'un combo (pic global de la courbe P&L) est trompeur : il
suppose un mouvement du sous-jacent souvent irréaliste sur la durée de détention.
Il faut borner le gain attendu à une amplitude statistiquement plausible (±1σ sur
la période), ce qui donne un ratio gain/risque plus honnête pour le scoring.

---

## Comportement implémenté

### 1. Amplitude réaliste ±1σ (`scoring/filters.py`)

```
realistic_move = spot × iv_atm × √(DTE / 365)
```

Ex : SPY, IV ATM = 15 %, DTE = 14 j → ±2,9 %
Ex : TSLA, IV ATM = 60 %, DTE = 14 j → ±11,8 %

Fonction `realistic_max_gain(combo, spot_range, pnl_slice)` : retourne le P&L
maximum dans la fenêtre [spot − realistic_move, spot + realistic_move].

### 2. Scoring mis à jour (`scoring/scorer.py`)

`gain_loss_ratio` utilise désormais `max_gain_real_pct` au lieu du gain absolu.

### 3. Filtres ajoutés

- `min_max_gain_pct` : gain réaliste minimum (%)
- `min_gain_loss_ratio` : ratio gain réaliste / perte max

### 4. Affichage UI

| Composant | Modification |
|---|---|
| `results_table.py` | Colonne "Gain ±1σ %" + caption indiquant la plage σ utilisée |
| `combo_detail.py` | Bannière "Gain ±1σ" avec delta = écart vs gain absolu |
| `app.py` + `page_backtest.py` | `max_gain_real_pct` et `realistic_range_pct` dans les métriques |

### 5. Ratio market/theoretical replay (`ui/page_backtest.py`)

Après un replay, une caption affiche :
```
Ratio market/theoretical = P&L_market / P&L_théorique_BS
```
Permet de mesurer si le modèle BS surévalue ou sous-évalue le combo testé.

---

## Impact sur l'existant

- `scoring/filters.py` : ajout `realistic_max_gain()` + 2 filtres
- `scoring/scorer.py` : `gain_loss_ratio` recalculé
- `ui/` : 4 fichiers modifiés (affichage uniquement)
- Rétro-compatibilité : les anciens filtres absolu/max restent présents
