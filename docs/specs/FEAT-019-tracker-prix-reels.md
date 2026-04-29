# FEAT-019 — Tracker de prix réels (Avignon Docker)

**Statut :** IMPLÉMENTÉ (code en place) — mécanisme de sync BUGUÉ (voir BUG ci-dessous)
**Branche :** master · Commit initial : d6fd75d

---

## Contexte

Le backtest utilise des données Polygon qui peuvent être incomplètes (free tier : 2 ans, 15min delayed).
Pour constituer un historique réel fiable et valider le moteur de backtest, il faut collecter les prix
bid/ask/mid réels des combos actifs pendant les heures de marché.

Le container Docker sur la machine Avignon (192.168.0.222) tourne 24/7 et est le point de collecte
naturel. Le client Streamlit sur Windows fait les scans et décide quels combos tracker.

---

## Architecture implémentée

```
Windows (Streamlit)              Avignon (Docker)
─────────────────────            ────────────────────────
scanner → "Tracker ce combo"     tracker/main.py
          ↓                        ├── APScheduler
  data/tracked_combos.json          │   ├── git pull /repo  (5 min)
          ↓                         │   └── collect_once()  (30 min)
  git commit + push ──── GitHub ──→ git pull                
                                    └── FastAPI :8502
                                        ├── GET /health
                                        ├── GET /combos
                                        ├── GET /prices/{id}
                                        └── GET /pnl/{id}
```

### Fichiers créés

| Fichier | Rôle |
|---|---|
| `tracker/collector.py` | Collecte Polygon snapshot (bid/ask/mid/spot/iv), SQLite |
| `tracker/api.py` | FastAPI REST : /health /combos /prices/{id} /pnl/{id} |
| `tracker/main.py` | APScheduler + git pull 5min + uvicorn |
| `tracker/Dockerfile` | Image Python + dépendances |
| `tracker/docker-compose.yml` | Mount /data et /repo |
| `tracker/requirements.txt` | fastapi, uvicorn, apscheduler, requests, gitpython |
| `data/tracked_combos.json` | Config versionnée des combos à tracker (commitée en JSON) |
| `ui/page_tracker.py` | Page Streamlit : liste combos, graphe P&L réel vs replay |
| `ui/components/combo_detail.py` | Bouton "Tracker ce combo" (ajoute au JSON + git push) |

### Modèle de données `tracked_combos.json`

```json
{
  "combos": [{
    "id": "md5[:12]",
    "symbol": "SPY",
    "as_of": "2026-04-28",
    "tracked_since": "2026-04-28T18:00:00",
    "net_debit": 1.23,
    "legs": [{
      "contract_symbol": "SPY260515C00500000",
      "direction": 1,
      "quantity": 1,
      "option_type": "call",
      "strike": 500.0,
      "expiration": "2026-05-15",
      "entry_price": 2.45,
      "implied_vol": 0.18
    }]
  }]
}
```

### Modèle de données SQLite `tracker.db`

Table `prices` :
```
id, timestamp (ET), combo_id, leg_symbol, bid, ask, mid, underlying_spot, iv
```

### Calcul P&L (endpoint /pnl/{id})

```
P&L_mid    = Σ direction × qty × (current_mid − entry_price) × 100
P&L_exec   = Σ direction × qty × (exec_price − entry_price) × 100
  exec_price = bid  si direction=+1 (long, on vend pour clôturer)
             = ask  si direction=-1 (short, on rachète pour clôturer)
P&L_%      = P&L_dollar / |net_debit| × 100
```

---

## Architecture réelle (après BUG-020)

```
Windows (Streamlit)              Avignon (Docker — bind mount ~/tracker-data)
─────────────────────            ──────────────────────────────────────────
"Tracker ce combo"               tracker/main.py
  POST /combos ─────────────────→ FastAPI sauvegarde dans DATA_DIR/tracked_combos.json
  DELETE /combos/{id} ──────────→ supprime du JSON
  GET /combos ──────────────────→ liste pour la page tracker

                                 APScheduler (toutes les 30 min)
                                   └── collect_once() lit tracked_combos.json
                                       → Polygon snapshot → tracker.db
```

**Données persistées sur disque hôte Avignon** (`~/tracker-data/`) :
- `tracked_combos.json` — liste des combos
- `tracker.db` — mesures SQLite

Le bind mount garantit que les données survivent à `docker-compose down`,
à un rebuild de l'image, ou à une réinstallation de Docker.

---

## Déploiement Avignon

```bash
# Sur Avignon (~/docker/gqqfm-tracker/)
docker-compose up -d

# Variables d'environnement requises
POLYGON_API_KEY=<clé>
REPO_DIR=/repo           # mount du clone git local
DATA_DIR=/data           # volume persistant pour tracker.db
PORT=8502
```

URL locale Streamlit → `http://192.168.0.222:8502`

---

## Impact sur l'existant

- `ui/app.py` : routage ajouté pour la page "Tracker prix réel"
- `ui/components/combo_detail.py` : bouton "Tracker ce combo" injecté en bas de chaque combo
- `data/tracked_combos.json` : nouveau fichier versionné (commité vide `{"combos": []}`)
- `.gitignore` : `tracker/data/tracker.db` exclu
