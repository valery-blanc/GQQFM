# Options Scanner — Reste à faire (delta spec)

> Base : `option_scanner_spec_v2.md` (FEAT-003 / BUG-003, tout implémenté)
> Objectif : intégrer le module EventCalendar et préparer l'accueil du screener

---

## 1. Résumé des modifications

Ce document décrit UNIQUEMENT les changements à apporter au scanner existant.
Il ne répète pas ce qui est déjà implémenté. Lire en parallèle avec
`SPEC_UNDERLYING_SCREENER.md` qui spécifie le module EventCalendar en détail
(section 3).

| # | Modification | Fichiers impactés | Effort |
|---|-------------|-------------------|--------|
| 1 | Nouveau module `events/` (partagé) | Nouveau package | Moyen |
| 2 | Combinator : multi-paires d'expirations avec scoring événementiel | `engine/combinator.py` | Moyen |
| 3 | Scorer : facteur événementiel sur le score des combinaisons | `scoring/scorer.py` | Faible |
| 4 | UI : affichage indicateur événementiel | `ui/components/results_table.py` | Faible |
| 5 | Config : paramètres EventCalendar | `config.py` | Trivial |
| 6 | Dépendances : finnhub-python | `requirements.txt` | Trivial |
| 7 | Structure projet : `events/` à la racine | Arborescence | Trivial |

---

## 2. Nouveau module `events/` (partagé scanner + screener)

Ce module est spécifié en détail dans `SPEC_UNDERLYING_SCREENER.md` section 3.
Il est placé à la racine du projet, au même niveau que `engine/`, `scoring/`,
`screener/`.

```
options-scanner/
├── ...
├── events/                        # NOUVEAU — partagé
│   ├── __init__.py
│   ├── calendar.py                # EventCalendar (interface unifiée)
│   ├── fomc_calendar.py           # Table statique FOMC 2026
│   ├── finnhub_calendar.py        # Client Finnhub + mapping événements
│   └── models.py                  # MarketEvent, EventImpact, EventScope
├── engine/
├── scoring/
├── screener/                      # NOUVEAU — implémenté via SPEC_UNDERLYING_SCREENER.md
└── ...
```

### 2.1 Résumé de l'API EventCalendar (référence)

```python
from events.calendar import EventCalendar
from events.models import MarketEvent, EventImpact

calendar = EventCalendar(finnhub_api_key="...")  # ou None pour FOMC seuls
calendar.load(from_date=date.today(), to_date=date(2026, 6, 30))

# Récupérer les événements dans une plage
events = calendar.get_events_in_range(start, end, min_impact=EventImpact.HIGH)

# Classifier pour une paire d'expirations
profile = calendar.classify_events_for_pair(near_expiry=date(...), far_expiry=date(...))
# profile = {
#     "danger_zone": [...],        # événements [today, near_expiry]
#     "sweet_zone": [...],         # événements [near_expiry+1, far_expiry]
#     "has_critical_in_danger": bool,
#     "has_high_in_sweet": bool,
#     "event_score_factor": float, # multiplicateur pour le scoring
# }
```

Pour le détail complet (modèles, FOMC statique, Finnhub, formule du
`event_score_factor`), voir `SPEC_UNDERLYING_SCREENER.md` sections 3.2 à 3.5.

---

## 3. Modification du Combinator

### 3.1 Situation actuelle

Le Combinator a déjà le flag `use_adjacent_expiry_pairs` (A6 dans l'annexe v2) :
- `True` : itère sur toutes les paires (NEAR, FAR) à 5-45j d'écart
- `False` : utilise `(expirations[0], expirations[-1])`

Les templates 1-3 (calendar strangle, double calendar, reverse iron condor)
utilisent `use_adjacent_expiry_pairs=False`, donc une seule paire fixe.

### 3.2 Ce qui change

Pour TOUS les templates (pas seulement les diagonales), le Combinator doit
maintenant considérer plusieurs paires d'expirations et intégrer le profil
événementiel de chaque paire.

**Nouveau paramètre de `generate_combinations` :**

```python
def generate_combinations(
    template: TemplateDefinition,
    chain: OptionsChain,
    event_calendar: EventCalendar | None = None,  # NOUVEAU
    max_combinations: int = 500_000,
    max_iterations: int = 2_000_000,
) -> list[Combination]:
    """
    Changements par rapport à l'implémentation actuelle :

    1. Si event_calendar est fourni ET template.use_adjacent_expiry_pairs == False :
       - Au lieu de prendre une seule paire fixe (expirations[0], expirations[-1]),
         générer les paires candidats :
           near ∈ expirations qui tombent dans SCREENER_NEAR_EXPIRY_RANGE
           far ∈ expirations qui tombent dans SCREENER_FAR_EXPIRY_RANGE
           avec far - near ≥ 10 jours
       - Pour chaque paire, calculer event_score_factor via
         event_calendar.classify_events_for_pair(near, far)
       - Sélectionner les 3 meilleures paires par event_score_factor
         (pour limiter l'explosion combinatoire)
       - Générer les combinaisons pour chacune de ces 3 paires
       - Stocker event_score_factor dans chaque Combination

    2. Si event_calendar est fourni ET template.use_adjacent_expiry_pairs == True :
       - Comportement existant (toutes les paires 5-45j)
       - PLUS : calculer event_score_factor pour chaque paire
       - Stocker dans Combination

    3. Si event_calendar est None :
       - Comportement identique à l'actuel (rétro-compatible)
       - event_score_factor = 1.0 pour toutes les combinaisons

    Le event_calendar est passé par le pipeline principal (app.py).
    Il est chargé UNE FOIS au début du scan, pas par combinaison.
    """
```

### 3.3 Modification de `Combination`

```python
@dataclass
class Combination:
    """Une combinaison de 2 à 4 legs."""
    legs: list[Leg]
    net_debit: float
    close_date: date
    template_name: str
    event_score_factor: float = 1.0           # NOUVEAU — multiplicateur événementiel
    events_in_sweet_zone: list[str] = None     # NOUVEAU — noms des événements favorables
    # field(default_factory=list) pour events_in_sweet_zone dans l'implémentation
```

### 3.4 Impact sur l'espace de recherche

Pour les templates 1-3 (actuellement 1 paire fixe) :
- Avant : ~100K-500K combinaisons
- Après (avec events, 3 paires max) : ~300K-1.5M combinaisons
- Le GPU gère facilement ce volume (traiter en 2-3 batches si nécessaire)

Pour les templates 4-5 (déjà multi-paires) :
- Pas de changement de volume, juste l'ajout de event_score_factor

---

## 4. Modification du Scorer

### 4.1 Situation actuelle

Le scorer calcule un score composite basé sur :
- `w1 × gain_loss_ratio` (0.4)
- `w2 × (1 - loss_prob)` (0.3)
- `w3 × expected_return` (0.3)

### 4.2 Ce qui change

Le `event_score_factor` de chaque combinaison est appliqué comme **multiplicateur
sur le score final**, pas comme composante additionnelle.

```python
def score_combinations(
    pnl_mid: xp.ndarray,
    net_debits: xp.ndarray,
    loss_probs: xp.ndarray,
    event_score_factors: xp.ndarray,     # NOUVEAU — shape (C_filtered,)
    criteria: ScoringCriteria,
) -> xp.ndarray:
    """
    Score = (w1 * gain_loss + w2 * (1-loss_prob) + w3 * expected_return)
          × event_score_factor

    event_score_factor vaut :
    - 1.0 si pas d'événement (neutre, pas d'impact)
    - > 1.0 si événement favorable en sweet zone (ex: 1.15 avec un FOMC)
    - < 1.0 si événement défavorable en danger zone (ex: 0.4 avec un FOMC)
      Note : les combinaisons avec CRITICAL en danger zone sont déjà
      éliminées par le Combinator (il ne génère pas de paires avec
      has_critical_in_danger=True). Les facteurs < 1.0 ici concernent
      les événements MODERATE en danger zone.

    Si event_score_factors est None (rétro-compatibilité) :
    traiter comme un vecteur de 1.0.
    """
```

### 4.3 Transmission du tenseur event_score_factors

Le pipeline dans `app.py` doit extraire les `event_score_factor` des
`Combination` et les passer au scorer sous forme de tenseur :

```python
# Dans le pipeline principal, après generate_combinations() :
event_factors = xp.array([c.event_score_factor for c in combinations], dtype=xp.float32)
# Passer à score_combinations() après le filtrage (re-indexer sur les survivants)
```

---

## 5. Modification de l'UI

### 5.1 Tableau des résultats

Ajouter une colonne optionnelle "Events" dans le tableau, affichée uniquement
si au moins une combinaison a `events_in_sweet_zone` non vide.

```python
# ui/components/results_table.py

# Si des événements sont présents dans les résultats :
if any(r.events_in_sweet_zone for r in results):
    df["Events"] = [
        ", ".join(r.events_in_sweet_zone) if r.events_in_sweet_zone else "—"
        for r in results
    ]
```

### 5.2 Graphique P&L

Ajouter une annotation textuelle sur le graphique si la combinaison
sélectionnée a un événement en sweet zone :

```python
# ui/components/chart.py

if combination.events_in_sweet_zone:
    events_str = ", ".join(combination.events_in_sweet_zone)
    fig.add_annotation(
        text=f"★ Events between expirations: {events_str}",
        xref="paper", yref="paper",
        x=0.02, y=0.98,
        showarrow=False,
        font=dict(size=11, color="gold"),
    )
```

---

## 6. Modification du pipeline principal

### 6.1 Chargement de l'EventCalendar

Le calendrier est chargé UNE FOIS au début du scan, avant la boucle
sur les tickers. Il est réutilisé pour chaque ticker.

```python
# ui/app.py — dans le handler du bouton "LANCER LE SCAN"

from events.calendar import EventCalendar
from config import FINNHUB_API_KEY, SCREENER_FAR_EXPIRY_RANGE

# Charger le calendrier événementiel (1 requête Finnhub)
event_calendar = EventCalendar(finnhub_api_key=FINNHUB_API_KEY)
try:
    event_calendar.load(
        from_date=date.today(),
        to_date=date.today() + timedelta(days=SCREENER_FAR_EXPIRY_RANGE[1] + 7),
    )
except Exception:
    event_calendar = None  # fallback : pas d'événements, factor=1.0

# Boucle sur les tickers (existante)
for symbol in symbols:
    chain = data_provider.get_options_chain(symbol)

    # Passer event_calendar au combinator
    combos = combinator.generate_combinations(
        template, chain,
        event_calendar=event_calendar,  # NOUVEAU
    )

    # ... reste du pipeline inchangé, sauf :
    # extraire event_score_factors et passer au scorer
```

### 6.2 Rétro-compatibilité

Si `FINNHUB_API_KEY` n'est pas configuré et que les FOMC statiques sont
hors de la fenêtre de dates, `event_calendar.classify_events_for_pair()`
retourne `event_score_factor=1.0`. Le scanner se comporte exactement comme
avant. Aucune régression.

---

## 7. Ajouts à config.py

```python
# ── EventCalendar (NOUVEAU) ──
FINNHUB_API_KEY: str | None = None     # Clé API Finnhub (gratuite, 60 req/min)
                                        # Peut être défini via env var FINNHUB_API_KEY
                                        # Si None : FOMC statiques uniquement

EVENT_PENALTY_CRITICAL_IN_NEAR = 0.4   # × 0.4 par événement CRITICAL en danger zone
EVENT_PENALTY_MODERATE_IN_NEAR = 0.7   # × 0.7 par événement MODERATE en danger zone
EVENT_BONUS_HIGH_IN_SWEET = 0.05       # + 0.05 par événement HIGH+ en sweet zone
EVENT_BONUS_MODERATE_IN_SWEET = 0.02   # + 0.02 par événement MODERATE en sweet zone
EVENT_BONUS_CAP = 0.15                 # bonus plafonné à +0.15
```

L'import de `FINNHUB_API_KEY` depuis une variable d'environnement :

```python
import os
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", None)
```

---

## 8. Ajout à requirements.txt

```
# Events calendar
finnhub-python>=2.4                  # Calendrier économique (CPI, NFP, GDP)
```

---

## 9. Tests

### 9.1 test_combinator_events.py (NOUVEAU)

```
Test 1 - Combinator sans event_calendar (rétro-compat) :
  generate_combinations(template, chain, event_calendar=None)
  → Toutes les combinaisons ont event_score_factor=1.0
  → Même nombre de résultats qu'avant

Test 2 - Combinator avec event_calendar, pas d'événement dans la fenêtre :
  Mocker un EventCalendar vide (aucun événement)
  → event_score_factor=1.0 pour toutes les combinaisons
  → Résultats identiques au test 1

Test 3 - Combinator avec FOMC en sweet zone :
  Template calendar_strangle, expirations disponibles = [7j, 14j, 28j, 42j]
  Mocker un FOMC à jour 21 (entre 14j et 28j)
  → La paire (14j, 28j) doit être incluse
  → event_score_factor > 1.0 pour les combos de cette paire

Test 4 - Combinator avec FOMC en danger zone :
  Mocker un FOMC à jour 10 (dans la zone near)
  → Les paires dont near_expiry > 10j sont exclues (si CRITICAL)
  → Ou event_score_factor < 1.0 (si MODERATE)

Test 5 - Multi-paires pour templates 1-3 :
  Vérifier que le combinator génère des combinaisons pour
  plusieurs paires d'expirations (pas juste la première/dernière)
  quand un event_calendar est fourni
```

### 9.2 test_scorer_events.py (NOUVEAU)

```
Test 6 - Score avec event_score_factor=1.0 :
  → Score identique au scoring actuel (rétro-compat)

Test 7 - Score avec event_score_factor=1.15 :
  → Score = score_base × 1.15

Test 8 - Score avec event_score_factor=0.7 :
  → Score = score_base × 0.7

Test 9 - Classement : deux combinaisons identiques sauf event_score_factor :
  combo_a : factor=1.15 (FOMC en sweet)
  combo_b : factor=1.0 (pas d'événement)
  → combo_a classée avant combo_b
```

---

## 10. Ordre d'implémentation recommandé

```
1. Implémenter le module events/ (indépendant, testable seul)
   → test_event_calendar.py (tests 10-13 de SPEC_UNDERLYING_SCREENER.md)

2. Modifier Combination (ajouter 2 champs, valeurs par défaut)
   → Aucun test cassé (rétro-compatible)

3. Modifier generate_combinations (paramètre event_calendar)
   → test_combinator_events.py (tests 1-5)
   → Vérifier que les tests existants passent toujours (event_calendar=None)

4. Modifier score_combinations (paramètre event_score_factors)
   → test_scorer_events.py (tests 6-9)
   → Vérifier que les tests existants passent toujours

5. Modifier app.py (chargement calendar, passage aux fonctions)
   → Test manuel via Streamlit

6. Modifier UI (colonne Events, annotation graphique)
   → Test manuel via Streamlit

7. Ajouter config.py + requirements.txt
```

Chaque étape est rétro-compatible : si `event_calendar=None`, le comportement
est identique à l'existant. Les tests existants ne doivent jamais casser.
