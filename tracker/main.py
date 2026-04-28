"""Point d'entrée : lance le scheduler (collecte + git pull) + l'API FastAPI."""

import logging
import os

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from collector import collect_once, init_db, pull_repo
from api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

if __name__ == "__main__":
    init_db()

    # Collecte immédiate au démarrage (si marché ouvert)
    pull_repo()
    collect_once()

    scheduler = BackgroundScheduler(timezone="America/New_York")

    # Git pull toutes les 5 minutes pour récupérer les mises à jour de tracked_combos.json
    scheduler.add_job(pull_repo, "interval", minutes=5)

    # Collecte toutes les 30 minutes pendant les heures de marché
    # (is_market_open() est vérifié dans collect_once)
    scheduler.add_job(collect_once, "interval", minutes=30)

    scheduler.start()

    port = int(os.environ.get("PORT", 8502))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
