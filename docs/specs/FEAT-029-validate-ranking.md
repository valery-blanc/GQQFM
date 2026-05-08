# FEAT-029 — Backtest de validation du ranking

**Status:** IMPL (script pret a tourner sur ANQA, resultats non analyses)
**Date:** 2026-05-08

## Context

L'algorithme de scan classe les combos via un score composite calculé depuis le
`pnl_tensor` théorique. Plusieurs hypothèses entrent dans ce calcul :
`days_before_close=3`, pricer BJS américain, IV figée à l'entrée, scénarios vol
`×0.8 / 1.0 / 1.2`, etc. Le diagnostic FEAT-028 a montré qu'au moins un de ces
paramètres (l'IV figée) cause un écart de 20+ pts entre prévision et réalité sur
des combos individuels.

**Question pratique** : ces écarts changent-ils le **ranking** ? Le top-10 d'un
scan est-il prédictif du P&L réel atteint ex-post ? Si oui, quelle variante du
moteur de scoring serait plus calibrée — c'est-à-dire produirait un top-K dont le
P&L réel suit mieux le P&L prédit ?

Il faut un outil pour répondre **empiriquement**, pas théoriquement.

## Objectif

Mesurer, sur un échantillon de scans historiques, la **calibration** et le
**pouvoir prédictif** du ranking actuel — et comparer à des variantes du moteur.
Sortie : un rapport (CSV + Markdown + scatter plots) qui dit clairement quelles
modifications du scoring valent l'effort d'implémentation.

## Behavior

### Pipeline

Pour chaque combinaison `(variant × symbol × as_of)` :

1. **Run scan** complet via `run_backtest_scan(params, symbol, as_of)` avec les
   `params` de la variante. Récupère les `top_K` combos (par score composite) et
   leurs `metrics`.
2. **Pour chaque combo** du top-K :
   - Lancer un replay quotidien `backtest_combo(combo, as_of, days_forward=close_date-as_of)`.
   - Extraire le **P&L réel à l'horizon de sortie** = `pnl_pct` du replay au point
     `close_date − days_before_close`.
   - Extraire le **spot réel observé** à ce point.
   - Calculer la **prédiction conditionnée** = `pnl_tensor[VOL_MID][argmin(|spot_range − spot_real|)]`
     du combo, divisée par `net_debit`. C'est la prévision faite par le scoring
     **étant donné** le spot effectivement observé — comparable à l'observé.
3. **Stocker une ligne** par combo : `(variant, symbol, as_of, combo_id, score,
   rank, pnl_pred_at_real_spot, pnl_real, max_gain_real_pred, spot_entry,
   spot_exit, days_to_close)`.

### Variantes à comparer

| Nom | `days_before_close` | `use_american_pricer` | vol scenarios | Notes |
|---|---|---|---|---|
| `current` | 3 | True (BJS) | `×0.8/1.0/1.2` figés | Référence (scoring actuel) |
| `days_bc_0` | 0 | True | idem | Effet structurel sur calendars |
| `days_bc_5` | 5 | True | idem | Tester un horizon plus tôt |
| `bs_eur` | 3 | False | idem | Effet pricer américain vs européen |
| `iv_calibrated` | 3 | True | percentiles 10/90 IV historique HV30 | Bande vol réaliste |
| `random` | 3 | True | idem | Top-K **aléatoire** dans les combos valides → baseline nul |

`random` sert de baseline : si une variante n'est pas significativement meilleure
que random sur Spearman, elle n'a pas de pouvoir prédictif.

### Échantillon

- **Symbols** : `["QQQ", "SPY", "IWM"]` (l'univers compact actuel ; extensible).
- **Dates** : 30 dates `as_of` espacées sur les 18 derniers mois (échantillon
  trimestriel + dates clés FOMC/earnings). Liste figée dans le script.
- **Top-K** : K = 10 combos par scan.
- **Total** : 6 variantes × 3 symbols × 30 dates × 10 combos ≈ **5 400 backtests**
  + 540 scans. Coûteux mais le cache SQLite Polygon réduit à un seul fetch par
  contrat-date. Estimé ~3 h sur ANQA RTX 5070 Ti.

### Métriques d'évaluation

Par variante (agrégées sur tout l'échantillon) :

1. **MAE** = `mean(|pnl_pred − pnl_real|)` — précision absolue.
2. **Bias** = `mean(pnl_pred − pnl_real)` — biais signé (positif = scoring trop
   optimiste).
3. **RMSE** — pénalise les gros écarts.
4. **Spearman ρ** entre `rank` (1..K) et `pnl_real` — qualité du **ranking**
   (le rank-1 doit gagner plus que le rank-10 en moyenne).
5. **Top-K hit rate** = `% des combos avec pnl_real > 0` — taux de positifs.
6. **Top-K mean return** = `mean(pnl_real)` du portefeuille top-K.
7. **Top-1 mean return** vs **Top-10 mean return** — gradient du ranking.
8. **Calibration ratio** = `mean(pnl_real) / mean(pnl_pred)` — la bande prédite
   est-elle réaliste ?

Une variante « gagne » si elle améliore **simultanément** :
- Spearman (ranking de meilleure qualité)
- Top-K mean return (les top-K performent mieux)
- |Bias| ↓ (prédictions moins systématiquement faussées)

### Sorties

1. **`scripts/output/validation_<variant>.csv`** — une ligne par combo top-K, brut.
2. **`scripts/output/validation_summary.csv`** — une ligne par variante, métriques
   agrégées. Lisible en table Markdown.
3. **`scripts/output/validation_scatter_<variant>.png`** — scatter `pnl_pred` vs
   `pnl_real`, ligne diagonale, bande ±10 pts. Repère visuel de calibration.
4. **`scripts/output/validation_report.md`** — rapport synthétique :
   - Tableau récapitulatif des 8 métriques × 6 variantes.
   - Top-K mean return courbe par rank (rank 1 → 10) pour chaque variante.
   - Verdict : « variante X gagne sur Y métriques, recommandation Z ».

## Spec technique

### Fichier `scripts/validate_ranking.py`

Orchestrateur principal. Structure :

```python
DATES_TO_TEST: list[date] = [...]   # 30 dates figées
SYMBOLS = ["QQQ", "SPY", "IWM"]
TOP_K = 10
HORIZON_DAYS_BC = 3                  # horizon d'évaluation = close_date − 3j

VARIANTS: dict[str, dict] = {
    "current":       {...},
    "days_bc_0":     {...},
    "days_bc_5":     {...},
    "bs_eur":        {...},
    "iv_calibrated": {...},
    "random":        {...},          # marqueur spécial — sélection aléatoire
}

def run_validation():
    rows = []
    for variant_name, params in VARIANTS.items():
        for symbol in SYMBOLS:
            for as_of in DATES_TO_TEST:
                rows += _validate_one_scan(variant_name, params, symbol, as_of)
    df = pd.DataFrame(rows)
    df.to_csv("scripts/output/validation_full.csv")
    _generate_summary(df)
    _generate_scatters(df)
    _generate_report(df)


def _validate_one_scan(variant, params, symbol, as_of):
    scan_result = run_backtest_scan(params, symbol, as_of)
    if variant == "random":
        top_k = random.sample(scan_result["combinations"], k=TOP_K)
    else:
        top_k = scan_result["combinations"][:TOP_K]
    rows = []
    for rank, combo in enumerate(top_k, 1):
        replay = backtest_combo(combo, as_of=as_of,
                                days_forward=(combo.close_date - as_of).days)
        # Trouver le point au close_date - HORIZON_DAYS_BC
        target_date = combo.close_date - timedelta(days=HORIZON_DAYS_BC)
        pt = min(replay, key=lambda p: abs((p.date - target_date).days))
        # Prédiction conditionnée sur le spot réel
        spot_idx = np.argmin(np.abs(spot_range - pt.spot))
        pnl_pred = pnl_tensor[VOL_MID, combo_idx, spot_idx] / combo.net_debit * 100
        rows.append({
            "variant": variant, "symbol": symbol, "as_of": as_of,
            "rank": rank, "combo_id": str(combo.legs),
            "score": metric.score,
            "pnl_pred_at_real_spot": pnl_pred,
            "pnl_real": pt.pnl_pct,
            "max_gain_real_pred": metric.max_gain_real_pct,
            "spot_entry": scan_result["spot"],
            "spot_exit": pt.spot,
            "days_to_close": metric.days_to_close,
            "mode": pt.mode,
        })
    return rows
```

### Fichier `scripts/output/validation_report.md`

Auto-généré. Format type :

```markdown
# Validation ranking — résultats

## Échantillon
- 3 symbols × 30 dates × 10 combos = 900 combos par variante
- 6 variantes testées
- Période : 2024-09 → 2026-04

## Tableau récap

| Variante       | Spearman ρ | Top-K mean | Hit rate | MAE   | Bias  |
|----------------|-----------|-----------|----------|-------|-------|
| current        | 0.34      | +12.3 %   | 67 %     | 18.2  | -3.1  |
| days_bc_0      | 0.41 ↑    | +14.1 %   | 71 %     | 17.5  | -2.8  |
| ...            |           |           |          |       |       |
| random         | 0.02      | +1.2 %    | 51 %     | 22.5  | +0.4  |

## Verdict

`days_bc_0` gagne sur 4 métriques sur 5 → recommandation : changer le default
`days_before_close` de 3 à 0 dans `config.py`.

`bs_eur` n'apporte rien (Spearman identique, Bias identique) → ne pas remplacer
le pricer.
...
```

## Limitations / pièges connus

- **Coût Polygon** : 540 scans complets + 5 400 replays. Le cache SQLite couvre
  les contrats déjà fetchés mais chaque combo top-K touche typiquement 4 contrats
  inédits. À lancer sur ANQA, jamais en local.
- **Univers limité** : 3 symbols est un échantillon minimal. Pour des conclusions
  robustes, étendre à 10+ symbols (XLF, GLD, TLT, IWM, …) — coût × 3.
- **Mode `theoretical` du replay** : si le replay tombe en mode theoretical à
  l'horizon (faible liquidité du combo), le `pnl_real` retourné est calculé en BS
  avec IV figée — donc il *match* mécaniquement le `pnl_pred` de la variante
  `current`, faussant la métrique. Filtrer ces cas dans le rapport ou les
  reporter dans une colonne séparée.
- **Sélection biaisée** : les combos top-K sont par construction ceux que
  l'algorithme classe au top — leur `pnl_pred` est donc toujours élevé. Le
  scatter `pred vs real` se concentre dans la zone `pred > +10 %`, peu
  d'observations dans la zone basse. Pour une calibration complète, ajouter
  occasionnellement des combos rank-50 / rank-100 → coût supplémentaire mais
  donne du recul.
- **Effet régime de marché** : 18 mois de bull-run vs marché calme donneraient
  des conclusions différentes. Les 30 dates doivent inclure au moins 1 épisode
  de stress (correction, FOMC hawkish, earnings season).

## Implementation

- `scripts/validate_ranking.py` — orchestrateur (boucle `variants × symbols × dates`).
- `ui/page_backtest.py:run_backtest_scan` accepte un `progress_callback` optionnel
  pour le mode headless (script hors Streamlit). Pas de changement de
  comportement dans l'UI : si `progress_callback=None`, fallback sur `st.progress`.
- Cache de scan : `current` et `random` partagent les memes params, donc le
  meme scan est reutilise (pick aleatoire au lieu du top-K trie).
- Variante `iv_calibrated` : recupere les closes Polygon 1 an avant `as_of`,
  calcule la HV30 rolling, prend p10/p90 et les utilise comme `vol_low`/`vol_high`
  (rapportes a la HV30 courante).
- Filtre dans le rapport : les points replay en mode `theoretical` ou `no_data`
  sont exclus du calcul des metriques (P&L non observe).
- Sortie : `scripts/output/{validation_full.csv, validation_summary.csv,
  validation_scatter_<variant>.png, validation_report.md}`.

## Vérification

1. Lancer `python -m scripts.validate_ranking` sur ANQA (ETA ~3 h).
2. Vérifier que `validation_full.csv` contient ~5 400 lignes (6 × 3 × 30 × 10).
3. Ouvrir `validation_report.md` — la table récap doit être lisible.
4. Le scatter de `random` doit être un nuage diffus (Spearman ≈ 0).
5. Le scatter de `current` doit montrer une corrélation positive (sinon
   le scoring actuel n'a aucun pouvoir prédictif → résultat surprise à
   investiguer).

## Décision attendue

Selon le rapport, on prendra l'une des décisions suivantes :
- **Aucune variante ne bat `current` significativement** → garder le moteur actuel,
  conclure que les écarts FEAT-028 ne pénalisent pas le ranking — les rangs sont
  préservés malgré les biais absolus.
- **Une variante gagne** sur ≥3 métriques → l'implémenter (changer config par
  défaut + tests + spec dans `option_scanner_spec_v2.md`).
- **Deux variantes complémentaires** → étudier une combinaison (ex: `days_bc_0` +
  `iv_calibrated`).

Le choix se base sur les chiffres, pas sur des arguments théoriques.
