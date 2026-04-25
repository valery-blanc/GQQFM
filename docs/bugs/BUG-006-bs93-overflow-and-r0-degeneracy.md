---
id: BUG-006
title: BS-1993 — overflow float32 et put dégénéré quand r ≈ 0
status: FIXED
date: 2026-04-25
discovered_by: tests/test_pricer_properties.py (FEAT-009)
---

## Fix appliqué (2026-04-25)

`engine/black_scholes.py::bs_american_price` :

1. **Fallback put à r ≈ 0** : `xp.where(rate > 1e-6, put_am_bs93, euro_put)`.
   Théoriquement exact (un put américain à taux nul n'a aucune prime
   d'exercice anticipé : exercer maintenant donne K, mais K placé à r=0 ne
   produit aucun intérêt sur (T - t) ; donc Amer = Euro).

2. **Garde-fou isfinite** : à la sortie de `bs_american_price`,
   `xp.where(xp.isfinite(result), result, intr)` remplace tout NaN/Inf
   par la valeur intrinsèque (borne inférieure sûre, jamais ≥ valeur
   correcte). Couvre les cas d'overflow float32 résiduels en régime
   extrême (vol < 8 %, T long, etc.).

3. **Tests régression** : `tests/test_pricer_properties.py::TestZeroRateFallback`
   ajoute 6 cas paramétriques (r=0 vs européen, r=0 + q>0, vol=0.06).

### Vérification

- Suite propriétés : 29/29 passed (était 17/23 avant fix), 6 warnings
  d'overflow (caught par isfinite), temps 6.85 s (était 52 s avec
  shrinking hypothesis sur les NaN).
- Suite existante (`test_black_scholes.py`, `test_pnl.py`) : 23/23 passed.

## Symptôme

`bs_american_price` produit :

1. Des valeurs `NaN` dans certaines régions de paramètres (S, K, T, vol, r, q
   pourtant tous dans des plages financières plausibles).
2. Un put américain à **0.0** alors que le put européen équivalent vaut ~9.87,
   pour `S = K = 50, T = 1, vol = 0.5, r = 0, q = 0`.

3 633 `RuntimeWarning: overflow encountered in power` levés pendant la suite
de tests propriétés (FEAT-009) sur 50 examples × 11 tests américains.

## Reproduction

### Cas 1 — NaN
```python
from engine.black_scholes import bs_american_price
from engine.backend import xp

# call avec dividende, r = 0
v = bs_american_price(
    xp.array([0], dtype=xp.int8),         # call
    xp.array([50.0], dtype=xp.float32),   # S
    xp.array([50.0], dtype=xp.float32),   # K
    xp.array([1.0], dtype=xp.float32),    # T
    xp.array([0.0625], dtype=xp.float32), # vol
    0.0,                                  # r
    xp.array([0.0625], dtype=xp.float32), # q
)
# v -> nan
```

### Cas 2 — put = 0
```python
v = bs_american_price(
    xp.array([1], dtype=xp.int8),         # put
    xp.array([50.0], dtype=xp.float32),   # S
    xp.array([50.0], dtype=xp.float32),   # K
    xp.array([1.0], dtype=xp.float32),    # T
    xp.array([0.5], dtype=xp.float32),    # vol
    0.0,                                  # r
    xp.array([0.0], dtype=xp.float32),    # q
)
# v -> 0.0  (intrinsic floor masque un résultat invalide)
# valeur attendue ~ 9.87 (put européen, qui sert de borne inférieure)
```

## Cause racine

### Finding #1 — overflow `S**beta`, `S**gamma`, `(I/S)**kappa`

Dans `_bs93_phi` et `_bs93_american_call` :

```python
# black_scholes.py:24, 26
power_S = xp.where(xp.isfinite(S ** gamma), S ** gamma, xp.float32(0.0))
ratio_pow = (I / S) ** kappa
ratio_pow = xp.where(xp.isfinite(ratio_pow), ratio_pow, xp.float32(0.0))

# black_scholes.py:69
val = (
    alpha * S ** beta             # <-- pas de garde isfinite ici
    - alpha * _bs93_phi(...)
    ...
)
```

`I` (frontière d'exercice anticipé) peut atteindre ~1e11+ quand
`safe_beta_m1` est clampé à `1e-10`, donc `(I/S)**kappa` puis
`alpha * S**beta` produisent `inf` ou `inf × 0 → NaN`.

Les gardes `xp.where(xp.isfinite(...), ..., 0.0)` sur `power_S` et `ratio_pow`
ne couvrent pas tous les chemins : `S**beta` à la ligne 69 est utilisé
directement, et la composition `alpha × S**beta − alpha × phi(...)` peut
produire `NaN` même quand chaque terme isolé est fini.

### Finding #2 — put dégénéré quand r ≈ 0

`bs_american_price` calcule le put via la transformation put-call :

```python
# black_scholes.py:114
put_am = _bs93_american_call(strike, spot, T, vol, div_yield, rate)
#                                                  ^^^^^^^^^  ^^^^
#                                                  joue r       joue q
```

Quand `r = 0` (le `rate` originel devient le `q` du call transformé), à
l'intérieur de `_bs93_american_call` :

```
b = r' - q' = 0 - 0 = 0      (avec r' = put.div_yield = 0, q' = put.rate = 0)
beta = 0.5 + sqrt(0.25 + 0)  = 1.0   exactement
safe_beta_m1 = max(0.0, 1e-10) = 1e-10
B_inf = (1.0 / 1e-10) * K = 5e11
```

`alpha = (I - K) * I^(-beta)` avec `beta = 1` et `I` géant donne `alpha ≈ 1`,
puis le polynôme final s'annule numériquement (somme de termes presque
identiques en magnitude mais de signes opposés → catastrophic cancellation),
donnant un résultat négatif ou NaN. Le plancher `xp.maximum(american, intr)`
le ramène à **0**, masquant silencieusement l'erreur.

Le cas `r = 0` n'est pas pathologique pour un put américain — c'est même
le régime où la prime d'exercice anticipé est maximale. La transformation
put-call de Bjerksund-Stensland n'est tout simplement pas robuste quand
`r' = 0` côté call transformé.

## Impact

- **Cas 1 (overflow → NaN)** : pour des combinaisons utilisateur dans des
  régions atypiques (vol très faible, T long, S/K éloignés), un leg renvoie
  NaN. Le NaN se propage dans `compute_pnl_batch` → courbe P&L cassée
  silencieusement.
- **Cas 2 (put r ≈ 0)** : le scanner sous-évalue les puts américains dans
  les environnements taux bas. Les positions short put apparaissent fausses
  (P&L surévalué côté short). Aujourd'hui Fed ~4.5 % donc r ≈ 0 n'est pas
  l'environnement courant, mais r faible (1–2 %) approche déjà la dégénérescence.

Aucun test existant n'a détecté ces cas car `tests/test_black_scholes.py`
fixe systématiquement `r = 0.05, q ≥ 0.03`.

## Fix proposé (à valider avant implémentation)

### Pour le Finding #1
Ajouter `xp.where(xp.isfinite(...), ..., 0.0)` autour de `S ** beta` et,
plus défensivement, autour de la valeur finale `val` avant le
`xp.where(S >= I, ...)`. Coût : 2 `where` supplémentaires, négligeable.

### Pour le Finding #2
Détecter `r' ≈ 0` (i.e. `rate ≤ 1e-6` côté put) et basculer sur le put
européen `bs_price(put, ...)` (qui sert déjà de borne inférieure) plutôt
que d'invoquer la transformation BS-1993 dans un régime numériquement
instable. C'est légèrement conservateur (sous-estime la prime d'exercice
anticipé), mais évite le retour à 0.

Alternative plus correcte : implémenter une approximation analytique
distincte pour le put américain (Barone-Adesi-Whaley, ou Bjerksund-Stensland
2002 qui est plus robuste), mais c'est une feature, pas un fix de bug.

## Suite

- À l'OK utilisateur : implémenter les deux fixes, ajouter cas de
  régression dans `tests/test_pricer_properties.py`, relancer la suite,
  cocher `[x]` les findings.
- Mettre à jour `docs/specs/option_scanner_spec_v2.md` §5.2 avec la note
  sur le cas `r ≈ 0`.
