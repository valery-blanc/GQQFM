# BUG-020 — Tracker sync via git push cassé → 0 combos sur Avignon

**Statut :** FIXED · Commit : (à venir)

---

## Symptôme

Page "Tracker prix réel" :
- UI locale affiche N combos (lus depuis `data/tracked_combos.json` local)
- Sous-titre Avignon indique "0 combo(s) trackés, 0 mesures en base"
- Aucune donnée collectée même pendant les heures de marché

## Reproduction

1. Scanner → cliquer "Tracker ce combo" sur un combo
2. Aller sur la page Tracker → voir le combo localement
3. L'API `/health` sur Avignon retourne `{"combos": 0, "total_price_rows": 0}`

## Cause racine

Le mécanisme de sync entre Streamlit (Windows) et le Docker Avignon passait par
`git push` (subprocess Streamlit → GitHub → `git pull` Docker toutes les 5 min).

Ce mécanisme échoue systématiquement car :
1. Le subprocess `streamlit run` ne dispose pas des credentials git (SSH key /
   token HTTPS) du shell de l'utilisateur
2. Les erreurs git sont catchées et n'affichent qu'un `st.warning` facilement manqué
3. Même si le push réussit, un délai de ≤5 min s'écoule avant que le Docker tire

Résultat : `tracked_combos.json` reste à `{"combos": []}` dans le repo GitHub →
le Docker ne voit jamais les combos → `collect_once()` quitte sans rien appeler.

## Fix appliqué

Remplacement du git push par des appels HTTP directs à l'API FastAPI d'Avignon :

| Avant | Après |
|---|---|
| `COMBOS_PATH.write_text(...)` + `git commit` + `git push` | `POST http://192.168.0.222:8502/combos` |
| `_save_combos(updated_list)` (suppression) → git push | `DELETE http://192.168.0.222:8502/combos/{id}` |
| `_load_combos()` lit fichier local | `GET /combos` sur l'API |

Changements côté Docker :
- `COMBOS_PATH` déplacé de `REPO_DIR/data/` vers `DATA_DIR/` (volume persistant)
- `docker-compose.yml` : volume `tracker_data` devient **bind mount** sur disque hôte
  Avignon (`~/tracker-data:/data`) → survie aux réinstallations Docker
- Service `gqqfm-clone` et volume `gqqfm_repo` supprimés (plus nécessaires)
- `pull_repo()` supprimé de `main.py` et du scheduler
- Nouveaux endpoints : `POST /combos` et `DELETE /combos/{id}`

## Section spec impactée

`docs/specs/FEAT-019-tracker-prix-reels.md` — architecture mise à jour.
