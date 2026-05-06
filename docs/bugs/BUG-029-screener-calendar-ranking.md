# BUG-029 — Screener calendar : SPY/QQQ enterrés, vol-stocks en tête

**Status:** FIXED
**Date:** 2026-05-06
**Severity:** Élevé — le top calendar contredit le profil métier attendu

## Symptôme
Profil "calendar", top 5 retourné = NFLX / SMH / MU / ORCL / INTC.
SPY est en position #9 (score 23), pourtant son profil est idéal calendar :
IV Rank 52w 46, Spread 2.1 %, ATR 0.9 %, AC1 +0.06, HV20/60 0.68.

## Root causes (4)

### A. Pénalité IV Rank > 70 oubliée dans le scoring multi-stratégie
`compute_score` legacy l'avait. `compute_score_calendar` / `compute_score_ric`
ajoutés en FEAT-023 § Étape 3 ne l'ont pas. Conséquence :
- INTC (IV Rank 85) score 28 — devrait être ×0.5 minimum
- XLE (IV Rank 100) score 25 — devrait être quasi éliminé
- NFLX (IV Rank 65), MU (65) bénéficient du même oubli relatif

### B. Cliff brutal sur term structure aberrant
`_score_term_structure_calendar` retourne 0 si ratio > 1.20. SPY mesuré à
ratio = 1.53 (probablement artefact post-FOMC sur expiration courte) → score
term = 0 → SPY perd 0.20 × 100 = 20 points secs alors que c'est une mesure
suspecte, pas un défaut réel du sous-jacent.

### C. Expirations < 7 jours sélectionnées en period d'événement
`SCREENER_NEAR_EXPIRY_RANGE = (5, 21)` autorise 5j. Pour SPY le jour FOMC,
l'expiration vendredi (5j post-FOMC) est sélectionnée — l'IV ATM est
fortement déprimée par le vol crush post-event → ratio iv_far/iv_near
artificiellement élevé.

### D. `_score_calmness` pénalise la vol qui DÉCÉLÈRE
Formule `1 - |hv_ratio - 1.0| / 0.5` traite symétriquement compression et
expansion. Or pour calendar, **vol qui se compresse = bonus** (l'option near
vendue perd sa prime plus vite). SPY (HV20/60 = 0.68) avait un score
stability de 0.36 alors que c'est l'idéal calendar.

## Fix appliqué (FEAT-023 § Étape 3 — bugfix)

| ID | Modif |
|---|---|
| A | `compute_score_calendar` : `iv_rank > 70` → ×0.5, `> 85` → ×0.3 |
| B | `_score_term_structure_calendar` : floor à 0.20 au lieu de 0 |
| C | `SCREENER_NEAR_EXPIRY_RANGE` : 5 → 7 jours |
| D | `_score_calmness` : compression_score = 1 si hv≤1, décroît seulement au-dessus |

## Tests ajoutés (`tests/test_scoring_multi.py`)
- `test_term_structure_calendar_floor_aberrant`
- `test_calendar_penalizes_high_iv_rank`
- `test_calendar_rewards_vol_compression`

## Spec section impactée
`docs/specs/option_scanner_spec_v2.md` § 14.4a — pénalités calendar

## Validation attendue
SPY estimé à ~39/100 après fix (vs 23 avant), positionné dans le top 3.
INTC/XLE/MU/NFLX (IV Rank > 65) pénalisés et descendus.
