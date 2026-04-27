"""DataProvider historique via Polygon.io (free tier : EOD only, 5 calls/min)."""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import requests

import config
from data import cache_polygon
from data.models import OptionContract, OptionsChain
from data.provider_yfinance import _bs_price, _implied_vol

logger = logging.getLogger(__name__)

ProgressCb = Callable[[float, str], None]   # progress ∈ [0,1], message

POLYGON_BASE_URL = "https://api.polygon.io"

# Free tier rate limit : 5 calls/min → un appel toutes les 12 s en théorie.
# On prend une marge à 13 s pour ne pas frôler la limite.
_RATE_LIMIT_SECONDS = 13.0
_RATE_LIMIT_RETRY_SECONDS = 30.0


def resolve_polygon_key(override: str | None = None) -> str | None:
    """
    Résout la clé API Polygon depuis (par ordre de priorité) :
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
    Provider historique pour le backtesting. Renvoie une chaîne d'options
    telle qu'elle existait à `as_of`, avec :
      - underlying_price : close du sous-jacent à `as_of`
      - mid des options : close EOD à `as_of`
      - implied_vol : recalculée par bisection BS depuis (close, spot, K, T, r)
      - div_yield : 0.0 (limite free tier — pas d'historique de dividend yield)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = resolve_polygon_key(api_key)
        if not self._api_key:
            raise RuntimeError(
                "Clé Polygon introuvable. Mets-la dans polygon.key ou env POLYGON_API_KEY."
            )
        self._last_call_ts: float = 0.0

    # ── HTTP ────────────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Respecte le rate limit free tier (5 calls/min)."""
        elapsed = time.time() - self._last_call_ts
        if elapsed < _RATE_LIMIT_SECONDS:
            time.sleep(_RATE_LIMIT_SECONDS - elapsed)
        self._last_call_ts = time.time()

    def _get(self, path: str, params: dict | None = None,
             use_cache: bool = True) -> dict:
        """GET sur Polygon avec cache SQLite et throttle. Retourne le JSON."""
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
        """Suit `next_url` pour les endpoints paginés (ex: contracts). Retourne tous les results."""
        all_results: list[dict] = []
        params = dict(params)
        params.setdefault("limit", 1000)

        data = self._get(path, params)
        all_results.extend(data.get("results", []))

        while data.get("next_url"):
            # next_url contient déjà tous les params, on doit juste y ajouter apiKey
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

    # ── Public API ──────────────────────────────────────────────────────────

    def get_underlying_close(self, symbol: str, as_of: date) -> float:
        """Close du sous-jacent à la date as_of (ou le dernier jour ouvré précédent)."""
        # Polygon renvoie [] si jour férié — on remonte de 5 j max
        for delta_days in range(0, 6):
            d = as_of - timedelta(days=delta_days)
            data = self._get(
                f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{d.isoformat()}/{d.isoformat()}",
            )
            if data.get("resultsCount", 0) > 0:
                return float(data["results"][0]["c"])
        raise RuntimeError(f"Aucun close trouvé pour {symbol} autour de {as_of}")

    def get_contract_close(self, contract_ticker: str, as_of: date) -> tuple[float, int] | None:
        """
        Retourne (close, volume) pour un contrat donné à la date as_of.
        None si pas de bar (contrat illiquide ce jour-là).
        """
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
    ) -> OptionsChain:
        """
        Récupère la chaîne d'options telle qu'elle existait à `as_of`.

        IMPORTANT — limites du free tier :
          - mid = close EOD du contrat (pas de bid/ask)
          - bid/ask sont mis à mid (spread = 0) pour respecter le modèle
          - implied_vol recalculée par bisection BS
          - div_yield = 0 (pas d'historique gratuit)
          - les contrats sans bar ce jour-là sont exclus
          - les contrats avec volume = 0 ce jour-là sont aussi exclus (close stale)
        """
        cb = progress_callback or (lambda p, m: None)

        cb(0.0, f"Fetching {symbol} close @ {as_of}…")
        underlying_price = self.get_underlying_close(symbol, as_of)

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

        # Filtre strike avant tout fetch d'aggregates pour économiser les calls
        kept_meta = [
            c for c in all_contracts
            if min_strike <= float(c.get("strike_price", 0)) <= max_strike
        ]
        n_total = len(kept_meta)
        cb(0.05, f"{n_total} contrats à fetcher (≈{n_total * _RATE_LIMIT_SECONDS / 60:.0f} min cold)")

        rate = config.DEFAULT_RISK_FREE_RATE
        contracts: list[OptionContract] = []
        skipped_no_bar = 0
        skipped_zero_vol = 0
        skipped_iv = 0

        for idx, meta in enumerate(kept_meta):
            ticker = meta["ticker"]
            strike = float(meta["strike_price"])
            exp = date.fromisoformat(meta["expiration_date"])
            opt_type = meta["contract_type"]   # "call" ou "put"

            # Progression : 5 % → 95 % sur la boucle aggregates
            frac = 0.05 + 0.90 * (idx / max(n_total, 1))
            remaining = (n_total - idx - 1) * _RATE_LIMIT_SECONDS
            eta_min = remaining / 60
            cb(frac, f"Fetching {idx+1}/{n_total} {ticker} (ETA {eta_min:.1f} min)")

            bar = self.get_contract_close(ticker, as_of)
            if bar is None:
                skipped_no_bar += 1
                continue
            mid, volume = bar
            if volume == 0:
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
                bid=mid,            # free tier : pas de bid/ask, on utilise mid
                ask=mid,
                mid=mid,
                implied_vol=iv,
                volume=volume,
                open_interest=0,    # pas dispo dans aggregates
                delta=None,
                div_yield=0.0,
            ))

        cb(0.97, f"Chain ready : {len(contracts)} contrats (skipped no_bar={skipped_no_bar} zero_vol={skipped_zero_vol} iv={skipped_iv})")
        logger.info(
            "Polygon chain %s @ %s : %d contrats (skipped no_bar=%d zero_vol=%d iv=%d)",
            symbol, as_of, len(contracts), skipped_no_bar, skipped_zero_vol, skipped_iv,
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
        """
        Pour V1 backtest : utilise la constante. Pour faire propre on devrait
        fetcher ^IRX historique mais ça consomme un call de plus à chaque scan.
        À améliorer si nécessaire.
        """
        return config.DEFAULT_RISK_FREE_RATE
