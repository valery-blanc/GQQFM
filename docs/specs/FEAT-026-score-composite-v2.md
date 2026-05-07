# FEAT-026 — Score composite v2 : ranking multi-critères réaliste

**Statut :** IMPLÉMENTÉ — en attente validation Val (test ANQA)
**Date :** 2026-05-07

## Contexte

Le score composite v1 (`scoring/scorer.py`, poids dans `config.py`) utilisait :

```
Score_v1 = 0.4 × norm(gain_loss_ratio)
         + 0.3 × (1 − norm(loss_prob))
         + 0.3 × norm(expected_return_pct)
```

Trois problèmes identifiés :

1. **Privilégie les combos peu chers** : `expected_return_pct = pnl_mid / net_debit × 100`
   ⇒ un petit combo avec gain pourcentuel élevé bat un gros combo plus rentable en absolu.
2. **Ignore la durée** : `days_to_close` n'apparaît nulle part dans le score
   ⇒ un combo à 7 jours et un combo à 90 jours équivalents pour le même profil P&L.
3. **Sécurité worst-case faible** : `max_loss` n'apparaît qu'au dénominateur du ratio
   gain/loss, pas comme pénalité directe.

Aucun composant ne tient compte de la **liquidité réelle**, du **slippage** (spread bid/ask),
ou de la **robustesse aux scénarios de vol**.

## Objectif

Remplacer la formule par un **score additif normalisé à 7 composants**, ordonnés selon
les priorités utilisateur :

1. Gain max ±1σ (priorité forte)
2. Durée courte avant clôture (capital immobilisé moins longtemps)
3. Rendement annualisé en %
4. Risque maîtrisé (loss_prob faible + perte max %.faible)
5. Bonus qualité : liquidité, robustesse à la vol, slippage

Les **poids sont ajustables dans la sidebar Streamlit** via 7 sliders (FEAT-026 §UI).

## Formule

```
Score_v2 = w1 × norm(max_gain_real_pct)         # gain ±1σ — priorité #1
         + w2 × norm(annualized_return_pct)      # rendement %/an = max_gain_real_pct × 365 / days
         + w3 × (1 − norm(loss_prob))            # 1 − proba perte (lognormale)
         + w4 × (1 − norm(|max_loss_pct|))       # 1 − perte max % (sécurité worst-case)
         + w5 × norm(liquidity_score)            # min(volume × OI) sur les legs
         + w6 × (1 − norm(vol_dispersion_pct))   # 1 − std(P&L au spot) / net_debit
         + w7 × (1 − norm(slippage_pct))         # 1 − Σ(ask−bid) / net_debit (NaN-safe)

Score_final = Score_v2 × event_score_factor      # FEAT-005, conservé
```

**Poids par défaut** (somme = 1.00, modifiables UI puis renormalisés) :

| Poids | Composant | Valeur défaut | Justification |
|---|---|---|---|
| `w_gain_real`   | `max_gain_real_pct`    | **0.25** | Priorité #1 utilisateur |
| `w_annualized`  | `annualized_return_pct`| **0.20** | Combine durée + rendement (#2 + #3) |
| `w_loss_prob`   | `1 − loss_prob`        | **0.15** | Sécurité probabiliste |
| `w_max_loss`    | `1 − |max_loss_pct|`   | **0.10** | Sécurité worst-case |
| `w_liquidity`   | `liquidity_score`      | **0.10** | Exécutabilité réelle |
| `w_robustness`  | `1 − vol_dispersion`   | **0.10** | Résistance changement de régime IV |
| `w_slippage`    | `1 − slippage_pct`     | **0.10** | Coût d'exécution implicite |

**Normalisation** : min-max sur la population filtrée (comme v1), puis poids
renormalisés à somme=1 via `ScoreWeights.normalized()`.

## Slippage NaN-safe

`bid` et `ask` sont **rarement disponibles** chez yfinance et polygon :

- **yfinance live** : bid/ask présents en séance, NaN/0 hors séance.
- **polygon backtest** : `bid = ask = mid` (close du contrat) → spread = 0 toujours.
- **saisie directe** : bid/ask = None.

Si au moins une leg du combo a `bid` ou `ask` manquant → `slippage_pct = NaN`. Au
moment de la normalisation min-max, les NaN sont remplacés par la **médiane** du
dataset (combo neutre sur ce composant — ne profite ni ne pénalise).

Implémenté dans `scoring/scorer.py:_fillna_with_median()`.

## Implémentation

### Fichiers modifiés

| Fichier | Action |
|---|---|
| `data/models.py` | Ajout `bid: float \| None = None` et `ask: float \| None = None` à `Leg` |
| `engine/combinator.py` | Propagation `bid`/`ask` depuis `OptionContract` vers `Leg` |
| `ui/combo_parser.py` | Propagation bid/ask en saisie directe live ; champ `slippage_pct` ajouté au dict metrics |
| `scoring/metrics.py` | **Nouveau fichier** : centralise le calcul des 7 métriques per-combo (`compute_combo_metrics`) |
| `scoring/scorer.py` | Réécrit pour utiliser `ComboMetricsBatch` + 7 composants ; suppression de `_compute_expected_return()` |
| `config.py` | Suppression des 3 `SCORE_WEIGHT_*` ; ajout dataclass `ScoreWeights` + `SCORE_WEIGHTS_DEFAULT` |
| `ui/components/sidebar.py` | Expander "Pondération du score (avancé)" avec 7 sliders + bouton Réinitialiser |
| `ui/app.py` | Lecture `score_weights` depuis params ; appel `compute_combo_metrics` ; nouveaux champs metrics |
| `ui/page_backtest.py` | Idem pour le mode backtest |
| `ui/components/results_table.py` | 4 nouvelles colonnes : `% / an`, `Liq.`, `Disp. vol`, `Slipp.` |

### Métriques `ComboMetricsBatch`

Champs disponibles per-combo (arrays shape `(C,)`) :

```python
max_loss_pct           # perte max / net_debit × 100
max_gain_real_pct      # gain max ±1σ / net_debit × 100
annualized_return_pct  # max_gain_real_pct × 365 / days_to_close
loss_prob              # ∈ [0, 1] — proba perte lognormale
liquidity_score        # min(volume × open_interest) sur les legs
vol_dispersion_pct     # std(P&L au spot courant) / |net_debit| × 100
slippage_pct           # Σ((ask−bid) × qty × 100) / net_debit  (NaN si données absentes)
days_to_close          # jours par combo
# auxiliaires display :
max_gain_real_dollar, max_loss_dollar, daily_gain_dollar
realistic_range_pct, atm_vol_per_combo
```

## UI — sliders pondération

`ui/components/sidebar.py:_render_score_weights_section()` affiche un expander
"⚖️ Pondération du score (avancé)" avec :

- 7 sliders, range `[0.0, 1.0]`, step `0.05`
- Affichage de la part normalisée à droite du label : `Gain max ±1σ — 25%`
- Bouton "Réinitialiser les poids par défaut"
- Persistance via `st.session_state["score_weights"]` (instance `ScoreWeights`)

Les valeurs des sliders sont la **valeur brute** (entre 0 et 1) ; la **part affichée**
est leur valeur divisée par la somme courante. À l'utilisation, `ScoreWeights.normalized()`
renormalise systématiquement à somme=1.

## Cas limites

- **Tous les poids à 0** : `normalized()` lève `ValueError`. L'UI ne devrait pas
  permettre cela en pratique (au moins un slider à 0.05 minimum).
- **Aucun combo a bid/ask** : médiane de NaN → 0 ; tous reçoivent 0 sur le composant
  slippage (neutre — l'effet est nul, le composant ne discrimine plus).
- **Tous les combos identiques sur un composant** : `_normalize()` retourne `zeros_like()`
  (range nul) ; le composant ne pénalise ni n'avantage personne.
- **Saisie directe (1 combo)** : `score = 0.0` (pas de scoring sur 1 combo isolé) ;
  les nouveaux champs (annualized_return_pct, liquidity_score, vol_dispersion_pct=0,
  slippage_pct éventuellement NaN) sont bien renseignés pour l'affichage tableau.

## Tests

- **Non-régression P&L** : `tests/test_scan_vs_direct.py` doit toujours donner
  `diff = $0.00` à spot[0]. L'ajout de `bid`/`ask` à `Leg` ne touche pas le calcul P&L.
- **Validation manuelle ANQA** : scan SPY 30 jours, vérifier que :
  1. Combos courts (7-14 j) à gain ±1σ raisonnable remontent dans le top 10.
  2. Combos très long-tail (90 j) à faible gain ±1σ descendent.
  3. Les 4 nouvelles colonnes (% / an, Liq., Disp. vol, Slipp.) s'affichent correctement.
  4. Les sliders sidebar fonctionnent : changer `w_gain_real=0` → les combos à gain ±1σ
     élevé sortent du top.
  5. Slippage = `—` pour les combos sans bid/ask, sans pénaliser le score.

## Migration / rétrocompatibilité

- L'ancien dict `metrics` continuait d'avoir les clés v1 (`gain_loss_ratio`, etc.) —
  conservées intactes en plus des nouvelles. Aucun appelant externe ne casse.
- Les 3 anciennes constantes `SCORE_WEIGHT_*` sont supprimées de `config.py`. Aucun
  fichier en dehors de `scoring/scorer.py` ne les utilisait (vérifié par `grep`).
- Le multiplicateur événementiel `event_score_factor` (FEAT-005) est conservé tel quel.
