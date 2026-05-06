# FEAT-023 — Refonte du screener de sous-jacents

**Status:** SPEC
**Date:** 2026-05-06
**Auteur:** Val + Claude
**Liens :** BUG-027 (fallback), BUG-028 (FOMC), FEAT-004 (screener V1)

## Contexte et motivation

Le screener actuel (FEAT-004) retourne quasi systématiquement des résultats issus
du *fallback* (BUG-027) — c.-à-d. des tickers explicitement disqualifiés. Le user
ne peut donc pas s'y fier pour choisir ses sous-jacents. Trois causes :

1. **Élimination injustifiée par événements macro** (BUG-028) — les jours
   FOMC/NFP/CPI, 100 % de l'univers est rejeté par `critical_event_in_near`.
2. **Mesure de liquidité non représentative** — moyennes/médianes calculées sur
   la chaîne entière (50–200 strikes) au lieu de la zone effectivement utilisée
   par les templates (ATM ±10 %).
3. **Scoring imprécis** — `iv_rank_proxy = IV/HV30` est un substitut grossier ;
   pas de mesure du comportement du sous-jacent (auto-corr, ATR, gaps) ; pas de
   différenciation calendar vs reverse iron condor.

Cette refonte se fait en **3 étapes commitables séparément**, du plus urgent au
plus structurel.

## Stratégies cibles

Le screener doit produire des sous-jacents adaptés aux templates suivants
(`templates/`) :

| Template | Profil | Sweet spot vol |
|---|---|---|
| `calendar_strangle` | long vega, long theta court terme | IV bas–modéré, plat |
| `double_calendar` | long vega, long theta sur 2 strikes | idem + ex-div interdit |
| `reverse_iron_condor` | long gamma, profite des gros mouvements | IV bas, vol réalisée qui s'accélère |
| `call_diagonal_backspread` | long vol asymétrique haussier | IV bas, skew limité |
| `call_ratio_diagonal` | mixte directionnel | IV modéré |

Tous : **4 jambes, 2 expirations**, sensibilité forte à la liquidité multi-strikes.

---

## Étape 1 — Fix urgent : événements macro non éliminatoires (BUG-028)

### Objectif
Permettre au screener de fonctionner les jours FOMC/NFP/CPI/GDP en distinguant
événements **macro** (corrélés au marché entier) et **micro** (spécifiques au ticker).

### Modifications

**`screener/scorer.py`** — règle `critical_event_in_near` :
```python
"critical_event_in_near": lambda m: any(
    ev.impact == EventImpact.CRITICAL and ev.scope == EventScope.MICRO
    for ev in m.events_in_danger_zone
),
```

Importer `EventScope` depuis `events.models`.

**`screener/scorer.py`** — ajouter une nouvelle pénalité multiplicative dans
`compute_score` pour les events MACRO en danger zone :
```python
# Macro CRITICAL en danger zone : pénalité forte mais pas éliminatoire
macro_critical_in_near = any(
    ev.impact == EventImpact.CRITICAL and ev.scope == EventScope.MACRO
    for ev in metrics.events_in_danger_zone
)
if macro_critical_in_near:
    penalty *= config.SCREENER_PENALTY_MACRO_CRITICAL  # 0.6
```

**`config.py`** — ajouter :
```python
SCREENER_PENALTY_MACRO_CRITICAL: float = 0.6
```

**`screener/models.py`** — `OptionsMetrics` doit déjà exposer
`events_in_danger_zone: list[MarketEvent]` (déjà le cas, OK).

### Tests

**`tests/test_screener_scoring.py`** — ajouter :
- `test_critical_micro_event_disqualifies` : un MarketEvent micro CRITICAL en
  danger zone → `check_disqualification` retourne `"critical_event_in_near"`.
- `test_critical_macro_event_does_not_disqualify` : un MarketEvent macro CRITICAL
  en danger zone → `check_disqualification` retourne `None` ; le score est
  multiplié par 0.6.
- `test_high_macro_in_danger_no_disqualification` : event HIGH macro → pas
  d'élimination, pénalité via `event_score_factor` uniquement.

### Critères d'acceptation
- ✅ Le 2026-05-06 (jour FOMC), le screener retourne ≥ 5 qualifiés réels (sans fallback).
- ✅ Les tickers avec earnings dans la near zone restent éliminés (`event_filter.py`).
- ✅ Aucun ticker disqualifié uniquement à cause d'un FOMC/NFP/CPI.
- ✅ Tests `tests/test_screener_scoring.py` passent.

### Impact spec
- `docs/specs/option_scanner_spec_v2.md` § 14 — clarifier la séparation macro/micro.

### Estimé
~30 minutes, Sonnet OK.

---

## Étape 2 — Liquidité ciblée ATM (refonte mesures et seuils)

### Objectif
Mesurer la liquidité **uniquement sur la zone ATM ±10 %** pour refléter
ce que les templates 4 jambes utilisent réellement, et calibrer les seuils
en conséquence.

### Modifications

**`screener/options_analyzer.py`** — refondre `compute_chain_liquidity` :

```python
def compute_atm_liquidity(
    chain_df,
    spot: float,
    atm_band_pct: float = 0.10,   # ±10% du spot
) -> ChainLiquidity:
    """
    Calcule les métriques de liquidité sur les strikes ATM ±atm_band_pct uniquement.

    Retourne un dataclass ChainLiquidity avec :
        - spread_pct_median  : spread bid/ask en % du mid (médiane sur ATM±band)
        - spread_dollar_med  : spread bid/ask en $ absolus (médiane)
        - volume_median      : volume médian sur ATM±band
        - volume_p25         : volume 25e percentile (mesure la jambe la plus faible)
        - oi_median          : OI médian sur ATM±band
        - oi_p25             : OI 25e percentile
        - strike_count_in_band : nb de strikes dans la zone ATM±band
        - mid_min, mid_max   : range des prix observés
    """
```

Détails :
- Filtrer `df` sur `abs(strike - spot) <= spot * atm_band_pct`.
- Calculer les médianes ET le 25e percentile (le 25e percentile détecte le strike
  le plus faible parmi ceux qui pourraient être utilisés — important pour 4 jambes).
- Spread $ : `df["ask"] - df["bid"]` directement, pas en %.
- Préserver le fallback OI sentinelle pour hors-séance (déjà géré, à conserver).

**`screener/options_analyzer.py`** — `analyze_ticker` mesure liquidité sur
**calls + puts** (les templates utilisent les deux côtés). Concaténer
`near_chain.calls` et `near_chain.puts` avant de passer à `compute_atm_liquidity`.

**`screener/models.py`** — étendre `OptionsMetrics` :
```python
# Liquidité ATM (calls + puts, ±10%)
spread_pct_atm_near: float
spread_pct_atm_far: float
spread_dollar_atm_near: float
spread_dollar_atm_far: float
volume_atm_p25_near: float
volume_atm_p25_far: float
oi_atm_p25_near: float
oi_atm_p25_far: float
strike_count_atm_near: int
strike_count_atm_far: int
```

Retirer (ou marquer deprecated) les champs basés sur la chaîne entière qui
deviennent redondants.

**`screener/scorer.py`** — règles éliminatoires recalibrées :
```python
DISQUALIFICATION_RULES = {
    # Spread % ATM > 12% sur near OU far (4 jambes = 4 spreads, 12%×4 ≈ 48% du débit perdu)
    "spread_too_wide": lambda m: max(m.spread_pct_atm_near, m.spread_pct_atm_far) > 0.12,
    # Volume p25 ATM < 20 sur near (la jambe la plus faible n'a presque rien)
    "no_volume_atm": lambda m: m.volume_atm_p25_near < 20,
    # OI p25 ATM < 50 (sauf sentinelle hors-séance) sur near
    "no_oi_atm": lambda m: (
        m.oi_atm_p25_near < 999_000 and m.oi_atm_p25_near < 50
    ),
    # Pas assez de strikes dans la zone ATM±10% (besoin de 4 strikes mini)
    "not_enough_strikes_atm": lambda m: min(
        m.strike_count_atm_near, m.strike_count_atm_far
    ) < 4,
    "iv_data_missing": lambda m: m.iv_atm_near <= 0 or m.iv_atm_far <= 0,
    "critical_event_in_near": (...idem étape 1...),
}
```

**`config.py`** — exposer les nouveaux seuils :
```python
SCREENER_ATM_BAND_PCT: float = 0.10       # ±10% du spot pour mesurer liquidité
SCREENER_MAX_SPREAD_PCT_ATM: float = 0.12 # 12% spread max sur ATM
SCREENER_MIN_VOLUME_P25: int = 20         # volume 25e perc. min sur ATM
SCREENER_MIN_OI_P25: int = 50             # OI 25e perc. min sur ATM
SCREENER_MIN_STRIKES_ATM: int = 4         # strikes mini dans ATM±band
```

**Score "tradabilité" (composante du score composite)** :
```python
def _score_tradability(metrics: OptionsMetrics) -> float:
    """
    Score 0-1 : coût d'entrée + sortie 4 jambes en % du prix moyen ATM.
    Cible : score = 1.0 quand spread total < 5%, score = 0 quand > 30%.
    """
    avg_spread = (metrics.spread_pct_atm_near + metrics.spread_pct_atm_far) / 2
    # 4 jambes × spread aller-retour = 4 × spread% (entrée vendue au bid, sortie idem)
    cost_pct = 4 * avg_spread
    return max(0.0, min(1.0, 1.0 - (cost_pct - 0.05) / (0.30 - 0.05)))
```

Le score liquidité existant `_score_liquidity` est remplacé par
`_score_tradability` + un sous-score volume/OI sur ATM±band.

### Tests

**`tests/test_screener_filters.py`** — nouveaux tests :
- `test_atm_liquidity_excludes_otm_wings` : chaîne avec ATM liquide + wings
  illiquides ⇒ `compute_atm_liquidity` retourne les stats des seuls ATM.
- `test_atm_band_uses_calls_and_puts` : la zone ATM combine les deux côtés.
- `test_volume_p25_detects_weak_leg` : 4 strikes ATM dont 1 à volume=10 ⇒
  p25 ≤ 10 ⇒ disqualifie via `no_volume_atm`.
- `test_tradability_score_penalizes_wide_spread` : spread 15 % ⇒ score < 0.2.

**`tests/test_screener_integration.py`** — vérifier qu'avec un univers `["SPY", "QQQ"]`
en séance, ces deux tickers passent tous les filtres ATM (= ne sont pas en fallback).

### Critères d'acceptation
- ✅ SPY, QQQ, IWM, AAPL, MSFT, NVDA passent tous les filtres en séance normale.
- ✅ Les tickers avec ATM liquide mais wings morts ne sont plus rejetés à tort.
- ✅ Score tradabilité retourne valeurs cohérentes (testées sur 3 cas connus).
- ✅ Tests existants passent toujours (régression).

### Impact spec
- `docs/specs/option_scanner_spec_v2.md` § 14 — section liquidité refondue.

### Estimé
~2 h, Sonnet OK avec spec ci-dessus respectée à la lettre. Surveiller les
tests : si un test passe avant le fix mais échoue après, c'est suspect.

---

## Étape 3 — Scoring multi-stratégie + métriques comportementales

### Objectif
Remplacer le score composite générique par **deux scores spécialisés** —
"calendar-friendly" (theta long, vol stable) et "RIC-friendly" (gamma long, vol
qui s'accélère) — alimentés par de vraies métriques comportementales du sous-jacent.

L'utilisateur choisit le profil dans l'UI (`Sidebar` → nouveau dropdown
"Stratégie cible").

### Métriques comportementales à ajouter

**Nouveau module `screener/behavior.py`** :

```python
@dataclass
class UnderlyingBehavior:
    symbol: str
    autocorr_1d: float           # auto-corr lag-1 sur 60j (mean revert si proche 0 ou négatif)
    atr_pct: float               # ATR_20 / spot
    gap_rate_2pct: float         # % jours avec |gap_open| > 2% sur 60j
    hv_ratio_20_60: float        # HV20 / HV60 (>1.2 = vol qui accélère)
    trend_strength: float        # |close[0] - close[-30]| / (ATR_20 * sqrt(30))
    beta_spy: float              # régression linéaire log-rendements sur 60j vs SPY
    range_position: float        # (close - min_30) / (max_30 - min_30), 0-1


def batch_compute_behavior(
    symbols: list[str],
    benchmark: str = "SPY",
    lookback_days: int = 90,
) -> dict[str, UnderlyingBehavior]:
    """
    Un seul appel `yf.download(symbols + [benchmark], period='6mo')`.
    Calcule toutes les métriques en pandas/numpy, sans nouvelle requête réseau.
    """
```

À appeler dans `screener.py` étape 5, en parallèle de `batch_compute_hv30`.

### Vrai IV Rank (au lieu du proxy IV/HV)

**Nouveau `screener/iv_rank.py`** :

```python
def compute_iv_rank_52w(
    symbol: str,
    current_iv_atm: float,
) -> float:
    """
    IV Rank vrai sur 252 jours = (IV_today - IV_min_52w) / (IV_max_52w - IV_min_52w) × 100.

    yfinance ne fournit pas l'historique de l'IV ATM. Approximation acceptée :
    reconstruire IV historique via une fenêtre HV sliding (HV21 sur 252j) en
    appliquant un facteur d'ajustement constant `IV/HV` calibré sur le ratio
    actuel. C'est imparfait mais bien meilleur que IV/HV30 instantané.

    Si la donnée IV historique devient disponible (Polygon paid, Tradier),
    remplacer par le vrai historique IV ATM.
    """
```

Documenter la limite dans la docstring : "approximation HV-derived ; remplacer
par vraie IV historique quand disponible (FEAT-024)".

### Skew

**`screener/options_analyzer.py`** — `analyze_ticker` calcule :

```python
# Skew 25-delta : IV(25d put) - IV(25d call), en % de IV ATM
skew_25d_near = compute_25d_skew(near_chain, spot, days_to_exp=near_days)
```

Approximation 25-delta : strike au delta ≈ 0.25, ou défaut 5-7 % OTM.

### Score multi-stratégie

**`screener/scorer.py`** — remplacer `compute_score` par :

```python
def compute_score_calendar(metrics: OptionsMetrics, behavior: UnderlyingBehavior) -> float:
    """
    Score 0-100 pour stratégies calendar/double-calendar.
    Privilégie : IV Rank modéré, term structure plat, vol stable, mean revert.
    """
    return 100 * (
        0.25 * _score_iv_rank_calendar(metrics.iv_rank_52w)         # sweet spot 25-60
        + 0.20 * _score_term_structure_calendar(metrics.term_structure_ratio)  # 0.97-1.07
        + 0.20 * _score_tradability(metrics)
        + 0.10 * _score_density(metrics)
        + 0.10 * _score_calmness(behavior)                          # mean revert + low ATR + low gap
        + 0.10 * _score_iv_realized(metrics.iv_atm_near, behavior.atr_pct * 16)  # IV/HV ≈ 1.0-1.3
        + 0.05 * _score_events(metrics.event_score_factor)
    ) * _penalties(metrics, behavior)


def compute_score_ric(metrics: OptionsMetrics, behavior: UnderlyingBehavior) -> float:
    """
    Score 0-100 pour reverse iron condor.
    Privilégie : IV Rank bas, vol qui accélère, trend ou range cassé.
    """
    return 100 * (
        0.30 * _score_iv_rank_ric(metrics.iv_rank_52w)              # bas (<35) = bon
        + 0.20 * _score_vol_acceleration(behavior.hv_ratio_20_60)   # >1.2 bon
        + 0.20 * _score_tradability(metrics)
        + 0.15 * _score_atr(behavior.atr_pct)                       # ATR > 1.5% bon
        + 0.10 * _score_density(metrics)
        + 0.05 * _score_events(metrics.event_score_factor)
    ) * _penalties(metrics, behavior)
```

### Univers révisé

**`screener/universe.py`** — supprimer/marquer "haute vol" :
- Retirer : `MRNA`, `BIIB`, `NIO`, `BABA` (pertinence calendar discutable)
- Marquer "high-vol" (alias `HIGH_VOL_TICKERS`) : `COIN`, `PLTR`, `SQ`, `SHOP`
- Vérifier `USO` — si options sur futures, retirer (cohérent avec exclusion VIX)
- Ajouter ETFs defensifs/diversifiants : `EFA`, `IEMG`, `LQD`, `IEF`

L'UI permet à l'utilisateur d'inclure ou exclure les "high-vol" via une case à
cocher.

### UI

**`ui/components/sidebar.py`** — nouveau bloc "Stratégie cible" :
- Radio : `Calendar / Double Calendar` | `Reverse Iron Condor` | `Auto (mix)`
- Case "Inclure tickers haute vol" (default off)
- Affichage du score : montre le score selon la stratégie choisie

**`ui/components/results_table.py`** — afficher les colonnes :
`Score | IV Rank 52w | Term ratio | Spread% ATM | Vol ATM p25 | ATR% | HV20/HV60 | Events`.

### Tests

`tests/test_behavior.py` (nouveau) :
- `test_autocorr_mean_revert` : série synthétique mean-revert ⇒ autocorr < 0.
- `test_atr_pct` : valeur connue sur série synthétique.
- `test_gap_rate` : 6 gaps sur 60j ⇒ rate = 0.10.

`tests/test_iv_rank.py` (nouveau) :
- `test_iv_rank_at_max` : current_iv = max_52w ⇒ rank ≈ 100.
- `test_iv_rank_at_min` : current_iv = min_52w ⇒ rank ≈ 0.

`tests/test_scoring_multi.py` (nouveau) :
- `test_calendar_score_prefers_low_iv_stable` : ticker IV bas + vol stable
  > ticker IV haut + vol erratique pour score calendar.
- `test_ric_score_prefers_vol_acceleration` : inverse pour RIC.

### Critères d'acceptation
- ✅ Sur 2026-05-06 en séance, top 5 calendar = mix d'ETFs et mega-caps
  (SPY/QQQ/IWM probablement dans le top 3).
- ✅ Top 5 RIC = sous-jacents avec ATR%, HV20/HV60 élevés (probablement TSLA,
  NVDA si vol qui accélère).
- ✅ Le user peut basculer entre profils sans relancer le screener (cache des
  metrics + behavior, recompute scores seulement).
- ✅ Tous tests passent.

### Impact spec
- `docs/specs/option_scanner_spec_v2.md` § 14 — refonte complète scoring section.
- Incrémenter version en en-tête.

### Estimé
~6-8 h, **Opus recommandé** :
- Logique IV Rank approximée (edge cases sur tickers récents, IPO < 1 an).
- Pondérations à ajuster sur cas réels après premier run.
- Coordination metrics ↔ behavior ↔ scoring sans incohérences.

---

## Plan de commits

| # | Commit | Modèle |
|---|---|---|
| 1 | `FIX BUG-028: events macro non éliminatoires` | Opus (fait dans cette session) |
| 2 | `FEAT-023 étape 2: liquidité ATM-ciblée` | Sonnet (avec spec ci-dessus) |
| 3 | `FEAT-023 étape 3: scoring multi-stratégie + behavior` | Opus |

Chaque commit doit :
1. Passer tous les tests existants + nouveaux tests de l'étape.
2. Être validé manuellement par Val sur ANQA (`192.168.0.133:8501`) avant commit.
3. Mettre à jour `docs/specs/option_scanner_spec_v2.md` et `docs/tasks/TASKS.md`.

## Risques et points d'attention

- **Régression sur cas hors-séance** : la sentinelle `_OI_UNAVAILABLE = 999_999`
  doit être préservée dans `compute_atm_liquidity`. Tester explicitement.
- **yfinance rate limit** : `batch_compute_behavior` ajoute 1 requête (déjà
  batch). Acceptable.
- **IV Rank approximé** : signaler dans l'UI avec ⓘ "approximation HV-based —
  vrai IV Rank nécessite data payante (FEAT-024 future)".
- **Cache** : si on recompute le score quand l'utilisateur change de stratégie,
  il faut cacher `OptionsMetrics` + `UnderlyingBehavior` (Streamlit
  `@st.cache_data` sur le résultat de `screen()` étape 1+2).
