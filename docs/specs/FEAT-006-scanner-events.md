# FEAT-006 — Correction du filtrage événementiel dans la sélection d'expirations

**Statut :** DONE
**Date :** 2026-03-26

> Priorité : haute (le fix actuel est trop permissif et peut laisser passer des combinaisons risquées)

---

## 1. Contexte

Le scanner intègre un calendrier d'événements macro (FOMC, NFP, CPI, etc.) pour scorer les paires d'expirations. Les événements CRITICAL (FOMC, NFP) dans la "danger zone" (entre aujourd'hui et l'expiration near) sont censés être éliminatoires car un gap de prix pendant la vie des legs courts peut provoquer des pertes hors-profil.

## 2. Le problème observé

**Date du test :** 26 mars 2026. Le NFP tombe le vendredi 3 avril (J+8).

Le `SCANNER_NEAR_EXPIRY_RANGE` est `(5, 21)` jours. Toutes les expirations near dans cette plage (1er au 16 avril) ont le NFP dans leur danger zone `[today, near_expiry]` :

```
near = 1 apr (6j)  → danger zone = [26 mar, 1 apr]  → NFP 3 apr = HORS zone ✓
near = 2 apr (7j)  → danger zone = [26 mar, 2 apr]  → NFP 3 apr = HORS zone ✓
near = 3 apr (8j)  → danger zone = [26 mar, 3 apr]  → NFP 3 apr = DANS zone ✗
near = 10 apr (15j) → danger zone = [26 mar, 10 apr] → NFP 3 apr = DANS zone ✗
near = 17 apr (22j) → danger zone = [26 mar, 17 apr] → NFP 3 apr = DANS zone ✗
```

Les expirations near du 1er et 2 avril (6-7j) sont en dessous du minimum de 5j — elles passent le filtre de plage. Mais les near du 3 avril et au-delà sont toutes bloquées par le NFP. Résultat : AUCUNE paire valide dans la plage normale.

Il existait aussi l'expiration du 30 mars (4j), sous le minimum de 5j donc exclue d'office.

## 3. Le fix actuel (mauvais)

La correction appliquée : si toutes les paires sont bloquées par CRITICAL en danger zone, les conserver quand même avec un `event_score_factor` réduit.

**Pourquoi c'est dangereux :** dans un autre scénario (par exemple near = 10 avril, NFP le 3 avril), le NFP tombe en plein milieu de la vie des legs courts. Un rapport NFP surprise peut provoquer un gap de 1-2% sur SPY en quelques minutes, faisant franchir les strikes des legs courts. Le filtre CRITICAL existe pour empêcher exactement ça — l'assouplir uniformément supprime une protection essentielle.

**Pourquoi ça a fonctionné par accident :** le résultat sélectionné (near = 30 mars, far = 18 juin) avait le NFP APRÈS l'expiration near. Le NFP était en sweet zone, pas en danger zone. Le fallback permissif a laissé passer une bonne combinaison, mais il aurait tout aussi bien pu en laisser passer une mauvaise.

## 4. La bonne correction

Le problème n'est pas dans le filtre CRITICAL — c'est dans le fait que la recherche ne considère pas les expirations near en dessous du minimum de plage quand toutes les paires normales sont bloquées.

### 4.1 Algorithme révisé de `_select_event_pairs`

```
Entrées :
  available_expirations : list[date]
  near_range : (min_days, max_days)     # ex: (5, 21)
  far_range : (min_days, max_days)      # ex: (25, 70)
  event_calendar : EventCalendar

Étape 1 — Paires normales :
  near_candidates = expirations dans [near_range.min, near_range.max]
  far_candidates = expirations dans [far_range.min, far_range.max]
  paires = toutes les (near, far) avec far - near ≥ 10 jours

  Pour chaque paire :
    classify_events_for_pair(near, far) → profil
    Si profil.has_critical_in_danger → EXCLURE la paire
    Sinon → conserver avec son event_score_factor

  Si des paires survivent → choisir la meilleure → TERMINÉ

Étape 2 — Extension near vers le bas (fallback structuré) :
  near_extended = expirations dans [2, near_range.min - 1]
    c'est-à-dire les expirations trop courtes pour la plage normale
    mais techniquement valides (≥ 2 jours)

  paires_ext = toutes les (near_ext, far) avec far - near_ext ≥ 10 jours

  Pour chaque paire :
    classify_events_for_pair(near_ext, far) → profil
    Si profil.has_critical_in_danger → EXCLURE
    Sinon → conserver avec event_score_factor
      ET marquer la combinaison avec un warning "near_expiry_short"

  Si des paires survivent → choisir la meilleure → TERMINÉ
    (le warning "near_expiry_short" sera affiché dans l'UI)

Étape 3 — Extension far vers le haut (second fallback) :
  Si toujours aucune paire :
  far_extended = expirations dans [far_range.max + 1, far_range.max + 30]

  Mêmes règles que l'étape 2, combinées avec near normal ET near étendu.

  Si des paires survivent → choisir la meilleure → TERMINÉ

Étape 4 — Dernier recours :
  Si AUCUNE paire ne passe après les extensions :
  Prendre la meilleure paire parmi les paires normales (étape 1)
  AVEC le CRITICAL en danger zone, appliquer le facteur réduit,
  ET ajouter un warning explicite :
  "⚠ Événement {name} le {date} pendant la vie des legs courts.
   Risque de gap de prix. Vérifiez le profil P&L attentivement."

  Ce cas ne devrait survenir que si le marché a très peu d'expirations
  disponibles (ticker peu liquide ou période de jours fériés).
```

### 4.2 Cas concret du 26 mars avec le NFP du 3 avril

```
Étape 1 :
  near_candidates dans [5, 21]j = [31 mar (5j), 1 apr (6j), 2 apr (7j),
                                    3 apr (8j), 10 apr (15j), 17 apr (22j)]
  Paires avec CRITICAL check :
    near=31 mar → danger=[26 mar, 31 mar] → NFP 3 apr HORS zone → ✓ VALIDE
    near=1 apr  → danger=[26 mar, 1 apr]  → NFP 3 apr HORS zone → ✓ VALIDE
    near=2 apr  → danger=[26 mar, 2 apr]  → NFP 3 apr HORS zone → ✓ VALIDE
    near=3 apr  → danger=[26 mar, 3 apr]  → NFP 3 apr DANS zone → ✗ EXCLU
    near=10 apr → danger=[26 mar, 10 apr] → NFP 3 apr DANS zone → ✗ EXCLU
    near=17 apr → danger=[26 mar, 17 apr] → NFP 3 apr DANS zone → ✗ EXCLU

  Paires valides avec far dans [25, 70]j :
    (31 mar, 17 apr), (31 mar, 24 apr), (31 mar, 1 mai), ..., (31 mar, 18 jun)
    (1 apr, 17 apr), (1 apr, 24 apr), ...
    (2 apr, 17 apr), ...

  Toutes ces paires ont le NFP en sweet zone → event_score_factor > 1.0
  → Meilleure paire sélectionnée → TERMINÉ à l'étape 1

  Pas besoin du fallback.
```

### 4.3 Autre cas : FOMC le 6 mai, on est le 20 avril

```
Étape 1 :
  near_candidates dans [5, 21]j = [25 apr (5j), 1 mai (11j), 8 mai (18j)]
  FOMC check :
    near=25 apr → danger=[20 apr, 25 apr] → FOMC 6 mai HORS zone → ✓
    near=1 mai  → danger=[20 apr, 1 mai]  → FOMC 6 mai HORS zone → ✓
    near=8 mai  → danger=[20 apr, 8 mai]  → FOMC 6 mai DANS zone → ✗

  Paires (25 apr, far) et (1 mai, far) avec FOMC en sweet zone
  → Sélection normale, pas de fallback nécessaire
```

### 4.4 Cas extrême : 2 CRITICAL dans la fenêtre complète

```
Ex: NFP le 3 avril ET FOMC le 6 mai, on est le 25 mars.

Étape 1 :
  near=31 mar → NFP 3 apr HORS danger, FOMC 6 mai HORS danger → ✓
    sweet zone = [1 apr, far_expiry]
    NFP 3 apr en sweet zone → factor bonus
    FOMC 6 mai en sweet zone → double bonus

  → Très bon candidat, sélectionné directement
```

## 5. Modifications de code

### 5.1 `engine/combinator.py` — `_select_event_pairs`

Remplacer la logique actuelle (fallback permissif) par l'algorithme en 4 étapes de la section 4.1.

**Signature inchangée :**

```python
def _select_event_pairs(
    available_expirations: list[date],
    near_range: tuple[int, int],
    far_range: tuple[int, int],
    event_calendar: EventCalendar,
) -> list[tuple[date, date, float, list[str], str | None]]:
    """
    Retourne les meilleures paires d'expirations.

    Chaque élément : (near, far, event_score_factor, sweet_events, warning)
    - warning : None si paire normale, sinon message d'avertissement
      ex: "Near expiry très court (4j), prime de calendar réduite"
      ex: "⚠ Événement NFP le 2026-04-03 pendant la vie des legs courts"

    Retourne au maximum 3 paires (triées par event_score_factor décroissant).
    """
```

### 5.2 `templates/base.py` — `Combination`

Ajouter un champ warning optionnel :

```python
@dataclass
class Combination:
    legs: list[Leg]
    net_debit: float
    close_date: date
    template_name: str
    event_score_factor: float = 1.0
    events_in_sweet_zone: list[str] = None
    event_warning: str | None = None          # NOUVEAU
```

### 5.3 `ui/components/results_table.py`

Si `event_warning` est présent sur la combinaison sélectionnée, l'afficher dans les détails :

```python
if combination.event_warning:
    st.warning(combination.event_warning)
```

### 5.4 `ui/components/chart.py`

Si `event_warning` est présent, ajouter une annotation rouge sur le graphique P&L (en plus de l'annotation dorée pour les sweet events) :

```python
if combination.event_warning:
    fig.add_annotation(
        text=f"⚠ {combination.event_warning}",
        xref="paper", yref="paper",
        x=0.02, y=0.02,
        showarrow=False,
        font=dict(size=11, color="red"),
    )
```

## 6. Suppression du fix actuel

Le fallback permissif actuel (conserver toutes les paires si toutes sont bloquées) doit être **supprimé** et remplacé par l'algorithme en 4 étapes. L'étape 4 (dernier recours) est le seul cas où une paire avec CRITICAL en danger zone est conservée, et elle est accompagnée d'un warning explicite.

## 7. Tests

### 7.1 test_select_event_pairs.py

```
Test 1 — Étape 1 suffit (cas normal) :
  Expirations : [5j, 12j, 19j, 33j, 47j]
  FOMC à jour 25 (entre 19j et 33j)
  → Paires (5j, 33j), (12j, 33j), (19j, 33j) valides
  → FOMC en sweet zone, event_score_factor > 1.0
  → Pas de warning

Test 2 — Étape 1 suffit, near très proche du CRITICAL :
  Expirations : [5j, 7j, 8j, 10j, 33j, 47j]
  NFP à jour 8
  → near=5j et near=7j → NFP HORS danger zone → ✓
  → near=8j → NFP DANS danger zone → ✗
  → near=10j → NFP DANS danger zone → ✗
  → Paires (5j, 33j) et (7j, 33j) sélectionnées, pas de warning

Test 3 — Étape 2 nécessaire (extension near) :
  Expirations : [4j, 10j, 15j, 33j, 47j]
  near_range = (5, 21), donc 4j est hors plage normale
  NFP à jour 8 → near=10j et near=15j bloqués
  → Étape 1 : aucune paire valide
  → Étape 2 : near=4j, danger=[today, today+4j] → NFP jour 8 HORS zone → ✓
  → Paire (4j, 33j) sélectionnée avec warning "Near expiry très court (4j)"

Test 4 — Étape 4 nécessaire (dernier recours) :
  Expirations : [10j, 33j]  (très peu d'expirations)
  NFP à jour 8 → near=10j bloqué
  Pas d'expiration en dessous de 5j (pas de fallback étape 2)
  → Étape 4 : (10j, 33j) conservée avec factor réduit
  → Warning "⚠ Événement NFP le ... pendant la vie des legs courts"

Test 5 — Aucun événement :
  Expirations : [5j, 12j, 33j, 47j]
  Pas d'événement dans la fenêtre
  → Toutes les paires à factor=1.0
  → Sélection par écart far-near maximal (préférer (5j, 47j))

Test 6 — Rétro-compatibilité sans event_calendar :
  event_calendar = None
  → Comportement identique à avant FEAT-006
```
