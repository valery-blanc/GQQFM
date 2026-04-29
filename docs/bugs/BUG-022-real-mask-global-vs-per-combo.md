# BUG-022 — real_mask ±1σ global au lieu de per-combo → Gain ±1σ faux

**Statut :** FIXED · Commits : 4bd8fbf, 8d2a602 · 2026-04-29

---

## Symptôme

La métrique "Gain ±1σ %" affichait des valeurs radicalement différentes selon
les combos, avec des résultats parfois aberrants (ex: 105338% pour des combos
à net_debit quasi-nul). La saisie directe du même combo donnait une valeur
complètement différente du scan.

## Cause racine

Dans `run_scan` (app.py) et `run_backtest_scan` (page_backtest.py), le masque
`real_mask` définissant la fenêtre ±1σ était calculé **une seule fois** avec
l'IV médiane et les jours médians de **toute la population** de combos :

```python
atm_vol = float(np.median(atm_vols))      # médiane globale
days_to_close = int(statistics.median(...)) # médiane globale
real_mask = (spot_range >= lo) & (spot_range <= hi)  # même masque pour TOUS
```

Chaque combo utilisait donc le même masque ±1σ, quelle que soit sa propre IV
ou sa propre date d'expiration. Un combo avec une IV très différente de la
médiane avait un `max_gain_real` calculé sur le mauvais intervalle.

## Fix appliqué

Le masque est désormais calculé **per-combo** dans la boucle métriques :

```python
for i, combo_i in enumerate(filtered_combos):
    atm_vol_i = min((abs(l.strike - spot), l.implied_vol) for l in combo_i.legs)[1]
    days_i    = max(1, (combo_i.close_date - today).days)
    range_i   = atm_vol_i * math.sqrt(days_i / 365.0) * 100
    mask_i    = (spot_range_cpu >= lo_i) & (spot_range_cpu <= hi_i)
```

De même dans `build_single_combo_results` (combo_parser.py) :
```python
atm_vol = min((abs(l.strike - spot), l.implied_vol) for l in combination.legs)[1]
```
(était `max(...)`, incorrect — devait utiliser la jambe la plus proche du spot)

## Correction annexe

`abs(net_debit)` utilisé comme dénominateur pour éviter l'inversion de signe
sur les spreads à crédit (net_debit négatif). Seuil : si `|nd| < 1$` → 1e-6.

## Spec impactée

`docs/specs/option_scanner_spec_v2.md` — §8 métriques de scoring
