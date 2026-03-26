"""
UnderlyingScreener — pipeline en entonnoir pour identifier les meilleurs sous-jacents.

Pipeline :
  Étape 1 — Univers statique (~128 tickers)
  Étape 2 — Filtre stock rapide (prix, volume)
  Étape 3 — Chargement calendrier événements
  Étape 4 — Filtre événements micro (earnings)
  Étape 5 — Analyse options détaillée (rate-limited)
  → Scoring + classement → top N
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable

import config
from events.calendar import EventCalendar
from screener.event_filter import filter_by_events
from screener.models import OptionsMetrics, ScreenerResult
from screener.options_analyzer import analyze_ticker, batch_compute_hv30
from screener.scorer import check_disqualification, compute_score, to_screener_result
from screener.stock_filter import fast_filter_stocks
from screener.universe import UNIVERSE

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]


def _is_us_market_open() -> bool:
    """Vérifie si NYSE est actuellement ouvert (9h30-16h00 ET, lun-ven)."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_time <= now_et <= close_time


class UnderlyingScreener:
    """
    Screener automatique de sous-jacents pour les stratégies calendar.

    Usage :
        screener = UnderlyingScreener(finnhub_api_key="xxx")
        results = screener.screen(top_n=5)
    """

    def __init__(self, finnhub_api_key: str | None = None) -> None:
        self._finnhub_api_key = finnhub_api_key
        self._event_calendar: EventCalendar | None = None

    def screen(
        self,
        top_n: int = config.SCREENER_DEFAULT_TOP_N,
        near_expiry_range: tuple[int, int] = config.SCREENER_NEAR_EXPIRY_RANGE,
        far_expiry_range: tuple[int, int] = config.SCREENER_FAR_EXPIRY_RANGE,
        progress_callback: ProgressCallback | None = None,
    ) -> list[ScreenerResult]:
        """
        Lance le pipeline de screening complet.

        Args:
            top_n              : nombre de résultats à retourner
            near_expiry_range  : (min_days, max_days) pour l'expiration NEAR
            far_expiry_range   : (min_days, max_days) pour l'expiration FAR
            progress_callback  : fonction(pct_done: float, message: str) pour l'UI

        Retourne : liste de ScreenerResult triée par score décroissant
        """
        def _progress(pct: float, msg: str) -> None:
            logger.info("[Screener %.0f%%] %s", pct, msg)
            if progress_callback:
                progress_callback(pct, msg)

        today = date.today()

        # ── Étape 1 : univers statique ─────────────────────────────────────
        _progress(0.0, f"Univers : {len(UNIVERSE)} tickers")
        candidates = list(UNIVERSE)

        # ── Étape 2 : filtre stock rapide ──────────────────────────────────
        _progress(5.0, "Filtre stock (prix, volume)…")
        candidates, prices = fast_filter_stocks(candidates)
        _progress(15.0, f"{len(candidates)} tickers après filtre stock")

        # ── Étape 3 : chargement calendrier événements ─────────────────────
        _progress(18.0, "Chargement calendrier événements…")
        cal = EventCalendar(finnhub_api_key=self._finnhub_api_key)
        cal.load(
            from_date=today,
            to_date=today + timedelta(days=far_expiry_range[1] + 7),
        )
        self._event_calendar = cal
        _progress(22.0, "Calendrier chargé")

        # ── Étape 4 : filtre événements micro ──────────────────────────────
        _progress(25.0, "Filtre earnings / ex-div…")
        candidates, earnings_dates, ex_div_dates = filter_by_events(
            candidates, near_max_days=near_expiry_range[1]
        )
        _progress(35.0, f"{len(candidates)} tickers après filtre événements")

        # ── Étape 5 : analyse options détaillée ────────────────────────────
        all_metrics: list[OptionsMetrics] = []
        n = len(candidates)

        # Batch HV30 : 1 requête yfinance pour tous les tickers
        _progress(35.0, f"HV30 batch ({n} tickers)…")
        hv30_map = batch_compute_hv30(candidates)
        _progress(40.0, "HV30 calculé")

        # Analyse parallèle (ThreadPoolExecutor — délai par thread, pas global)
        def _analyze_one(sym: str) -> OptionsMetrics | None:
            spot = prices.get(sym, 0.0)
            if spot <= 0:
                return None
            return analyze_ticker(
                symbol=sym,
                spot_price=spot,
                event_calendar=cal,
                near_range=near_expiry_range,
                far_range=far_expiry_range,
                next_earnings_date=earnings_dates.get(sym),
                next_ex_div_date=ex_div_dates.get(sym),
                hv30_precomputed=hv30_map.get(sym),
            )

        completed = 0
        with ThreadPoolExecutor(max_workers=config.SCREENER_MAX_WORKERS) as executor:
            future_to_sym = {executor.submit(_analyze_one, sym): sym for sym in candidates}
            for future in as_completed(future_to_sym):
                completed += 1
                sym = future_to_sym[future]
                pct = 40.0 + (completed / max(n, 1)) * 50.0
                _progress(pct, f"Analysé {sym} ({completed}/{n})…")

                metrics = future.result()
                if metrics is None:
                    continue

                # Filtre éliminatoire (thread principal — pas d'écriture concurrente)
                reason = check_disqualification(metrics)
                if reason:
                    metrics.disqualification_reason = reason
                    logger.debug("Éliminé %s : %s", sym, reason)
                    continue

                all_metrics.append(metrics)

        _progress(90.0, f"{len(all_metrics)} tickers qualifiés, calcul du score…")

        # ── Scoring + classement ───────────────────────────────────────────
        results: list[ScreenerResult] = []
        for metrics in all_metrics:
            score = compute_score(metrics)
            results.append(to_screener_result(metrics, score))

        results.sort(key=lambda r: r.score, reverse=True)
        _progress(100.0, f"Terminé — {len(results)} résultats, top {top_n} sélectionnés")

        return results[:top_n]
