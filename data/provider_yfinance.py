"""Implémentation DataProvider via Yahoo Finance (yfinance)."""

from datetime import date, datetime, timedelta, timezone

import math

import pandas as pd
import yfinance as yf

import config


def _implied_vol(option_type: str, price: float, spot: float, strike: float,
                 tte: float, rate: float) -> float:
    """Calcule l'IV implicite par bisection (fallback quand yfinance retourne IV≈0)."""
    if tte <= 0 or price <= 0:
        return 0.0
    lo, hi = 1e-4, 5.0
    for _ in range(50):
        mid = (lo + hi) / 2
        sq = mid * math.sqrt(tte)
        d1 = (math.log(spot / strike) + (rate + 0.5 * mid ** 2) * tte) / sq
        d2 = d1 - sq
        from scipy.stats import norm
        if option_type == "call":
            val = spot * norm.cdf(d1) - strike * math.exp(-rate * tte) * norm.cdf(d2)
        else:
            val = strike * math.exp(-rate * tte) * norm.cdf(-d2) - spot * norm.cdf(-d1)
        if val < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-5:
            break
    return (lo + hi) / 2


def _bs_price(option_type: str, spot: float, strike: float,
              tte: float, vol: float, rate: float) -> float:
    """Calcule le prix Black-Scholes (re-pricing hors séance)."""
    if tte <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if vol <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    from scipy.stats import norm
    sq = vol * math.sqrt(tte)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / sq
    d2 = d1 - sq
    if option_type == "call":
        return spot * norm.cdf(d1) - strike * math.exp(-rate * tte) * norm.cdf(d2)
    return strike * math.exp(-rate * tte) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def _consensus_iv(rows: list, spot: float, tte: float, rate: float) -> float | None:
    """
    Calcule l'IV consensus pour une expiration hors-séance.

    Utilise uniquement les options OTM dont le lastPrice donne une IV plausible
    (0.05-1.5). Les options OTM sont moins sensibles aux mouvements de spot
    donc leur lastPrice (stale) donne une meilleure estimation de l'IV courante.
    Retourne la médiane, ou None si insuffisant.
    """
    otm_ivs = []
    for (option_type, strike, last_price, *_) in rows:
        if last_price <= 0:
            continue
        is_otm = (option_type == "call" and strike > spot) or \
                 (option_type == "put" and strike < spot)
        if not is_otm:
            continue
        iv = _implied_vol(option_type, last_price, spot, strike, tte, rate)
        if 0.05 <= iv <= 1.5:
            otm_ivs.append(iv)
    if len(otm_ivs) < 2:
        return None
    otm_ivs.sort()
    mid = len(otm_ivs) // 2
    if len(otm_ivs) % 2 == 0:
        return (otm_ivs[mid - 1] + otm_ivs[mid]) / 2
    return otm_ivs[mid]


def _safe_float(v, default: float = 0.0) -> float:
    """Convertit v en float, retourne default si None ou NaN."""
    try:
        result = float(v)
        return default if math.isnan(result) else result
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    """Convertit v en int, retourne default si None ou NaN."""
    try:
        result = float(v)
        return default if math.isnan(result) else int(result)
    except (TypeError, ValueError):
        return default


from data.models import OptionContract, OptionsChain


class YFinanceProvider:
    """Fournisseur de données d'options via l'API Yahoo Finance."""

    def get_risk_free_rate(self) -> float:
        from data.risk_free_rate import fetch_risk_free_rate
        rate, _ = fetch_risk_free_rate()
        return rate

    def get_options_chain(
        self,
        symbol: str,
        min_expiry: date | None = None,
        max_expiry: date | None = None,
        min_strike: float | None = None,
        max_strike: float | None = None,
        min_volume: int = 0,
        min_open_interest: int = 0,
    ) -> OptionsChain:
        """Récupère et filtre la chaîne d'options pour un symbole."""
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        underlying_price = float(info.last_price)

        # Dividend yield continu annualisé (0.0 si non disponible)
        try:
            full_info = ticker.info
            raw_yield = full_info.get("dividendYield") or full_info.get("trailingAnnualDividendYield")
            div_yield = float(raw_yield) if raw_yield else 0.0
            # yfinance retourne parfois dividendYield en % (ex: 1.14 pour 1.14%)
            # au lieu de fraction (0.0114). Normalisation : si > 1.0 → diviser par 100.
            if div_yield > 1.0:
                div_yield /= 100.0
        except Exception:
            div_yield = 0.0

        today = date.today()
        if min_expiry is None:
            min_expiry = today + timedelta(days=config.MIN_DAYS_TO_EXPIRY)
        if max_expiry is None:
            max_expiry = today + timedelta(days=config.MAX_DAYS_TO_EXPIRY)
        if min_strike is None:
            min_strike = underlying_price * (1 - config.MAX_STRIKE_PCT_FROM_SPOT)
        if max_strike is None:
            max_strike = underlying_price * (1 + config.MAX_STRIKE_PCT_FROM_SPOT)

        # Expirations disponibles dans la plage
        all_expirations = [
            date.fromisoformat(exp)
            for exp in ticker.options
        ]
        valid_expirations = [
            exp for exp in all_expirations
            if min_expiry <= exp <= max_expiry
        ]

        contracts: list[OptionContract] = []
        rate = config.DEFAULT_RISK_FREE_RATE

        for exp in valid_expirations:
            tte = max(0.0, (exp - today).days / 365.0)
            chain = ticker.option_chain(exp.isoformat())

            # --- Première passe : collecter toutes les données brutes ---
            raw_rows = []   # (option_type, strike, bid, ask, last_price, volume, oi, iv_yf, delta)
            all_off_hours = True
            for option_type, df in [("call", chain.calls), ("put", chain.puts)]:
                for _, row in df.iterrows():
                    strike = float(row["strike"])
                    if not (min_strike <= strike <= max_strike):
                        continue
                    bid = _safe_float(row.get("bid"))
                    ask = _safe_float(row.get("ask"))
                    if bid > 0 or ask > 0:
                        all_off_hours = False
                    last_price = _safe_float(row.get("lastPrice"))
                    volume = _safe_int(row.get("volume"))
                    oi = _safe_int(row.get("openInterest"))
                    iv_yf = _safe_float(row.get("impliedVolatility"))
                    _d = row.get("delta")
                    delta = None if _d is None else (_safe_float(_d) or None)
                    contract_symbol = str(row.get("contractSymbol", ""))
                    raw_rows.append((option_type, strike, bid, ask, last_price,
                                     volume, oi, iv_yf, delta, contract_symbol))

            # --- Calcul IV consensus si hors séance ---
            # Hors séance : toutes bid=ask=0. Les lastPrices des options ITM sont souvent
            # obsolètes (trades quand le spot était différent). On utilise les options OTM
            # (moins sensibles aux mouvements de spot) pour estimer l'IV courante, puis
            # on re-price TOUTES les options avec BS au spot courant.
            cons_iv = None
            if all_off_hours and tte > 0:
                cons_iv = _consensus_iv(
                    [(r[0], r[1], r[4]) for r in raw_rows],
                    underlying_price, tte, rate,
                )

            # --- Deuxième passe : créer les contrats avec prix et IV corrects ---
            for (option_type, strike, bid, ask, last_price,
                 volume, oi, iv_yf, delta, contract_symbol) in raw_rows:

                if all_off_hours:
                    # Hors séance : utiliser BS au spot courant avec IV consensus
                    if last_price <= 0:
                        continue
                    if oi < config.MIN_OPEN_INTEREST and volume < config.MIN_OPEN_INTEREST:
                        continue
                    if volume < min_volume or oi < min_open_interest:
                        continue

                    if cons_iv is not None:
                        # Re-pricer au spot courant : corrige les lastPrice stales
                        mid = max(_bs_price(option_type, underlying_price, strike, tte, cons_iv, rate), 0.01)
                        iv = cons_iv
                    else:
                        # Fallback : lastPrice direct + bisection IV
                        mid = last_price
                        iv = _implied_vol(option_type, mid, underlying_price, strike, tte, rate)
                        if iv < 0.01:
                            continue   # IV non calculable → contrat inutilisable

                else:
                    # Séance ouverte : bid/ask live
                    if bid == 0 and ask == 0:
                        if last_price <= 0:
                            continue
                        bid = ask = last_price
                    if bid == 0:
                        continue
                    mid = (bid + ask) / 2
                    if mid > 0 and (ask - bid) / mid > config.MAX_BID_ASK_SPREAD_PCT:
                        continue
                    if oi < config.MIN_OPEN_INTEREST and volume < config.MIN_OPEN_INTEREST:
                        continue
                    if volume < min_volume or oi < min_open_interest:
                        continue
                    iv = iv_yf
                    if iv < 0.05:
                        iv = _implied_vol(option_type, mid, underlying_price, strike, tte, rate)

                contracts.append(OptionContract(
                    contract_symbol=contract_symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=exp,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    implied_vol=iv,
                    volume=volume,
                    open_interest=oi,
                    delta=delta,
                    div_yield=div_yield,
                ))

        expirations = sorted(set(c.expiration for c in contracts))
        strikes = sorted(set(c.strike for c in contracts))

        return OptionsChain(
            underlying_symbol=symbol.upper(),
            underlying_price=underlying_price,
            div_yield=div_yield,
            contracts=contracts,
            expirations=expirations,
            strikes=strikes,
            fetch_timestamp=datetime.now(tz=timezone.utc),
        )
