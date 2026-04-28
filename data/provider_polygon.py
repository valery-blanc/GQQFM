"""DataProvider historique via Massive (ex-Polygon.io) — plan payant : appels illimités."""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import requests

import config
from data import cache_polygon
from data.models import OptionContract, OptionsChain
from data.provider_yfinance import _bs_price, _implied_vol

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float, str], None]   # progress ∈ [0,1], message

POLYGON_BASE_URL = "https://api.polygon.io"

# Plan payant Massive (ex-Polygon) : appels illimités — throttle supprimé.
# On conserve un délai minimal de sécurité pour absorber la latence réseau.
_RATE_LIMIT_SECONDS = 0.0
_RATE_LIMIT_RETRY_SECONDS = 5.0

_ET = ZoneInfo("America/New_York")

# Heures de marché disponibles pour le time picker (HH:MM ET)
SCAN_TIME_OPTIONS: dict[str, str] = {
    "09:30 (ouverture)": "09:30",
    "10:00": "10:00",
    "10:30": "10:30",
    "11:00": "11:00",
    "12:00 (midi ET)": "12:00",
    "13:00": "13:00",
    "14:00": "14:00",
    "15:00": "15:00",
    "15:30": "15:30",
    "16:00 (clôture)": "16:00",
}


def resolve_polygon_key(override: str | None = None) -> str | None:
    """
    Résout la clé API depuis (par ordre de priorité) :
    1. Paramètre direct
    2. Variable d'environnement POLYGON_API_KEY
    3. Fichier polygon.key à la racine du projet
    Retourne None si aucune clé valide trouvée.
    """
    if override:
        return override
    env_key = os.environ.get("POLYGON_API_KEY")
    if env_key:
        return env_key
    key_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "polygon.key")
    if os.path.isfile(key_file):
        val = open(key_file).read().strip()
        if val and val != "XXXX":
            return val
    return None


class PolygonHistoricalProvider:
    """
    Provider historique pour le backtesting (plan Massive payant).

    Deux modes de pricing selon le paramètre `scan_time` :
      - scan_time=None   → close EOD du contrat (agrégat journalier)
      - scan_time="HH:MM"→ close de la minute la plus proche à l'heure ET indiquée

    Dans les deux cas :
      - mid = prix du contrat (bid/ask non disponibles, spread = 0)
      - implied_vol recalculée par bisection BS depuis (price, spot, K, T, r)
      - div_yield = 0.0 (pas d'historique de dividend yield)
      - ^IRX historique utilisé pour le taux sans risque au jour de la simulation
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = resolve_polygon_key(api_key)
        if not self._api_key:
            raise RuntimeError(
                "Clé Polygon/Massive introuvable. Mets-la dans polygon.key ou env POLYGON_API_KEY."
            )
        self._last_call_ts: float = 0.0

    # ── HTTP ────────────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call_ts
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        self._last_call_ts = time.time()

    def _get(self, path: str, params: dict | None = None,
             use_cache: bool = True) -> dict:
        params = dict(params or {})
        cache_key = cache_polygon.make_key(path, params)

        if use_cache:
            cached = cache_polygon.get(cache_key)
            if cached is not None:
                return cached

        self._throttle()
        params["apiKey"] = self._api_key
        url = f"{POLYGON_BASE_URL}{path}"
        resp = requests.get(url, params=params, timeout=60)
        if resp.status_code == 429:
            logger.warning("Polygon 429 — wait %.0fs and retry", _RATE_LIMIT_RETRY_SECONDS)
            time.sleep(_RATE_LIMIT_RETRY_SECONDS)
            self._last_call_ts = time.time()
            resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if use_cache:
            cache_polygon.set(cache_key, data)
        return data

    def _paginated(self, path: str, params: dict) -> list[dict]:
        all_results: list[dict] = []
        params = dict(params)
        params.setdefault("limit", 1000)

        data = self._get(path, params)
        all_results.extend(data.get("results", []))

        while data.get("next_url"):
            self._throttle()
            url = data["next_url"]
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}apiKey={self._api_key}"
            cache_key = cache_polygon.make_key(url, {})
            cached = cache_polygon.get(cache_key)
            if cached is not None:
                data = cached
            else:
                resp = requests.get(full_url, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                cache_polygon.set(cache_key, data)
            all_results.extend(data.get("results", []))
        return all_results

    # ── Minute bar helper ────────────────────────────────────────────────────

    def _minute_bar_at(self, ticker: str, as_of: date, scan_time: str) -> tuple[float, int] | None:
        """
        Retourne (close, volume) de la minute la plus proche de scan_time (HH:MM ET).
        Tolerance : ±15 minutes. Retourne None si aucune donnée pour la journée.
        """
        h, m = map(int, scan_time.split(":"))
        target_dt = datetime(as_of.year, as_of.month, as_of.day, h, m, tzinfo=_ET)
        target_ms = int(target_dt.timestamp() * 1000)

        # Fetch toutes les minutes de la journée (1 call, mis en cache)
        data = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/minute/{as_of.isoformat()}/{as_of.isoformat()}",
            params={"limit": 500},
        )
        bars = data.get("results", [])
        if not bars:
            return None

        closest = min(bars, key=lambda b: abs(b["t"] - target_ms))
        if abs(closest["t"] - target_ms) > 15 * 60 * 1000:
            return None

        return float(closest["c"]), int(closest.get("v", 0))

    # ── Public API ──────────────────────────────────────────────────────────

    def get_underlying_close(self, symbol: str, as_of: date,
                             scan_time: str | None = None) -> float:
        """Prix du sous-jacent à as_of (close EOD ou minute intraday)."""
        if scan_time is not None:
            result = self._minute_bar_at(symbol.upper(), as_of, scan_time)
            if result is not None:
                return result[0]
            # Fallback sur EOD si aucune barre intraday trouvée
            logger.warning("Pas de bar intraday pour %s @ %s %s, fallback EOD", symbol, as_of, scan_time)

        for delta_days in range(0, 6):
            d = as_of - timedelta(days=delta_days)
            data = self._get(
                f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{d.isoformat()}/{d.isoformat()}",
            )
            if data.get("resultsCount", 0) > 0:
                return float(data["results"][0]["c"])
        raise RuntimeError(f"Aucun close trouvé pour {symbol} autour de {as_of}")

    def get_contract_close(self, contract_ticker: str, as_of: date,
                           scan_time: str | None = None) -> tuple[float, int] | None:
        """
        Retourne (price, volume) pour un contrat à as_of.
        scan_time=None → close EOD ; scan_time="HH:MM" → minute la plus proche.
        """
        if scan_time is not None:
            return self._minute_bar_at(contract_ticker, as_of, scan_time)

        data = self._get(
            f"/v2/aggs/ticker/{contract_ticker}/range/1/day/{as_of.isoformat()}/{as_of.isoformat()}",
        )
        if data.get("resultsCount", 0) == 0:
            return None
        bar = data["results"][0]
        return float(bar.get("c", 0.0)), int(bar.get("v", 0))

    def list_contracts(
        self,
        symbol: str,
        as_of: date,
        expiry_min: date,
        expiry_max: date,
    ) -> list[dict]:
        """Liste tous les contrats actifs sur `symbol` à la date `as_of`."""
        return self._paginated(
            "/v3/reference/options/contracts",
            {
                "underlying_ticker": symbol.upper(),
                "as_of": as_of.isoformat(),
                "expiration_date.gte": expiry_min.isoformat(),
                "expiration_date.lte": expiry_max.isoformat(),
                "expired": "false",
            },
        )

    def get_options_chain(
        self,
        symbol: str,
        as_of: date,
        min_expiry: date | None = None,
        max_expiry: date | None = None,
        min_strike: float | None = None,
        max_strike: float | None = None,
        min_volume: int = 0,
        min_open_interest: int = 0,
        progress_callback: ProgressCb | None = None,
        scan_time: str | None = None,
    ) -> OptionsChain:
        """
        Récupère la chaîne d'options telle qu'elle existait à `as_of`.

        scan_time (HH:MM ET) : si fourni, utilise les minutes aggregates pour
        pricer les contrats à l'heure choisie au lieu du close EOD.
        """
        cb = progress_callback or (lambda p, m: None)
        time_label = f" @ {scan_time} ET" if scan_time else " (EOD)"

        cb(0.0, f"Fetching {symbol} spot{time_label} …")
        underlying_price = self.get_underlying_close(symbol, as_of, scan_time)

        if min_expiry is None:
            min_expiry = as_of + timedelta(days=config.MIN_DAYS_TO_EXPIRY)
        if max_expiry is None:
            max_expiry = as_of + timedelta(days=config.MAX_DAYS_TO_EXPIRY)
        if min_strike is None:
            min_strike = underlying_price * (1 - config.MAX_STRIKE_PCT_FROM_SPOT)
        if max_strike is None:
            max_strike = underlying_price * (1 + config.MAX_STRIKE_PCT_FROM_SPOT)

        cb(0.02, f"Listing contracts (strike ±{int(config.MAX_STRIKE_PCT_FROM_SPOT*100)}%, expiry +{config.MIN_DAYS_TO_EXPIRY}-{config.MAX_DAYS_TO_EXPIRY}j)…")
        all_contracts = self.list_contracts(symbol, as_of, min_expiry, max_expiry)

        kept_meta = [
            c for c in all_contracts
            if min_strike <= float(c.get("strike_price", 0)) <= max_strike
        ]
        n_total = len(kept_meta)
        cb(0.05, f"{n_total} contrats à fetcher (plan payant — appels illimités)")

        rate = self.get_risk_free_rate(as_of)
        contracts: list[OptionContract] = []
        skipped_no_bar = 0
        skipped_zero_vol = 0
        skipped_iv = 0
        t_loop_start = time.time()

        for idx, meta in enumerate(kept_meta):
            ticker = meta["ticker"]
            strike = float(meta["strike_price"])
            exp = date.fromisoformat(meta["expiration_date"])
            opt_type = meta["contract_type"]

            frac = 0.05 + 0.90 * (idx / max(n_total, 1))
            if idx > 0:
                elapsed = time.time() - t_loop_start
                avg_s = elapsed / idx
                remaining_s = (n_total - idx) * avg_s
                eta_str = f"{remaining_s:.0f} s" if remaining_s < 120 else f"{remaining_s/60:.1f} min"
            else:
                eta_str = "…"
            cb(frac, f"Fetching {idx+1}/{n_total} {ticker} (ETA {eta_str})")

            bar = self.get_contract_close(ticker, as_of, scan_time)
            if bar is None:
                skipped_no_bar += 1
                continue
            mid, volume = bar
            # En EOD, on filtre les contrats sans activité ce jour (volume = 0 = close stale).
            # En intraday, la minute peut avoir volume = 0 sur un contrat liquide — on accepte.
            if scan_time is None and volume == 0:
                skipped_zero_vol += 1
                continue
            if mid <= 0:
                continue

            tte = max(0.0, (exp - as_of).days / 365.0)
            iv = _implied_vol(opt_type, mid, underlying_price, strike, tte, rate)
            if iv < 0.01 or iv > 5.0:
                skipped_iv += 1
                continue

            if volume < min_volume:
                continue

            contracts.append(OptionContract(
                contract_symbol=ticker,
                option_type=opt_type,
                strike=strike,
                expiration=exp,
                bid=mid,
                ask=mid,
                mid=mid,
                implied_vol=iv,
                volume=max(volume, 1),
                open_interest=0,
                delta=None,
                div_yield=0.0,
            ))

        cb(0.97, f"Chain ready : {len(contracts)} contrats (skipped no_bar={skipped_no_bar} zero_vol={skipped_zero_vol} iv={skipped_iv})")
        logger.info(
            "Polygon chain %s @ %s%s : %d contrats (skipped no_bar=%d zero_vol=%d iv=%d)",
            symbol, as_of, time_label, len(contracts), skipped_no_bar, skipped_zero_vol, skipped_iv,
        )

        expirations = sorted(set(c.expiration for c in contracts))
        strikes = sorted(set(c.strike for c in contracts))

        return OptionsChain(
            underlying_symbol=symbol.upper(),
            underlying_price=underlying_price,
            div_yield=0.0,
            contracts=contracts,
            expirations=expirations,
            strikes=strikes,
            fetch_timestamp=datetime.now(tz=timezone.utc),
        )

    def get_risk_free_rate(self, as_of: date | None = None) -> float:
        """^IRX historique pour la date de simulation (via yfinance)."""
        from data.risk_free_rate import fetch_historical_risk_free_rate
        if as_of is not None:
            rate, _ = fetch_historical_risk_free_rate(as_of)
            return rate
        return config.DEFAULT_RISK_FREE_RATE
