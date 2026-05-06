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
from screener.behavior import UnderlyingBehavior, batch_compute_behavior
from screener.event_filter import filter_by_events
from screener.iv_rank import batch_compute_iv_rank_52w
from screener.iv_rank_polygon import batch_compute_iv_rank_polygon
from screener.models import OptionsMetrics, ScreenerResult
from screener.options_analyzer import analyze_ticker, batch_compute_hv30
from screener.scorer import (
    check_disqualification,
    compute_score,
    compute_score_calendar,
    compute_score_ric,
    to_screener_result,
)
from screener.stock_filter import fast_filter_stocks
from screener.universe import get_universe

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
        profile: str = "calendar",
        include_high_vol: bool = False,
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
        universe = get_universe(include_high_vol=include_high_vol)
        _progress(0.0, f"Univers : {len(universe)} tickers (profil={profile})")
        candidates = list(universe)

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
        all_metrics_disq: list[OptionsMetrics] = []   # disqualifiés — secours si < top_n
        n = len(candidates)

        # Batch HV30 : 1 requête yfinance pour tous les tickers
        _progress(35.0, f"HV30 batch ({n} tickers)…")
        hv30_map = batch_compute_hv30(candidates)
        _progress(38.0, "HV30 calculé")

        # Batch behavior (autocorr, ATR, gaps, HV ratio) — 1 requête yfinance
        _progress(38.0, f"Behavior batch ({n} tickers)…")
        behavior_map = batch_compute_behavior(candidates)
        _progress(40.0, "Behavior calculé")

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
                    all_metrics_disq.append(metrics)   # conservé pour fallback
                    continue

                all_metrics.append(metrics)

        _progress(88.0, f"{len(all_metrics)} qualifiés / {len(all_metrics_disq)} disqualifiés")

        # ── IV Rank 52w — vrai via Polygon (FEAT-024) ou fallback HV (FEAT-023) ─
        _progress(89.0, "IV Rank 52w batch…")
        symbols_for_ivr = [m.symbol for m in all_metrics + all_metrics_disq]
        iv_map = {m.symbol: m.iv_atm_near for m in all_metrics + all_metrics_disq}
        hv_map = {m.symbol: m.hv30 for m in all_metrics + all_metrics_disq}
        iv_rank_52w_map = self._compute_iv_rank(
            symbols_for_ivr, iv_map, hv_map,
            progress_callback=lambda p, m: _progress(89.0 + p * 3, m),
        )
        for m in all_metrics + all_metrics_disq:
            m.iv_rank_52w = iv_rank_52w_map.get(m.symbol, 50.0)
        _progress(92.0, "IV Rank 52w calculé")

        # ── Scoring + classement (selon profil) ────────────────────────────
        scorer = self._scorer_for_profile(profile)

        results: list[ScreenerResult] = []
        for metrics in all_metrics:
            behavior = behavior_map.get(metrics.symbol)
            score = scorer(metrics, behavior)
            results.append(to_screener_result(metrics, score, behavior, profile))

        results.sort(key=lambda r: r.score, reverse=True)

        # ── Fallback : compléter jusqu'à top_n avec les meilleurs disqualifiés ──
        if len(results) < top_n and all_metrics_disq:
            disq_scored = []
            for metrics in all_metrics_disq:
                behavior = behavior_map.get(metrics.symbol)
                score = scorer(metrics, behavior)
                disq_scored.append(to_screener_result(metrics, score, behavior, profile))
            disq_scored.sort(key=lambda r: r.score, reverse=True)
            needed = top_n - len(results)
            results.extend(disq_scored[:needed])
            logger.info(
                "Fallback : %d tickers disqualifiés ajoutés pour atteindre top_%d",
                min(needed, len(disq_scored)), top_n,
            )

        _progress(100.0, f"Terminé — {len(results)} résultats retournés (top {top_n}, profil {profile})")
        return results[:top_n]

    def _compute_iv_rank(
        self,
        symbols: list[str],
        iv_map: dict[str, float],
        hv_map: dict[str, float],
        progress_callback=None,
    ) -> dict[str, float]:
        """
        IV Rank 52w. Préfère Polygon (FEAT-024 — vrai rank historique),
        retombe sur l'approximation HV-based (FEAT-023) en cas d'indisponibilité.
        """
        try:
            from data.provider_polygon import PolygonHistoricalProvider, resolve_polygon_key
            if resolve_polygon_key():
                polygon = PolygonHistoricalProvider()
                logger.info("IV Rank : utilise Polygon (vrai rank historique)")
                return batch_compute_iv_rank_polygon(
                    symbols, iv_map, polygon,
                    progress_callback=progress_callback,
                )
        except Exception as exc:
            logger.warning("IV Rank Polygon indisponible (%s) — fallback HV-based", exc)
        return batch_compute_iv_rank_52w(symbols, iv_map, hv_map)

    @staticmethod
    def _scorer_for_profile(profile: str):
        """Retourne la fonction de score selon le profil cible."""
        if profile == "ric":
            return compute_score_ric
        if profile == "calendar":
            return compute_score_calendar
        # Auto / inconnu → calendar par défaut
        return compute_score_calendar
