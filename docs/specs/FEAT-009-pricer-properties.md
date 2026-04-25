# FEAT-009 — Suite de tests propriétés (pricers + PnL)

**Statut :** DONE (tests écrits — exécution et analyse en cours)
**Date :** 2026-04-25
**Dépendance ajoutée :** `hypothesis >= 6.100`

## Contexte

Les pricers européens (Black-Scholes) et américains (Bjerksund-Stensland 1993)
disposent jusqu'ici de tests par valeurs de référence (`tests/test_black_scholes.py`).
Cela ne couvre pas systématiquement les propriétés théoriques que tout pricer
correct doit respecter.

L'objectif est d'établir un filet de sécurité hypothesis-based qui détecte
toute régression numérique, y compris dans les régions de paramètres peu
exercées (vol extrêmes, T courts, q proche du seuil 1e-6, etc.).

## Propriétés vérifiées

### European (`bs_price`)

| Propriété | Formulation |
|---|---|
| Positivité | `C >= 0`, `P >= 0` |
| Borne sup. call | `C <= S` |
| Borne sup. put | `P <= K * exp(-rT)` |
| Borne inf. call | `C >= max(S - K * exp(-rT), 0)` |
| Borne inf. put | `P >= max(K * exp(-rT) - S, 0)` |
| Parité put-call | `C - P = S - K * exp(-rT)` |
| Monotonie spot | `dC/dS >= 0`, `dP/dS <= 0` (bump 5 %) |
| Vega positif | `dC/dvol >= 0`, `dP/dvol >= 0` (bump 10 %) |

### American (`bs_american_price`)

| Propriété | Formulation |
|---|---|
| Positivité, finitude | `V >= 0`, `isfinite(V)` |
| Plancher intrinsèque | `V >= max(S-K, 0)` (call) ; `V >= max(K-S, 0)` (put) |
| Call sans dividende | `Amer(call, q=0) = Euro(call)` |
| Put sans dividende | `Amer(put, q=0) >= Euro(put)` |

### Frontière dividende (proxy ex-div)

Le modèle utilise un rendement de dividende **continu** : pas de
discontinuité ex-div discrète à tester. On vérifie en proxy :

| Propriété | Formulation |
|---|---|
| Continuité au seuil | `Amer(call, q=0) ~ Amer(call, q=1e-7)` (branche xp.where) |
| Sensibilité call à q | `Amer(call, q=0.06) <= Amer(call, q=0.01)` |
| Sensibilité put à q | `Amer(put, q=0.06) >= Amer(put, q=0.01)` |

### PnL attribution (`compute_pnl_batch`)

| Propriété | Formulation |
|---|---|
| Inversion | `PnL(combo, dir flipped) = -PnL(combo)` |
| Linéarité | `PnL([leg_a, leg_b]) = PnL([leg_a]) + PnL([leg_b])` |
| Payoff à l'échéance (long) | `PnL = (intrinsic - entry) * qty * 100` |
| Payoff à l'échéance (short) | `PnL = -(intrinsic - entry) * qty * 100` |

## Domaine d'échantillonnage hypothesis

Plage volontairement restreinte pour rester dans la zone numériquement
stable de la float32 utilisée par le moteur GPU :

- `S, K ∈ [50, 500]`
- `T ∈ [0.02, 2.0]` années (≈ 1 semaine à 2 ans)
- `vol ∈ [0.05, 0.80]`
- `r ∈ [0.0, 0.08]`
- `q ∈ [0.0, 0.08]`

`max_examples = 50` par test (un compromis fiabilité / temps wall-clock).

## Tolérance

Tolérance combinée absolue + relative :

```
|a - b| <= ABS_TOL + REL_TOL * max(|a|, |b|)
ABS_TOL = 0.02   # 2 cents
REL_TOL = 0.005  # 0.5 %
```

Justifiée par la précision float32 (~7 chiffres significatifs).

## Fichiers ajoutés

- `tests/test_pricer_properties.py` — suite hypothesis + tests PnL ciblés
- `requirements.txt` — `hypothesis >= 6.100`
- `docs/specs/FEAT-009-pricer-properties.md` — ce fichier

## Suivi

Voir `docs/tasks/TASKS.md` section FEAT-009.
Toute violation observée est documentée comme finding (failure log) et,
si elle révèle un vrai bug, donnera lieu à un BUG-XXX dédié.
