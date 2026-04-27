# FEAT-012 — Fetch live du taux sans risque (^IRX) + plages DTE strictes

## Statut
DONE

## Contexte

1. Le taux sans risque était une constante hardcodée (`DEFAULT_RISK_FREE_RATE = 0.045`).
   La roadmap V2 prévoyait de fetcher `^IRX` (T-bill 13 semaines via Yahoo)
   pour avoir un taux à jour. Au moment de FEAT-012, le taux réel est
   ~3.6 %, soit 0.9 point sous la constante.

2. **Bug FEAT-011** : malgré le slider `near_expiry_range = (14, 35)`,
   l'utilisateur voyait des combos avec une jambe courte expirant dans 2 jours.
   La cause : `_select_event_pairs` (FEAT-006) avait un fallback "étape 2"
   qui étendait la plage near vers le bas (jusqu'à 2 jours) quand toutes
   les paires normales étaient bloquées par un CRITICAL event. Ce fallback
   précédait FEAT-011 et ne respectait pas la nouvelle contrainte stricte.

## Comportement

### Live ^IRX

Nouveau module `data/risk_free_rate.py` :

```python
def fetch_risk_free_rate() -> tuple[float, str]:
    """Returns (rate_decimal, source) where source ∈ {"live", "fallback"}."""
```

- Fetch `^IRX` via `yf.Ticker("^IRX").history(period="5d")`.
- ^IRX est coté en pourcent → division par 100 pour obtenir un taux décimal.
- Validation : `0 < rate < 20 %` (sinon fallback).
- En cas d'erreur (réseau, data manquante, valeur aberrante) : retourne
  `config.DEFAULT_RISK_FREE_RATE` avec source `"fallback"`.

### Sidebar

- Cache `@st.cache_data(ttl=3600)` autour du fetch (1 fetch / heure).
- Le `st.number_input` "Taux sans risque" affiche la valeur live comme défaut.
- Caption sous le champ : `✓ ^IRX live — 3.593 %` ou `⚠ fallback constante — 4.500 %`.

### YFinanceProvider

`get_risk_free_rate()` retourne désormais le taux live (au lieu de la constante).

### Plages DTE strictes (fix bug)

`_select_event_pairs` réduit de 4 étapes à 2 :
- **Étape 1** — paires normales (near ∈ near_range, far ∈ far_range), CRITICAL exclus.
- **Étape 2** — dernier recours : paires dans la plage user, CRITICAL accepté + warning explicite.

Les anciennes étapes 2 (extension near vers [2, near_min-1]) et 3 (extension far
vers [far_max+1, far_max+30]) sont supprimées : elles violaient la contrainte stricte
imposée par les sliders FEAT-011.

Si aucune paire valide dans la plage user → retourne `[]` et le combinator
utilise `_build_default_pairs` (qui respecte aussi la plage).

## Fichiers modifiés

- `data/risk_free_rate.py` (nouveau)
- `data/provider_yfinance.py` — `get_risk_free_rate` utilise le live
- `ui/components/sidebar.py` — cache + caption source
- `engine/combinator.py` — `_select_event_pairs` simplifié (2 étapes)
- `tests/test_select_event_pairs.py` — `test_step2_near_extension` renommé
  et réécrit en `test_strict_range_no_near_extension`
- `docs/specs/option_scanner_spec_v2.md` — version FEAT-012, §A3 mis à jour
- `docs/tasks/TASKS.md`
