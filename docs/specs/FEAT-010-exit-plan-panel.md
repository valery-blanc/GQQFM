# FEAT-010 — Panneau "Plan de sortie" dans combo_detail

## Statut
DONE

## Contexte

Le scanner identifie une combinaison à entrer mais ne fournit aucune
indication sur **quand** la fermer. L'utilisateur doit appliquer mentalement
des règles standard (target +30 %, stop -50 %, J-3 avant la première
expiration short, fermeture post-event).

## Comportement

Affichage d'un nouveau bloc dans `combo_detail.py`, sous les métriques
principales et au-dessus du tableau des legs.

### Contenu du panneau

4 cellules `st.metric` ou équivalent :

| Cellule | Valeur | Calcul |
|---|---|---|
| **Target profit (+30 %)** | `+$X` | `net_debit * 0.30` |
| **Stop loss (−50 %)** | `−$X` | `net_debit * 0.50` |
| **Date butoir** | `JJ MMM YYYY` | `close_date − 3 jours calendaires` |
| **Jours restants** | `N j` | `(date_butoir − today).days`, rouge si < 5 |

### Règle "post-event" (conditionnelle)

Si `combination.events_in_sweet_zone` est non vide, afficher un
`st.info` au-dessus des métriques :

> 📅 Sortie post-event recommandée : fermer dès le lendemain de
> {events_in_sweet_zone[0]} (l'IV crush attendu est la thèse de la position)

### Règle d'invalidation directionnelle (note statique)

Texte explicatif sous les métriques en `st.caption` :

> Couper aussi si : spot sort de ±15 % du strike central (thèse vol/temps
> cassée) ou si la perte courante atteint le stop −50 %.

## Spec technique

- Pas de nouveau champ dans le modèle `Combination` — tout est calculé
  à l'affichage à partir de `net_debit`, `close_date`, `events_in_sweet_zone`
- Fonction privée `_render_exit_plan(combination)` dans `combo_detail.py`
- Appelée depuis `render_combo_detail`, après les 4 metrics et avant le
  warning ex-div

## Impact sur l'existant

- Aucun changement dans le modèle de données
- Aucun changement dans le pricer / scorer / combinator
- UI uniquement, dans `ui/components/combo_detail.py`

## Fichiers modifiés

- `ui/components/combo_detail.py` — nouvelle fonction `_render_exit_plan`
- `docs/specs/option_scanner_spec_v2.md` — section UI mise à jour
- `docs/tasks/TASKS.md` — entrée FEAT-010
