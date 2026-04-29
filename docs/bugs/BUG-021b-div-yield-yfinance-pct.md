# BUG-021b — div_yield yfinance retourné en % → pricer américain faux

**Statut :** FIXED · Commit : 357c09b · 2026-04-29

---

## Symptôme

Le scan live (yfinance) produisait des profils P&L radicalement différents de la
saisie directe pour le même combo avec les mêmes prix.
Exemples observés : ratio scan/direct de 18x à 106x sur la perte max.

## Cause racine

`yfinance.info['dividendYield']` retourne parfois le taux en **pourcentage**
(ex : `1.14` pour SPY à 1.14%) au lieu de la fraction décimale attendue (`0.0114`).

Le pricer Bjerksund-Stensland 1993 utilisait alors `q = 1.14` (114% de yield)
comme coût de portage `b = r - q = 0.045 - 1.14 = -1.095`, ce qui produisait
des frontières d'exercice anticipé aberrantes et donc des prix d'options faux.

La saisie directe n'était pas affectée car `combo_parser.py` n'initialisait pas
`div_yield` → défaut `Leg.div_yield = 0.0`, comportement raisonnable.

## Fix appliqué

`data/provider_yfinance.py` :
```python
if div_yield > 1.0:
    div_yield /= 100.0
```
Normalisation : si `dividendYield > 1.0`, on divise par 100.
Aucun titre normal n'a un rendement > 100% en conditions réelles.

`ui/combo_parser.py` : `div_yield = contract.div_yield` (était `0.0`)  
`ui/page_tracker.py` : idem dans `_combo_to_combination`

## Corrections annexes découvertes lors de l'audit

- `close_date` dans `combo_parser._build_combination` et `page_tracker._combo_to_combination` :
  utilisait `min(all expirations)` → corrigé en `min(short expirations)` (cohérence avec le combinator)
- `real_mask` (fenêtre ±1σ) dans `run_scan` et `run_backtest_scan` : calculé avec IV et jours
  globaux (médiane de population) → corrigé per-combo (cf. commits 4bd8fbf et 8d2a602)

## Vérification

Test unitaire créé : `tests/test_scan_vs_direct.py`
- Test B (mêmes prix) : diff = $0.00 pour les deux pricers ✓
- div_yields = 0.0114 dans tous les tenseurs ✓

## Spec impactée

`docs/specs/option_scanner_spec_v2.md` — §5 pricer américain, §8 métriques
