# FEAT-028 — Alignement P&L théorique au replay (courbe dynamique + IV historique + marker observé)

**Status:** IN PROGRESS
**Date:** 2026-05-08

## Context

Diagnostic chiffré (script `scripts/diag_pnl_gap.py` exécuté sur ANQA pour un combo
QQQ Double Calendar 2/3 entrée 2026-04-15, observation 2026-04-24) :

| | P&L (%) |
|---|---:|
| Replay observé (mode = market 4/4) | +25.95 % |
| Courbe théorique actuelle (IV figée entrée, expi-3j) | +5.34 % |
| Théorique avec IV recalculée à la date courante | +25.98 % |

→ écart de **20.6 pts** dont **22.2 pts attribuables au vega non capturé** (IV figée
à l'entrée), le reste se compensant. Une fois l'IV réelle injectée, le pricer
américain Bjerksund-Stensland reproduit le marché à **0.03 pt près**.

L'utilisateur n'a aucun moyen visuel de voir l'écart prévision/réalité, et la courbe
P&L est statique pendant tout le replay alors qu'elle pourrait être recalculée à
chaque instant.

## Behavior

Trois améliorations cumulatives sur la page Backtest, section « Replay historique » :

### 1. Curseur replay → profil P&L recalculé à l'instant choisi
- Sous le graphique de replay, un slider permet de choisir un point parmi les `points`
  du replay courant.
- Pour le point choisi, un nouveau graphique « Profil P&L théorique recalculé à `t`
  selon le marché observé » est affiché en dessous, à côté du profil P&L statique
  d'origine.
- Le recalcul utilise :
  - `today = point.date.date()` → `days_before_close = max(0, (close_date − today).days)`
  - IV par leg = IV implicite recalculée par bisection BS depuis
    `point.leg_values[contract_symbol]` (utilise donc directement le prix marché
    observé du replay quand mode = `market`, fallback IV entrée sinon)
  - même `compute_pnl_batch` que la courbe statique → réutilise GPU + BJS américain

### 2. Marker « P&L observé »
- Sur le profil P&L recalculé, superposer un marker (étoile jaune, taille 14px) à la
  position `(point.spot, point.pnl_pct)`.
- Hover affiche : `Replay : spot $XXX.XX, P&L +XX.XX %`.
- Le marker doit retomber **sur** la courbe (à 0.05 pt près) si le pricer + IV
  refetched sont cohérents — c'est la signature visuelle de l'alignement.

### 3. IV historique par date (Piste 3)
- Pas de nouvel appel API : on réutilise les `leg_values` déjà fetchées par
  `backtest_combo` / `backtest_combo_hourly` dans le replay courant.
- Helper `compute_iv_at_replay_point(point, legs, rate) -> dict[str, float]` dans
  `backtesting/replay.py`.
- Si `leg_modes[sym] != "market"` ou prix invalide → fallback IV entrée pour ce leg
  (mode dégradé identique au comportement actuel).

## Spec technique

### Fichier `backtesting/replay.py`
Ajouter :
```python
def compute_iv_at_replay_point(
    point: BacktestPoint,
    legs: list[Leg],
    rate: float,
) -> dict[str, float]:
    """IV implicite par leg à la date du point, depuis point.leg_values.
    Fallback sur leg.implied_vol (IV entrée) si prix invalide / TTE expiré."""
```

### Fichier `engine/pnl.py`
Aucune modification nécessaire — `combinations_to_tensor` accepte déjà
`days_before_close` et la `Combination` passée porte les `implied_vol` par leg.

### Fichier `ui/components/chart.py`
`plot_pnl_profile(...)` accepte un nouveau kwarg :
```python
observed_point: tuple[float, float] | None = None  # (spot, pnl_pct)
```
Si fourni, ajoute un trace `go.Scatter` étoile jaune.

### Fichier `ui/page_backtest.py` `_render_replay_section`
Après l'affichage du graphe replay :
```python
if replay_state and len(points) > 0:
    cursor_idx = st.slider("Point du replay (profil recalculé)",
                            min_value=0, max_value=len(points)-1,
                            value=len(points)-1, key=...)
    pt = points[cursor_idx]
    today = pt.date.date() if isinstance(pt.date, datetime) else pt.date
    days_bc = max(0, (combo.close_date - today).days)

    iv_per_leg = compute_iv_at_replay_point(pt, combo.legs, params["risk_free_rate"])
    legs_dyn = [replace(l, implied_vol=iv_per_leg.get(l.contract_symbol, l.implied_vol))
                for l in combo.legs]
    combo_dyn = Combination(
        legs=legs_dyn, net_debit=combo.net_debit, close_date=combo.close_date,
        template_name=combo.template_name, ...
    )

    spot_range = xp.linspace(pt.spot * SPOT_RANGE_LOW, pt.spot * SPOT_RANGE_HIGH,
                              NUM_SPOT_POINTS, dtype=xp.float32)
    tensor = combinations_to_tensor([combo_dyn], days_before_close=days_bc)
    pnl_tensor = compute_pnl_batch(tensor, spot_range, [vol_low, 1.0, vol_high],
                                     params["risk_free_rate"])

    fig = plot_pnl_profile(combo_dyn, to_cpu(pnl_tensor)[:, 0, :], to_cpu(spot_range),
                            current_spot=pt.spot,
                            loss_prob=...,  # non recalculé, set 0 ou laisser N/A
                            max_loss_pct=..., max_gain_pct=...,
                            observed_point=(pt.spot, pt.pnl_pct))
    st.plotly_chart(fig, use_container_width=True)
```

## Impact / risques

- **Pas de coût Polygon** : 0 nouvel appel API (on consomme le replay déjà fetché).
- **Coût compute** : 1 `compute_pnl_batch` par déplacement du curseur ≈ 200 spots
  × 3 vol × 1 combo. Sur ANQA RTX 5070 Ti : ~10 ms. Sur Tulear CPU : ~80 ms.
  Acceptable comme rerun Streamlit.
- **Cas dégradé** (mode = `theoretical` ou `expired` pour un leg) : on retombe sur
  l'IV entrée pour ce leg → comportement strictement identique à aujourd'hui.
- **Compatibilité** : aucune signature publique modifiée (`observed_point` est optionnel).

## Vérification

1. Lancer le backtest sur QQQ avec le combo Double Calendar 2/3 entrée 2026-04-15 12h00 ET.
2. Lancer un replay 5min.
3. Déplacer le curseur jusqu'à 2026-04-24 13h30.
4. Vérifier sur le profil P&L recalculé :
   - Marker étoile jaune posé à spot ≈ $662.85, P&L ≈ +25.95 %.
   - La courbe (vol médiane) passe **par** le marker à <0.1 pt près.
5. Comparer avec le profil P&L statique (haut de la page) : il doit toujours afficher
   ~+5.4 % au spot 662, prouvant la divergence avant FEAT-028.
