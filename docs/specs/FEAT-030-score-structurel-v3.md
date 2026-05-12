# FEAT-030 — Score composite v3 : métriques structurelles

**Status:** SPEC  
**Date:** 2026-05-10  
**Auteur:** FEAT-029 diagnostic + analyse théorique  

---

## Contexte

Le score composite v2 (FEAT-026/026b) classe les combos selon 7 métriques dérivées
du `pnl_tensor` théorique : gain ±1σ, rendement annualisé, probabilité de perte,
perte max, liquidité, robustesse vol, slippage.

**Lacune identifiée.** Ces 7 métriques mesurent le *résultat attendu* d'un combo,
pas la *qualité structurelle* de la position :

1. Un combo peut avoir un `max_gain_real_pct` élevé mais reposer sur une structure à
   terme inversée (IV_near < IV_far) — il n'y a alors aucun edge théta.
2. Le scorer ne détecte pas quand le marché est en régime trend (HV > IV), situation
   où les calendars échouent systématiquement.
3. La fenêtre ±1σ utilise l'IV implicite plutôt que la vol réalisée, ce qui la rend
   trop étroite quand HV > IV.
4. Le rapport theta/gamma, indicateur direct de la qualité d'un trade temps, est absent.
5. Les bandes de vol (`vol_low = IV×0.8 / vol_high = IV×1.2`) sont flat pour tous les
   symbols — elles ne reflètent pas la vraie distribution de vol de chaque sous-jacent.

**Ce que FEAT-030 fait.** Cinq améliorations indépendantes, appliquées au pipeline
scan live et backtest, qui renforcent la cohérence entre le ranking prédit et le P&L
réel observé (objectif de FEAT-029).

---

## Vue d'ensemble des 5 améliorations

| # | Nom | Nature | Composant touché |
|---|-----|---------|-----------------|
| A | Pente de terme (term slope) | Nouvelle métrique + filtre disqualifiant | `scoring/metrics.py`, `scoring/filters.py`, `scoring/scorer.py` |
| B | Filtre de régime HV/IV | Multiplicateur scalaire de score | `scoring/scorer.py`, `ui/page_live.py`, `ui/page_backtest.py` |
| C | Vol bands calibrées HV p10/p90 | Remplacement des défauts ×0.8/×1.2 | `data/provider_yfinance.py`, `ui/page_live.py`, `ui/page_backtest.py`, `ui/components/sidebar.py` |
| D | Theta/Gamma ratio | Nouveaux Greeks BS + nouvelle composante score | `engine/black_scholes.py`, `scoring/metrics.py`, `scoring/scorer.py` |
| E | Fenêtre ±1σ HV-ajustée | Élargissement de la fenêtre réaliste | `scoring/filters.py`, `scoring/metrics.py` |

---

## A — Pente de terme (term_structure_slope)

### Problème

Un calendar strangle est rentable **structurellement** quand IV_near > IV_far
(on vend de la vol chère à court terme, on achète de la vol moins chère à long terme).
Le scanner actuel ne vérifie pas cette condition ; un combo avec IV_near ≈ IV_far ou
IV_near < IV_far (backwardation) n'a aucun edge mais peut quand même être classé haut.

### Définition

Pour un combo avec N legs répartis sur K ≥ 2 expirations distinctes, on
compare la **première** expiration à la **dernière** (les expirations
intermédiaires sont ignorées — les templates 4 jambes en ont rarement
plus de 2) :

```python
expiries = sorted(set(leg.expiration for leg in combo.legs))
if len(expiries) >= 2:
    near_exp = expiries[0]              # plus tôt
    far_exp  = expiries[-1]             # plus tard
    near_ivs = [l.implied_vol for l in combo.legs if l.expiration == near_exp]
    far_ivs  = [l.implied_vol for l in combo.legs if l.expiration == far_exp]
    term_slope = float(np.mean(near_ivs) / max(np.mean(far_ivs), 1e-6))
else:
    term_slope = float("nan")           # K=1 → pas de structure calendaire
```

**Pourquoi pas un split médian** : pour K=2 (cas dominant : calendar
strangle), un split `expiries[len/2]=expiries[1]=max_exp` donne
`far_ivs = []` → div by zero. La formulation first/last est sans
ambiguïté pour tous les K ≥ 2.

**Cas K=1** (RIC, backspread — tous les legs sur 1 seule expiration) :
`term_slope = NaN`. Le score utilisera `_fillna_with_median` (cf. section
score), donc ces combos reçoivent un score neutre = médiane de la
population — ils ne sont ni récompensés ni pénalisés par cette métrique.

**Signe économique :** `term_slope > 1.0` = vol proche plus chère = bonne
structure pour un calendar.

### Filtre disqualifiant

Dans `filter_combinations` (skip si NaN — K=1 ne peut pas être éliminé
par cette règle) :
```python
ts_finite = xp.isfinite(term_slope_per_combo)
ts_pass = xp.where(ts_finite,
                   term_slope_per_combo >= config.MIN_TERM_STRUCTURE_SLOPE,
                   xp.ones_like(term_slope_per_combo, dtype=bool))
mask &= ts_pass
```

Valeur défaut : `MIN_TERM_STRUCTURE_SLOPE = 0.95`
(tolérance de 5% pour les cas à la limite — une légère backwardation transitoire
ne doit pas éliminer un combo par ailleurs excellent).

### Composante de score

```python
s_ts = _normalize(_fillna_with_median(metrics.term_slope))
score += w.w_term_slope * s_ts
```

Le `_fillna_with_median` fait que les K=1 reçoivent le score médian
(neutre) au lieu de `0` (pénalité injuste).

### Helper réutilisable — `scoring/metrics.py:compute_term_slopes`

```python
def compute_term_slopes(combinations: list[Combination]) -> np.ndarray:
    """Calcule term_slope pour chaque combo. Shape (C,) float32.
    NaN pour K=1 (handled par _fillna_with_median dans le scorer).

    Appelée 1 fois dans run_scan / run_backtest_scan AVANT filter_combinations
    (pour le filtre disqualifiant). Slicée par valid_indices avant
    compute_combo_metrics (pas de double calcul).
    """
    out = np.empty(len(combinations), dtype=np.float32)
    for i, combo in enumerate(combinations):
        expiries = sorted({l.expiration for l in combo.legs})
        if len(expiries) < 2:
            out[i] = np.nan
            continue
        near_exp, far_exp = expiries[0], expiries[-1]
        near_ivs = [l.implied_vol for l in combo.legs if l.expiration == near_exp]
        far_ivs  = [l.implied_vol for l in combo.legs if l.expiration == far_exp]
        out[i] = float(np.mean(near_ivs) / max(np.mean(far_ivs), 1e-6))
    return out
```

### Modifications fichiers

**`config.py`**
```python
MIN_TERM_STRUCTURE_SLOPE: float = 0.95
```
Et dans `ScoreWeights` (voir section ScoreWeights ci-dessous).

**`scoring/metrics.py`**

1. Ajouter le helper `compute_term_slopes` (cf. ci-dessus, top du module).
2. Ajouter dans `ComboMetricsBatch` :
   ```python
   term_slope: "xp.ndarray"   # shape (C,) float32, NaN si K=1
   ```
3. Ajouter dans `compute_combo_metrics` un nouveau param :
   ```python
   def compute_combo_metrics(
       ...
       term_slope_arr: np.ndarray | None = None,    # shape (C,) — calculé en amont
   ) -> ComboMetricsBatch:
   ```
   Si `term_slope_arr is None` (rétrocompat tests), calculer via
   `compute_term_slopes(combinations)`. Sinon utiliser tel quel et juste
   `to_xp(term_slope_arr)`.

**`scoring/filters.py`**  
`filter_combinations` reçoit `term_slope_per_combo: xp.ndarray | None = None`.
Si fourni, applique le filtre (avec gestion NaN cf. ci-dessus). Si `None`,
filtre désactivé (rétrocompat tests).

**`scoring/scorer.py`**  
Ajouter (cf. ci-dessus) :
```python
s_ts = _normalize(_fillna_with_median(metrics.term_slope))
score += w.w_term_slope * s_ts
```

**Pipeline (run_scan / run_backtest_scan)** :
```python
# AVANT filter_combinations
term_slopes_all = compute_term_slopes(all_combinations)        # np.ndarray (N,)
term_slopes_xp = to_xp(term_slopes_all)
valid_indices = filter_combinations(
    ..., term_slope_per_combo=term_slopes_xp,
)
# Slice après filtre, pour réutilisation dans compute_combo_metrics
term_slopes_filtered = term_slopes_all[valid_indices_cpu]      # np.ndarray (C,)
metrics_batch = compute_combo_metrics(
    filtered_combos, ..., term_slope_arr=term_slopes_filtered,
)
```

---

## B — Filtre de régime HV/IV (regime_score_factor)

### Problème

Les calendars échouent quand le sous-jacent est en tendance : le spot sort de la
zone de profit avant la date de sortie. Le ratio HV30/IV_ATM mesure ce régime.
Quand HV > IV, le marché *réalise* plus que ce que les options implicent — les
vendeurs de gamma perdent.

Le premier data point de FEAT-029 (QQQ +6% en 14j, 9/10 combos perdants) illustre
exactement ce cas.

### Définition

```
hv30       : HV annualisée calculée sur les 30 derniers jours de closes (% form)
iv_atm     : IV ATM du sous-jacent au moment du scan (médiane des atm_vol per-combo)
hv_iv_ratio = hv30 / iv_atm
```

Fonction de mapping `hv_iv_ratio → regime_factor` :

| hv_iv_ratio | Interprétation | regime_factor |
|---|---|---|
| < 0.60 | Vol très chère vs réalité → super pour calendars | 1.05 |
| 0.60 – 0.85 | Normal | 1.00 |
| 0.85 – 1.00 | Marché trending, risque élevé | 0.80 |
| > 1.00 | Vol réalisée > implicite → trend fort | 0.55 |

Ce facteur est **scalaire** (même valeur pour tous les combos d'un scan, car il
dépend du sous-jacent, pas du combo individuel). Il s'applique **après**
`score_combinations` :

```python
# Dans run_scan / run_backtest_scan, après scores = score_combinations(...)
scores = scores * regime_factor   # broadcast scalaire
```

### Source de HV30

**Mode live** : calculée dans `run_scan` via `screener.options_analyzer.compute_hv30(symbol)`
(fonction existante, ligne 104 — pas de duplication). Import direct :
```python
from screener.options_analyzer import compute_hv30
hv30 = compute_hv30(symbol)   # float ou 0.0 si données insuffisantes
```

**Mode backtest** : calculée dans `run_backtest_scan` à partir des bars
underlying. **IMPORTANT** : `_prefetch_daily_range` n'est PAS encore appelé au
moment du scan (c'est fait plus tard dans `backtest_combo` pour le replay).
Il faut donc fetcher explicitement avant le scan :
```python
from backtesting.replay import _prefetch_daily_range
from scoring.regime import compute_hv30_from_bars

start = as_of - timedelta(days=90 + 60)
bars = _prefetch_daily_range(provider, symbol.upper(), start, as_of)
hv30 = compute_hv30_from_bars(bars, as_of)   # voir scoring/regime.py
```

### Module nouveau : `scoring/regime.py`

Factorise tous les helpers HV30 + régime, partagés entre :
- `validate_ranking.py` (FEAT-029, fonction existante `_hv30_percentiles`)
- `data/provider_yfinance.py` (FEAT-030-C `get_hv30_and_vol_bands`)
- `ui/page_live.py` / `ui/page_backtest.py` (FEAT-030-B regime factor)

```python
"""Helpers HV30 et facteur de régime — FEAT-030-B + 030-C."""
from __future__ import annotations

import math
from datetime import date

import numpy as np

import config


def compute_hv30_from_closes(closes: np.ndarray, win: int = 21) -> float:
    """HV annualisée sur `win` jours (~21 trading days = 30 calendar).
    Retourne 0.0 si données insuffisantes."""
    closes = np.asarray(closes, dtype=np.float64)
    closes = closes[closes > 0]
    if len(closes) < win + 1:
        return 0.0
    log_ret = np.diff(np.log(closes))
    hv = float(log_ret[-win:].std() * math.sqrt(252))
    return hv if np.isfinite(hv) and hv > 0 else 0.0


def compute_hv30_from_bars(
    bars: dict[date, tuple[float, int]],
    as_of: date,
    win: int = 21,
) -> float:
    """Wrapper sur compute_hv30_from_closes pour les bars Polygon
    (`{date: (close, volume)}`). Filtre les dates ≤ as_of."""
    sorted_items = sorted((d, c) for d, (c, _) in bars.items() if d <= as_of)
    closes = np.array([c for _, c in sorted_items], dtype=np.float64)
    return compute_hv30_from_closes(closes, win=win)


def compute_hv30_percentiles(
    closes: np.ndarray,
    win: int = 21,
    lookback: int = 90,
) -> tuple[float, float, float] | None:
    """Retourne (p10, current, p90) de la HV30 rolling sur `lookback` jours
    précédents. None si données insuffisantes (< 30 points HV)."""
    closes = np.asarray(closes, dtype=np.float64)
    closes = closes[closes > 0]
    if len(closes) < win + 30:
        return None
    log_ret = np.diff(np.log(closes))
    if len(log_ret) < win + 30:
        return None
    hv_series = np.array([
        log_ret[i - win:i].std() * math.sqrt(252)
        for i in range(win, len(log_ret) + 1)
    ])
    hv_series = hv_series[np.isfinite(hv_series) & (hv_series > 0)]
    if len(hv_series) < 30:
        return None
    return (
        float(np.percentile(hv_series, 10)),
        float(hv_series[-1]),
        float(np.percentile(hv_series, 90)),
    )


def compute_regime_factor(hv30: float, iv_atm: float) -> float:
    """Multiplicateur scalaire du score selon HV30/IV_ATM.
    Retourne 1.0 (neutre) si données insuffisantes."""
    if hv30 <= 0 or iv_atm <= 0:
        return 1.0
    ratio = hv30 / iv_atm
    for threshold, factor in config.REGIME_HV_IV_THRESHOLDS:
        if ratio < threshold:
            return factor
    return config.REGIME_HV_IV_THRESHOLDS[-1][1]   # fallback dernier seuil
```

**Conséquence** : `scripts/validate_ranking.py:_hv30_percentiles` est
SUPPRIMÉ et remplacé par un import depuis `scoring/regime.py`. Pas de
duplication (FEAT-029 et FEAT-030 partagent le même code).

### Affichage UI

Indicateur au-dessus du tableau résultats (`ui/page_live.py`) :
```python
ratio = hv30 / iv_atm if (hv30 > 0 and iv_atm > 0) else None
if ratio is None:
    st.caption("Régime : données HV30 indisponibles → score neutre")
elif ratio < 0.60:
    st.success(f"Régime : ✅ vol chère (HV={hv30*100:.0f}%/IV={iv_atm*100:.0f}%) → score ×{regime_factor:.2f}")
elif ratio < 0.85:
    st.caption(f"Régime : neutre (HV={hv30*100:.0f}%/IV={iv_atm*100:.0f}%) → score ×{regime_factor:.2f}")
elif ratio < 1.00:
    st.warning(f"Régime : ⚠️ marché trending (HV={hv30*100:.0f}%/IV={iv_atm*100:.0f}%) → score ×{regime_factor:.2f}")
else:
    st.error(f"Régime : 🔴 tendance forte (HV={hv30*100:.0f}%/IV={iv_atm*100:.0f}%) → score ×{regime_factor:.2f}")
```

### Modifications fichiers

**`config.py`**
```python
# Seuils HV/IV pour le multiplicateur de régime (FEAT-030-B)
# Itérés dans l'ordre : on prend le premier dont le ratio < threshold.
REGIME_HV_IV_THRESHOLDS: list[tuple[float, float]] = [
    (0.60, 1.05),   # hv_iv < 0.60 → factor 1.05 (vol chère)
    (0.85, 1.00),   # 0.60–0.85    → factor 1.00 (normal)
    (1.00, 0.80),   # 0.85–1.00    → factor 0.80 (trending)
    (9.99, 0.55),   # > 1.00       → factor 0.55 (trend fort)
]
```

**`scoring/scorer.py`** : ajouter param `regime_factor: float = 1.0` à
`score_combinations`. Appliqué scalaire après `event_score_factors` :
```python
def score_combinations(
    metrics: ComboMetricsBatch,
    weights: config.ScoreWeights,
    event_score_factors: "xp.ndarray | None" = None,
    regime_factor: float = 1.0,
) -> "xp.ndarray":
    ...
    if event_score_factors is not None:
        score = score * event_score_factors
    score = score * float(regime_factor)
    return score
```

**`ui/page_live.py`** :
```python
from screener.options_analyzer import compute_hv30
from scoring.regime import compute_regime_factor

hv30 = compute_hv30(symbol)              # float ou 0.0
iv_atm = atm_vol                          # déjà calculé
regime_factor = compute_regime_factor(hv30, iv_atm)
# ... après score_combinations :
scores = score_combinations(metrics_batch, weights,
                            event_score_factors=event_factors,
                            regime_factor=regime_factor)
```

**`ui/page_backtest.py`** :
```python
from backtesting.replay import _prefetch_daily_range
from scoring.regime import compute_hv30_from_bars, compute_regime_factor

# AVANT le scan, fetch des bars 90+60j avant as_of
hv_start = as_of - timedelta(days=150)
hv_bars = _prefetch_daily_range(provider, symbol.upper(), hv_start, as_of)
hv30 = compute_hv30_from_bars(hv_bars, as_of)
regime_factor = compute_regime_factor(hv30, atm_vol)
# Note : ces bars seront aussi réutilisées par `compute_hv30_percentiles`
# pour C (vol bands calibrées), donc 1 seul appel Polygon partagé.
```

---

## C — Vol bands calibrées HV p10/p90

### Problème

`vol_low = IV × 0.8` et `vol_high = IV × 1.2` sont identiques pour tous les
symbols et tous les régimes. Or :
- Pour QQQ avec IV=15% (calme), ×1.2 = 18% = HV p90 historique → correct.
- Pour TSLA avec IV=60% (normal), ×1.2 = 72% = seulement HV p70 → trop conservateur.
- En période de stress (VIX > 25), ×1.2 peut être en dessous du p10 historique !

La variante `iv_calibrated` de FEAT-029 teste cette hypothèse empiriquement.
FEAT-030-C l'implémente dans le scan live.

### Algorithme

Tous les helpers HV vivent dans **`scoring/regime.py`** (cf. section B). En
mode live, `data/provider_yfinance.py` ajoute juste un wrapper qui fetche
les closes via yfinance puis appelle les helpers :

```python
# data/provider_yfinance.py — nouveau (méthode YFinanceProvider)
def get_hv30_and_vol_bands(
    self,
    symbol: str,
    lookback_days: int = 90,
) -> tuple[float, float, float]:
    """Retourne (hv30, vol_low_factor, vol_high_factor).
    Fallback : (0.0, config.DEFAULT_VOL_LOW, config.DEFAULT_VOL_HIGH)
    si données insuffisantes."""
    import yfinance as yf
    from scoring.regime import compute_hv30_percentiles, compute_hv30_from_closes

    try:
        hist = yf.download(symbol, period=f"{lookback_days + 60}d",
                           interval="1d", progress=False, auto_adjust=True)
        closes = hist["Close"].squeeze().dropna().to_numpy()
    except Exception:
        return (0.0, config.DEFAULT_VOL_LOW, config.DEFAULT_VOL_HIGH)

    perc = compute_hv30_percentiles(closes, win=21, lookback=lookback_days)
    if perc is None:
        # Tente HV30 seule pour le facteur régime
        hv = compute_hv30_from_closes(closes, win=21)
        return (hv, config.DEFAULT_VOL_LOW, config.DEFAULT_VOL_HIGH)

    p10, current_hv, p90 = perc
    if current_hv < 1e-6:
        return (current_hv, config.DEFAULT_VOL_LOW, config.DEFAULT_VOL_HIGH)
    low  = float(np.clip(p10 / current_hv, 0.40, 0.80))
    high = float(np.clip(p90 / current_hv, 1.20, 2.50))
    return (current_hv, low, high)
```

**Note** : `current_hv = hv30` est retourné dans la même tuple → 1 seul fetch
yfinance pour B et C (cohérent avec l'objectif anti-duplication).

### Activation explicite — checkbox

**Pas d'heuristique fragile** sur la valeur des sliders. On utilise un
boolean explicite venant de `ui/page_params.py` (cf. plus bas).

`params["use_hv_calibration"]: bool` — quand `True`, la calibration est
activée et écrase `vol_low`/`vol_high` ; quand `False`, on garde les
sliders user. Dans les deux cas, on calcule `hv30` (pour le facteur
régime, B).

### Intégration dans `run_scan` (live)

```python
provider = YFinanceProvider()
chain = provider.get_options_chain(symbol)
# ...

if params.get("use_hv_calibration", True):
    hv30, vol_low, vol_high = provider.get_hv30_and_vol_bands(symbol)
else:
    from screener.options_analyzer import compute_hv30
    hv30 = compute_hv30(symbol)
    vol_low, vol_high = params["vol_low"], params["vol_high"]
vol_scenarios = [vol_low, 1.0, vol_high]
```

### Intégration dans `run_backtest_scan` (backtest)

Le backtest n'utilise pas yfinance — on lit le HV depuis Polygon. **Mais**
les bars 150j avant `as_of` doivent être fetchés explicitement (pas
prévu par le pipeline actuel) :

```python
from scoring.regime import (
    compute_hv30_from_bars, compute_hv30_percentiles, compute_regime_factor,
)
from backtesting.replay import _prefetch_daily_range

hv_start = as_of - timedelta(days=150)
hv_bars = _prefetch_daily_range(provider, symbol.upper(), hv_start, as_of)
sorted_closes = np.array(
    [c for d, (c, _) in sorted(hv_bars.items()) if d <= as_of and c > 0],
    dtype=np.float64,
)
hv30 = compute_hv30_from_bars(hv_bars, as_of)

if params.get("use_hv_calibration", True):
    perc = compute_hv30_percentiles(sorted_closes, win=21, lookback=90)
    if perc is not None and perc[1] > 1e-6:
        p10, cur, p90 = perc
        vol_low  = float(np.clip(p10 / cur, 0.40, 0.80))
        vol_high = float(np.clip(p90 / cur, 1.20, 2.50))
    else:
        vol_low, vol_high = params["vol_low"], params["vol_high"]
else:
    vol_low, vol_high = params["vol_low"], params["vol_high"]
vol_scenarios = [vol_low, 1.0, vol_high]
```

### UI — checkbox dans `ui/page_params.py` (PAS sidebar.py)

⚠️ **Correction importante** : depuis FEAT-027, les widgets sont dans
`ui/page_params.py`, pas `ui/components/sidebar.py` (qui ne fait plus
qu'agréger `session_state`). Ajouter la checkbox **dans la section
"Scénarios de volatilité"** de `page_params.py:113-119` :

```python
st.subheader("Scénarios de volatilité")
st.checkbox(
    "Calibration auto HV (p10/p90 sur 90 jours)",
    value=True,
    key="p_use_hv_calibration",
    help="Quand activé : remplace les sliders ci-dessous par des bandes "
         "calibrées sur la distribution historique réelle du symbol "
         "(percentiles 10/90 de la HV30 sur 90 jours). Sinon : utilise "
         "les sliders manuels.",
)
st.caption("Le scénario médian (1.0×) est fixe et sert au filtrage.")
col_v1, col_v2 = st.columns(2)
with col_v1:
    st.slider("Vol basse (×)", 0.5, 0.95, 0.8, 0.05, key="p_vol_low",
              disabled=st.session_state.get("p_use_hv_calibration", True))
with col_v2:
    st.slider("Vol haute (×)", 1.05, 2.0, 1.2, 0.05, key="p_vol_high",
              disabled=st.session_state.get("p_use_hv_calibration", True))
```

Et dans `ui/components/sidebar.py:get_base_params()` :
```python
"use_hv_calibration": bool(ss.get("p_use_hv_calibration", True)),
```

---

## D — Theta/Gamma ratio

### Problème

Deux combos avec le même `max_gain_real_pct` peuvent avoir des profils temps très
différents : l'un accumule du theta régulièrement (`theta_net >> gamma_net`), l'autre
mise sur une convergence de vol. Le ratio theta/gamma mesure directement la qualité
intrinsèque du trade temps.

### Architecture : 2 versions des Greeks

Le pricer batch (`engine/pnl.py`) n'utilise pas les Greeks ; on les
calcule per-combo dans `compute_combo_metrics`. Pour éviter le marshaling
CPU↔GPU coûteux (4 legs × ~100 combos = 400 round-trips CuPy si on
utilise le backend `xp`), on écrit **2 versions** :

- **Versions vectorisées GPU/CPU** dans `engine/black_scholes.py` (signature
  comme `bs_price`, utiles pour les tests de propriétés et un usage
  futur dans le tenseur)
- **Versions CPU pures** dans `engine/black_scholes.py` aussi mais avec
  préfixe `_cpu` — utilisent `numpy` + `scipy.stats.norm` directement.
  Ce sont **celles qu'on appelle dans `compute_combo_metrics`**.

### Constante module-level

Ajouter en haut de **`engine/black_scholes.py`** :
```python
import math
_SQRT_2PI = math.sqrt(2.0 * math.pi)   # ≈ 2.5066282746310002
```

### Versions GPU-aware (utilisées dans les tests)

```python
def _bs_nd1(spot, strike, tte, vol, rate):
    """N'(d1) = PDF gaussienne en d1. Partagée par bs_theta et bs_gamma.
    Vectorisée — supporte les shapes (M, N) comme bs_price."""
    safe_tte = xp.maximum(tte, xp.float32(1e-8))
    safe_vol = xp.maximum(vol, xp.float32(1e-8))
    sqrt_T = xp.sqrt(safe_tte)
    d1 = (xp.log(spot / strike) + (rate + 0.5 * safe_vol ** 2) * safe_tte) / (safe_vol * sqrt_T)
    return xp.exp(-0.5 * d1 ** 2) / xp.float32(_SQRT_2PI)


def bs_gamma(spot, strike, tte, vol, rate):
    """Gamma Black-Scholes européen (identique call/put).
    Γ = N'(d1) / (S × σ × √T)"""
    safe_tte = xp.maximum(tte, xp.float32(1e-8))
    safe_vol = xp.maximum(vol, xp.float32(1e-8))
    nd1 = _bs_nd1(spot, strike, tte, vol, rate)
    result = nd1 / (spot * safe_vol * xp.sqrt(safe_tte))
    return xp.where(xp.isfinite(result), result, xp.float32(0.0))


def bs_theta(option_type, spot, strike, tte, vol, rate):
    """Theta Black-Scholes européen, en $/jour (divisé par 365).
    Θ_call < 0 (coût du temps), Θ_put ≤ 0 (sauf put deep OTM, rare).

    option_type : 0 = call, 1 = put.
    """
    safe_tte = xp.maximum(tte, xp.float32(1e-8))
    safe_vol = xp.maximum(vol, xp.float32(1e-8))
    sqrt_T = xp.sqrt(safe_tte)
    d1 = (xp.log(spot / strike) + (rate + 0.5 * safe_vol ** 2) * safe_tte) / (safe_vol * sqrt_T)
    d2 = d1 - safe_vol * sqrt_T

    nd1 = xp.exp(-0.5 * d1 ** 2) / xp.float32(_SQRT_2PI)
    disc = xp.exp(-xp.float32(rate) * safe_tte)

    decay_term = -(spot * nd1 * safe_vol) / (2.0 * sqrt_T)
    rate_term_call = -xp.float32(rate) * strike * disc * ndtr(d2)
    rate_term_put  = +xp.float32(rate) * strike * disc * ndtr(-d2)

    theta_call = decay_term + rate_term_call
    theta_put  = decay_term + rate_term_put
    is_call = xp.asarray(option_type) == 0
    annual = xp.where(is_call, theta_call, theta_put)
    daily = annual / xp.float32(365.0)
    return xp.where(xp.isfinite(daily), daily, xp.float32(0.0))
```

### Versions CPU pures (utilisées dans `compute_combo_metrics`)

À appeler dans la boucle per-leg ; **n'importent jamais `xp`** — utilisent
`numpy` + `scipy.stats.norm` directement, pas de marshaling GPU :

```python
import numpy as np
from scipy.stats import norm as _norm


def bs_gamma_cpu(spot: float, strike: float, tte: float,
                 vol: float, rate: float) -> float:
    """Gamma scalaire CPU. Retourne 0.0 si non-fini."""
    if tte <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_T = math.sqrt(tte)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / (vol * sqrt_T)
    nd1 = math.exp(-0.5 * d1 * d1) / _SQRT_2PI
    g = nd1 / (spot * vol * sqrt_T)
    return g if math.isfinite(g) else 0.0


def bs_theta_cpu(option_type: str, spot: float, strike: float, tte: float,
                 vol: float, rate: float) -> float:
    """Theta scalaire CPU, en $/jour. option_type : 'call' ou 'put'."""
    if tte <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_T = math.sqrt(tte)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / (vol * sqrt_T)
    d2 = d1 - vol * sqrt_T
    nd1 = math.exp(-0.5 * d1 * d1) / _SQRT_2PI
    disc = math.exp(-rate * tte)
    decay = -(spot * nd1 * vol) / (2.0 * sqrt_T)
    if option_type == "call":
        annual = decay - rate * strike * disc * _norm.cdf(d2)
    else:
        annual = decay + rate * strike * disc * _norm.cdf(-d2)
    daily = annual / 365.0
    return daily if math.isfinite(daily) else 0.0
```

### Calcul per-combo dans `compute_combo_metrics`

```python
from engine.black_scholes import bs_gamma_cpu, bs_theta_cpu

# Dans la boucle for i, combo in enumerate(combinations):
theta_net_i = 0.0
gamma_abs_i = 0.0
for leg in combo.legs:
    K = float(leg.strike)
    T = max(1, (leg.expiration - today).days) / 365.0
    v = float(leg.implied_vol)
    sign = float(leg.direction)
    qty  = float(leg.quantity)

    g = bs_gamma_cpu(current_spot, K, T, v, risk_free_rate) * qty * 100.0
    t = bs_theta_cpu(leg.option_type.lower(), current_spot, K, T, v, risk_free_rate) * qty * 100.0

    gamma_abs_i += abs(sign * g)
    theta_net_i += sign * t

tg_ratio_arr[i] = float(theta_net_i / max(gamma_abs_i, 1e-6))
```

**Signe attendu** : pour un calendar strangle bien construit,
`theta_net > 0` (receveur de temps net) et `gamma_net > 0` (risque de mouvement),
donc `tg_ratio > 0`. Plus le ratio est élevé, meilleur est le trade.

### Filtre disqualifiant optionnel

```python
# Pas de filtre dur par défaut — le scorer suffisamment pénalise les ratios négatifs
# Option pour V2 : mask &= (tg_ratio >= 0) pour forcer des positions theta-positives
```

### Composante de score

```python
s_tg = _normalize(_fillna_with_median(metrics.tg_ratio))   # normalisé [0,1]
# Pas de 1-normalize : on veut que les tg_ratio les plus ÉLEVÉS scorent le mieux
score += w.w_tg_ratio * s_tg
```

### Modifications `ComboMetricsBatch`

Les 2 nouveaux champs (les arrays peuvent contenir NaN qui sera comblé
par `_fillna_with_median` dans le scorer) :
```python
term_slope: "xp.ndarray"   # shape (C,) — IV_near_mean / IV_far_mean (NaN si K=1)
tg_ratio: "xp.ndarray"     # shape (C,) — theta_net / gamma_abs en $/jour ÷ $
```

---

## E — Fenêtre ±1σ HV-ajustée

### Problème

`max_gain_real_pct` = gain max dans la fenêtre `IV × sqrt(T/365)`. Mais l'IV
implicite est systématiquement **en dessous** du mouvement réellement réalisé
(la vol réalisée historique HV excède l'IV environ 30% du temps). La fenêtre est
donc trop étroite en régime de marché actif.

Fix minimal : utiliser `max(IV, HV30)` comme amplitude de la fenêtre.

### Modification `scoring/filters.py`

```python
def realistic_max_gain(
    pnl_mid,
    spot_range,
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    hv30: float = 0.0,         # nouveau param — 0.0 = backward-compat
) -> "xp.ndarray":
    effective_vol = max(atm_vol, hv30) if hv30 > 0 else atm_vol
    T = max(days_to_close, 1) / 365.0
    half_range = effective_vol * math.sqrt(T)
    ...
```

### Modification `scoring/metrics.py`

```python
def compute_combo_metrics(
    ...
    hv30: float = 0.0,         # nouveau param — passé depuis run_scan
) -> ComboMetricsBatch:
    ...
    effective_vol_i = max(atm_vol_i, hv30) if hv30 > 0 else atm_vol_i
    half = effective_vol_i * math.sqrt(days_i / 365.0)
    ...
```

### Propagation

Dans `run_scan` et `run_backtest_scan` :
```python
metrics_batch = compute_combo_metrics(
    ...,
    hv30=hv30,   # float calculé en amont (voir améliorations B et C)
)
```
Et dans `filter_combinations` :
```python
valid_indices = filter_combinations(
    ...,
    hv30=hv30,
)
```

---

## Mise à jour de `ScoreWeights` (config.py)

Les 2 nouveaux composants (term_slope, tg_ratio) s'ajoutent au score composite.
Les poids sont re-normalisés à somme = 1.0.

```python
@dataclass
class ScoreWeights:
    # FEAT-026 (existants — légèrement réduits pour faire de la place)
    w_gain_real:   float = 0.20   # était 0.25
    w_annualized:  float = 0.18   # était 0.20
    w_loss_prob:   float = 0.13   # était 0.15
    w_max_loss:    float = 0.09   # était 0.10
    w_liquidity:   float = 0.09   # était 0.10
    w_robustness:  float = 0.08   # était 0.10
    w_slippage:    float = 0.08   # était 0.10
    # FEAT-030 (nouveaux)
    w_term_slope:  float = 0.10   # pente de terme — qualité calendaire
    w_tg_ratio:    float = 0.05   # theta/gamma — qualité du trade temps
    # Total = 1.00
```

### Mise à jour des 9 sliders — `ui/page_params.py:_WEIGHT_FIELDS`

⚠️ **Pas dans `sidebar.py`** (qui ne contient plus de widgets depuis FEAT-027).
Modifier la liste **`_WEIGHT_FIELDS`** dans `ui/page_params.py:10-26` en
ajoutant 2 entrées :

```python
_WEIGHT_FIELDS = [
    # ... 7 entrées existantes ...
    ("w_term_slope", "Pente de terme",
     "IV_near / IV_far. Récompense les structures calendaires "
     "avec vol proche plus chère que vol lointaine. K=1 → score neutre."),
    ("w_tg_ratio", "Theta/Gamma",
     "Theta net ÷ |Gamma net| à spot courant. Mesure la qualité "
     "intrinsèque du trade temps (théta capté vs risque directionnel)."),
]
```

`scripts/validate_ranking.py` utilise `ScoreWeights()` (defaults) — backward
compatible car les nouveaux champs ont des defaults.

---

## Impact sur l'UI

### Clés à ajouter au dict `metrics[i]` (run_scan + run_backtest_scan)

Aux lignes 161-181 de `ui/page_live.py` et 194-210 de `ui/page_backtest.py`,
ajouter :
```python
"term_slope":  float(term_slope_cpu[i]),    # NaN possible (K=1)
"tg_ratio":    float(tg_ratio_cpu[i]),      # toujours fini (clamps dans bs_*_cpu)
```
où `term_slope_cpu = to_cpu(metrics_batch.term_slope)` et idem pour `tg_ratio`.

### Clés ajoutées au dict de retour de `run_scan` / `run_backtest_scan`

```python
return {
    ...,
    "hv30": hv30,                      # float (0.0 si indisponible)
    "iv_atm": atm_vol,                 # float
    "regime_factor": regime_factor,    # float (1.0 si neutre)
}
```

### Résultats table (`ui/components/results_table.py`)

Deux nouvelles colonnes après `"Disp. vol"` :

| Colonne | Source | Format | NaN |
|---|---|---|---|
| `Pente IV` | `metrics["term_slope"]` | `"1.18×"` (2 décimales) | `"—"` (K=1) |
| `θ/Γ` | `metrics["tg_ratio"]` | `"+2.4"` (1 décimale) | `"—"` |

Pas de coloriage — Streamlit DataFrame brut ne le permet pas
trivialement. Texte noir uniforme. Si l'utilisateur veut visuellement
distinguer θ/Γ > 0 vs ≤ 0, on pourra ajouter un emoji dans la cellule
("✓ +2.4" vs "✗ −0.5") en V2.

### Indicateur régime au-dessus du tableau

Cf. section B "Affichage UI" — implémenté dans `ui/page_live.py` et
`ui/page_backtest.py` après le scan, avant l'appel à `render_results_table`.
Lit `result["hv30"]`, `result["iv_atm"]`, `result["regime_factor"]`.

---

## Spec technique — nouveaux paramètres `filter_combinations`

Signature complète après FEAT-030 :

```python
def filter_combinations(
    pnl_tensor,
    spot_range,
    net_debits,
    avg_volumes,
    criteria: ScoringCriteria,
    current_spot: float,
    atm_vol: float,
    days_to_close: int,
    risk_free_rate: float,
    term_slope_per_combo: "xp.ndarray | None" = None,   # FEAT-030-A
    hv30: float = 0.0,                                   # FEAT-030-E
) -> "xp.ndarray":
```

---

## Fichiers modifiés

| Fichier | Nature de la modification |
|---|---|
| `config.py` | `MIN_TERM_STRUCTURE_SLOPE`, `REGIME_HV_IV_THRESHOLDS`, mise à jour `ScoreWeights` (9 composants) |
| `engine/black_scholes.py` | Constante `_SQRT_2PI` ; fonctions GPU `_bs_nd1`, `bs_gamma`, `bs_theta` ; fonctions CPU `bs_gamma_cpu`, `bs_theta_cpu` |
| **`scoring/regime.py`** (NOUVEAU) | `compute_hv30_from_closes`, `compute_hv30_from_bars`, `compute_hv30_percentiles`, `compute_regime_factor` |
| `scoring/metrics.py` | helper `compute_term_slopes`, params `term_slope_arr` / `hv30`, 2 nouveaux champs `ComboMetricsBatch`, calcul `tg_ratio` per-leg |
| `scoring/filters.py` | params `term_slope_per_combo` / `hv30` dans `realistic_max_gain` et `filter_combinations`, filtre disqualifiant term_slope (skip NaN) |
| `scoring/scorer.py` | 2 nouveaux composants (`s_ts`, `s_tg`), param `regime_factor`, mise à jour docstring |
| `data/provider_yfinance.py` | méthode `get_hv30_and_vol_bands(symbol, lookback_days)` |
| `ui/page_live.py` | Pré-calcul `term_slopes_all` ; HV30 via `screener.compute_hv30` ; vol bands via `get_hv30_and_vol_bands` (si `use_hv_calibration`) ; `regime_factor` ; propagation `hv30` ; nouveaux champs dans dict `metrics` et dict de retour ; affichage indicateur régime |
| `ui/page_backtest.py` | Idem + fetch explicite des bars 150j avant `as_of` pour HV30/percentiles via `_prefetch_daily_range` |
| **`ui/page_params.py`** | Checkbox "Calibration auto HV" + sliders disabled selon checkbox + 2 nouveaux items dans `_WEIGHT_FIELDS` |
| `ui/components/sidebar.py` | `get_base_params()` lit `p_use_hv_calibration` (1 ligne) |
| `ui/components/results_table.py` | Colonnes `Pente IV` et `θ/Γ` |
| `scripts/validate_ranking.py` | Suppression de `_hv30_percentiles` local → import depuis `scoring/regime.py` |
| `docs/specs/option_scanner_spec_v2.md` | §6.4 score v3 + §5 filtre term slope + §A3 Greeks |

**Fichiers non modifiés** : `engine/pnl.py`, `engine/combinator.py`,
`data/models.py`, `backtesting/`, `tracker/`, `screener/` (la fonction
`screener.options_analyzer.compute_hv30` est réutilisée telle quelle).

---

## Tests à créer / mettre à jour

| Fichier test | Cas à couvrir |
|---|---|
| `tests/test_black_scholes.py` | GPU : `bs_gamma`, `bs_theta` (valeurs connues call/put ITM/OTM/ATM, γ > 0, θ_call < 0, relation θ ≈ -½σ²S²Γ). CPU : `bs_gamma_cpu`, `bs_theta_cpu` mêmes scenarios + tte=0 → 0.0 |
| **`tests/test_regime.py`** (NOUVEAU) | `compute_hv30_from_closes` (cas trivial 21 closes vs np.std), `compute_hv30_percentiles` (None si <60 closes), `compute_regime_factor` (4 buckets de seuils, fallback hv=0 → 1.0) |
| `tests/test_pnl.py` | Pas de changement (Greeks non utilisés dans le tenseur) |
| `tests/test_scoring.py` | `compute_term_slopes` : K=1 → NaN, K=2 calendar IV_near>IV_far → ratio>1 ; tg_ratio > 0 pour calendar bien formé ; filtre `term_slope < 0.95` disqualifie les backwardations ; filtre skippe les NaN (K=1 passe) |
| `tests/test_scan_vs_direct.py` | Vérifier que `hv30=0` (default) ne change rien au Test B (diff = $0.00 à spot[0]). Le combo_parser doit toujours passer `hv30=0` pour rester comparable au scan en mode `use_hv_calibration=False`. |

---

## Ordre d'implémentation

1. **`engine/black_scholes.py`** — `_SQRT_2PI` + Greeks GPU + Greeks CPU (indépendant, testable)
2. **`config.py`** — `MIN_TERM_STRUCTURE_SLOPE`, `REGIME_HV_IV_THRESHOLDS`, `ScoreWeights` 9 composants
3. **`scoring/regime.py`** (nouveau) — helpers HV30 + percentiles + regime_factor
4. **`scoring/metrics.py`** — `compute_term_slopes`, params `term_slope_arr` / `hv30`, tg_ratio per-leg
5. **`scoring/filters.py`** — `hv30` + `term_slope_per_combo` params + filtre disqualifiant
6. **`scoring/scorer.py`** — `s_ts`, `s_tg`, param `regime_factor`
7. **`data/provider_yfinance.py`** — `get_hv30_and_vol_bands` (wrapper sur `scoring/regime`)
8. **`ui/page_live.py`** + **`ui/page_backtest.py`** — pipeline complet (term_slopes pré-calc, HV30, regime, nouveaux dict keys)
9. **`ui/page_params.py`** — checkbox `p_use_hv_calibration` + 2 sliders dans `_WEIGHT_FIELDS`
10. **`ui/components/sidebar.py`** — 1 ligne pour propager le checkbox
11. **`ui/components/results_table.py`** — colonnes `Pente IV` et `θ/Γ`
12. **`scripts/validate_ranking.py`** — supprimer `_hv30_percentiles`, importer depuis `scoring/regime`
13. Tests : `test_black_scholes.py`, **`test_regime.py`** (nouveau), `test_scoring.py`, `test_scan_vs_direct.py`
14. `docs/specs/option_scanner_spec_v2.md` — §6.4, §5, §A3

---

## Dépendance à FEAT-029

L'amélioration **C** (vol bands calibrées HV p10/p90) implémente la
variante `iv_calibrated` testée empiriquement par FEAT-029. Le déploiement
**par défaut** dépend du résultat :

| Résultat FEAT-029 sur `iv_calibrated` | Action FEAT-030-C |
|---|---|
| Bat `current` sur ≥ 2 métriques (Spearman / TopK / Bias) | Checkbox `p_use_hv_calibration = True` par défaut |
| Match nul | Checkbox = `False` par défaut, fonction disponible pour l'utilisateur |
| Perd | Implémenter quand même (la logique est utile pour ceux qui veulent la tester) mais checkbox = `False` et docs explicites |

Les améliorations **A**, **B**, **D**, **E** sont **indépendantes** des
résultats FEAT-029 (elles testent des dimensions non couvertes par les 5
variantes du backtest — pente de terme, régime HV/IV, theta/gamma,
fenêtre HV-ajustée).

---

## Décisions associées (hors FEAT-030 mais déclenchées par FEAT-029)

Selon les résultats FEAT-029, deux décisions séparées seront à prendre.
Elles ne sont **pas** dans le scope FEAT-030 car ce sont juste des
changements de défauts, pas de structure :

- Si **`days_bc_0`** gagne → changer `p_days_before_close` défaut de 3 à 0
  dans `ui/page_params.py:147` et le défaut de
  `combinations_to_tensor(days_before_close=0)` dans `engine/pnl.py`.
- Si **`bs_eur`** gagne → changer le radio "Pricer" défaut de
  "Américain" à "Européen" dans `ui/page_params.py:170`.

Documenter dans `docs/specs/option_scanner_spec_v2.md` §A le choix retenu.

---

## Validation empirique post-FEAT-030

Une fois FEAT-030 mergée, **réutiliser le cadre FEAT-029** pour valider
empiriquement que le ranking s'améliore :

1. Ajouter une variante `feat_030` dans
   `scripts/validate_ranking.py:VARIANTS` :
   ```python
   "feat_030": {
       "days_before_close": 3,
       "use_american_pricer": True,
       "vol_factors": (0.8, 1.2),     # ignoré, override par vol_calibration
       "vol_calibration": True,        # FEAT-030-C
       "use_term_slope_filter": True,  # FEAT-030-A
       "use_regime_factor": True,      # FEAT-030-B
       "use_hv_window": True,          # FEAT-030-E
       "score_weights": "feat_030",    # 9 composants avec w_term_slope + w_tg_ratio
       "random_pick": False,
   },
   ```
2. Re-lancer `python -m scripts.validate_ranking` (sur Tulear, cache déjà chaud) :
   - Tous les autres steps déjà checkpointés sont skippés.
   - Seuls les 90 nouveaux (feat_030, symbol, as_of) tournent.
   - Estimation : ~10h supplémentaires (90 steps × ~6 min).
3. Critère d'acceptation FEAT-030 :
   - Spearman_rank(`feat_030`) > Spearman_rank(`current`) + 0.05
   - **ET** TopK_mean(`feat_030`) > TopK_mean(`current`) + 1 pt
4. Si non atteint : revue critique des poids `ScoreWeights` ou désactivation
   composant par composant (A/B/C/D/E) pour identifier le coupable, puis
   re-validation.

Cette boucle de validation **est obligatoire** avant de considérer
FEAT-030 comme "DONE" dans `option_scanner_spec_v2.md`.

---

## Limitations et cas limites connus

- **Theta américain** : `bs_theta` / `bs_theta_cpu` utilisent la formule
  européenne. Pour les options américaines avec dividende, l'erreur est < 5%
  sur le theta. Acceptable pour le scoring, pas pour du pricing exact.
- **Term slope K=1** : combos RIC/backspread (1 seule expiration) reçoivent
  `term_slope = NaN`, comblé par `_fillna_with_median` → score neutre.
  Le filtre `MIN_TERM_STRUCTURE_SLOPE` les laisse passer (NaN n'est pas
  comparable à 0.95).
- **HV30 indisponible hors-séance** : `get_hv30_and_vol_bands` retourne
  `(0.0, DEFAULT_VOL_LOW, DEFAULT_VOL_HIGH)` si yfinance ne retourne pas
  d'historique. `regime_factor = 1.0` (neutre) et `hv30 = 0` → comportement
  équivalent à pré-FEAT-030 sur les composants B et E.
- **Backtest HV30** : calculée sur les 90+60 jours *avant* `as_of`,
  jamais après. Correct car on simule ce qu'on aurait vu à la date du scan.
- **Backtest fetch supplémentaire** : ajout d'un `_prefetch_daily_range`
  des 150 jours avant `as_of` pour calculer HV/percentiles. C'est 1 appel
  Polygon supplémentaire par scan backtest (cache hit dès la 2e exécution
  du même `(symbol, as_of)`).
- **Normalisation du tg_ratio** : la normalisation min-max écrase les
  différences si tous les combos ont un tg_ratio proche. Envisager une
  normalisation linéaire avec cap à un quantile p95 pour V2.
- **Saisie directe (`combo_parser.py`)** : ne calcule **pas** `hv30` ni
  `term_slope`. Pour rester comparable au scan, le test `test_scan_vs_direct.py`
  doit forcer `use_hv_calibration=False` côté scan. La saisie directe ne
  bénéficie donc pas des composants B/C/E mais utilise toujours A et D
  (term_slope et tg_ratio sont calculés à partir des legs eux-mêmes).
