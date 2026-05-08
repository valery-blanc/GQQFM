"""FEAT-029 — Backtest de validation du ranking.

Mesure empiriquement la calibration et le pouvoir predictif du score composite
en comparant le pnl_pred (theorique scan) au pnl_real (replay Polygon historique)
sur ~5400 combos top-K.

Sortie : scripts/output/{validation_full.csv, validation_summary.csv,
validation_scatter_<variant>.png, validation_report.md}.

Usage : python -m scripts.validate_ranking
        (sur ANQA, ETA ~3h)
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
from backtesting import backtest_combo
from config import ScoreWeights
from data.models import ScoringCriteria
from data.provider_polygon import PolygonHistoricalProvider
from ui.page_backtest import run_backtest_scan


# ── Configuration de l'echantillon ─────────────────────────────────────────────
SYMBOLS: list[str] = ["QQQ", "SPY", "IWM"]
TOP_K: int = 10
HORIZON_DAYS_BC: int = 3  # horizon eval = close_date - 3j (memes que default scan)
VOL_MID = config.VOL_MEDIAN_INDEX  # = 1
RANDOM_SEED = 42

OUTPUT_DIR = ROOT / "scripts" / "output"


def _build_dates(n: int = 30,
                 start: date = date(2024, 9, 9),
                 end: date = date(2026, 4, 21)) -> list[date]:
    """30 dates as_of approximativement equidistantes, snappees au prochain weekday."""
    if n <= 1:
        return [start]
    span = (end - start).days
    step = span / (n - 1)
    out: list[date] = []
    seen: set[date] = set()
    for i in range(n):
        d = start + timedelta(days=int(round(i * step)))
        while d.weekday() >= 5 or d in seen:
            d += timedelta(days=1)
        out.append(d)
        seen.add(d)
    return out


DATES_TO_TEST: list[date] = _build_dates()


# ── Variantes ──────────────────────────────────────────────────────────────────
# Une variante = config qui modifie le params dict du scan.
# `vol_calibration: True` declenche le calcul percentiles HV30 par (symbol, as_of).
VARIANTS: dict[str, dict] = {
    "current":       {"days_before_close": 3, "use_american_pricer": True,
                      "vol_factors": (0.8, 1.2), "vol_calibration": False,
                      "random_pick": False},
    "days_bc_0":     {"days_before_close": 0, "use_american_pricer": True,
                      "vol_factors": (0.8, 1.2), "vol_calibration": False,
                      "random_pick": False},
    "days_bc_5":     {"days_before_close": 5, "use_american_pricer": True,
                      "vol_factors": (0.8, 1.2), "vol_calibration": False,
                      "random_pick": False},
    "bs_eur":        {"days_before_close": 3, "use_american_pricer": False,
                      "vol_factors": (0.8, 1.2), "vol_calibration": False,
                      "random_pick": False},
    "iv_calibrated": {"days_before_close": 3, "use_american_pricer": True,
                      "vol_factors": (0.8, 1.2), "vol_calibration": True,
                      "random_pick": False},
    # `random` reutilise le scan de `current` (memes params) mais top-K aleatoire.
    "random":        {"days_before_close": 3, "use_american_pricer": True,
                      "vol_factors": (0.8, 1.2), "vol_calibration": False,
                      "random_pick": True},
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _log(progress: float, msg: str) -> None:
    pct = int(progress * 100)
    print(f"  [{pct:3d}%] {msg}", flush=True)


def _hv30_percentiles(provider: PolygonHistoricalProvider,
                      symbol: str, as_of: date,
                      lookback_days: int = 365) -> tuple[float, float, float] | None:
    """Retourne (p10, current, p90) de la HV30 rolling sur 1 an avant as_of.
    Retourne None si donnees insuffisantes."""
    from backtesting.replay import _prefetch_daily_range  # type: ignore
    start = as_of - timedelta(days=lookback_days + 60)
    bars = _prefetch_daily_range(provider, symbol.upper(), start, as_of)
    if not bars:
        return None
    closes = [(d, c) for d, (c, _) in sorted(bars.items()) if c > 0]
    if len(closes) < 60:
        return None
    arr = np.array([c for _, c in closes], dtype=np.float64)
    log_ret = np.diff(np.log(arr))
    win = 21
    if len(log_ret) < win + 30:
        return None
    hv = np.array([
        log_ret[i - win:i].std() * math.sqrt(252)
        for i in range(win, len(log_ret) + 1)
    ])
    hv = hv[np.isfinite(hv) & (hv > 0)]
    if len(hv) < 30:
        return None
    return float(np.percentile(hv, 10)), float(hv[-1]), float(np.percentile(hv, 90))


def _build_params(variant_cfg: dict,
                  symbol: str,
                  as_of: date,
                  provider: PolygonHistoricalProvider | None) -> dict:
    """Construit le dict params du scan pour la variante."""
    vol_low, vol_high = variant_cfg["vol_factors"]
    if variant_cfg["vol_calibration"] and provider is not None:
        perc = _hv30_percentiles(provider, symbol, as_of)
        if perc is not None:
            p10, cur, p90 = perc
            if cur > 0:
                vol_low = max(0.4, p10 / cur)
                vol_high = min(2.5, p90 / cur)

    return {
        "selected_templates": ["calendar_strangle", "double_calendar"],
        "criteria": ScoringCriteria(
            max_loss_pct=-50.0,
            max_loss_probability_pct=25.0,
            min_max_gain_pct=10.0,
            min_gain_loss_ratio=0.1,
            max_net_debit=10_000.0,
            min_avg_volume=0,
        ),
        "vol_low": vol_low,
        "vol_high": vol_high,
        "risk_free_rate": config.DEFAULT_RISK_FREE_RATE,
        "max_combinations": 200_000,
        "days_before_close": variant_cfg["days_before_close"],
        "use_american_pricer": variant_cfg["use_american_pricer"],
        "near_expiry_range": config.SCANNER_NEAR_EXPIRY_RANGE,
        "far_expiry_range":  config.SCANNER_FAR_EXPIRY_RANGE,
        "score_weights": ScoreWeights(),
        "scan_clicked": False,
    }


def _params_signature(params: dict) -> tuple:
    """Cle de cache : meme signature => meme scan reutilisable."""
    return (
        tuple(params["selected_templates"]),
        round(params["vol_low"], 4),
        round(params["vol_high"], 4),
        params["days_before_close"],
        params["use_american_pricer"],
        params["max_combinations"],
        params["near_expiry_range"],
        params["far_expiry_range"],
    )


def _pnl_pred_at_spot(pnl_combo: np.ndarray,
                      spot_range: np.ndarray,
                      spot_real: float,
                      net_debit: float) -> float:
    """pnl_pred conditionne sur le spot reellement observe (vol scenario median).

    pnl_combo : (V, M) = pnl dollar du combo sur la grille de spots.
    """
    idx = int(np.argmin(np.abs(spot_range - spot_real)))
    pnl_dollar = float(pnl_combo[VOL_MID, idx])
    denom = net_debit if net_debit > 0 else 1e-6
    return pnl_dollar / denom * 100.0


def _validate_scan_result(variant: str,
                          scan: dict,
                          symbol: str,
                          as_of: date,
                          rng: random.Random,
                          random_pick: bool,
                          provider: PolygonHistoricalProvider) -> list[dict]:
    """Pour une scan_result donnee, lance les replays top-K et collecte les rows."""
    combos = scan.get("combinations") or []
    metrics = scan.get("metrics") or []
    if not combos:
        return []

    indices = list(range(len(combos)))
    if random_pick:
        sel = rng.sample(indices, k=min(TOP_K, len(indices)))
    else:
        sel = indices[:TOP_K]

    spot_ranges = scan["spot_ranges"]
    pnl_per_combo = scan["pnl_per_combo"]
    rfr_scan = scan.get("rfr") or config.DEFAULT_RISK_FREE_RATE

    spots_list = scan.get("spots") or []
    spot_entry = float(spots_list[0]) if spots_list else float(scan.get("spot", 0.0))

    rows: list[dict] = []
    for slot, idx in enumerate(sel, 1):
        combo = combos[idx]
        m = metrics[idx]
        spot_range = np.asarray(spot_ranges[idx])
        pnl_combo = np.asarray(pnl_per_combo[idx])

        days_forward = max(1, (combo.close_date - as_of).days + 5)
        try:
            replay = backtest_combo(combo, as_of=as_of,
                                    days_forward=days_forward,
                                    provider=provider,
                                    rate=rfr_scan)
        except Exception as exc:  # noqa: BLE001
            print(f"    [WARN] replay {variant}/{symbol}/{as_of}/r{slot}: {exc}",
                  flush=True)
            continue
        if not replay:
            continue

        target_date = combo.close_date - timedelta(days=HORIZON_DAYS_BC)
        pt = min(replay, key=lambda p: abs((p.date - target_date).days))

        pnl_pred = _pnl_pred_at_spot(pnl_combo, spot_range, pt.spot, combo.net_debit)
        rank = slot if random_pick else (idx + 1)

        rows.append({
            "variant": variant,
            "symbol": symbol,
            "as_of": as_of.isoformat(),
            "rank": rank,
            "combo_id": _combo_id(combo),
            "score": float(m["score"]),
            "pnl_pred_at_real_spot": float(pnl_pred),
            "pnl_real": float(pt.pnl_pct),
            "max_gain_real_pct_pred": float(m["max_gain_real_pct"]),
            "spot_entry": spot_entry,
            "spot_exit": float(pt.spot),
            "days_to_close": int(m["days_to_close"]),
            "mode": pt.mode,
            "net_debit": float(combo.net_debit),
        })
    return rows


def _combo_id(combo) -> str:
    parts = []
    for leg in combo.legs:
        sign = "L" if leg.direction > 0 else "S"
        parts.append(f"{sign}{leg.quantity}{leg.option_type[0].upper()}"
                     f"{leg.strike:g}@{leg.expiration.isoformat()}")
    return "|".join(parts)


# ── Checkpoint / resume ────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "variant", "symbol", "as_of", "rank", "combo_id", "score",
    "pnl_pred_at_real_spot", "pnl_real", "max_gain_real_pct_pred",
    "spot_entry", "spot_exit", "days_to_close", "mode", "net_debit",
]


def _load_done_keys(csv_path: Path) -> set[tuple[str, str, str]]:
    """Retourne l'ensemble des `(variant, symbol, as_of)` deja traites
    (lus depuis le CSV de checkpoint si present)."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return set()
    try:
        df = pd.read_csv(csv_path, usecols=["variant", "symbol", "as_of"])
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] CSV illisible ({exc}), reset checkpoint", flush=True)
        return set()
    return {(str(r.variant), str(r.symbol), str(r.as_of))
            for r in df.itertuples(index=False)}


def _append_rows(rows: list[dict], csv_path: Path) -> None:
    """Append des rows au CSV (cree avec header si absent)."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    df.to_csv(csv_path, index=False, mode="a", header=write_header)


# ── Orchestration ──────────────────────────────────────────────────────────────
def run_validation() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUTPUT_DIR / "validation_full.csv"
    rng = random.Random(RANDOM_SEED)
    provider = PolygonHistoricalProvider()

    done_keys = _load_done_keys(out_csv)
    print(f"FEAT-029 validation : "
          f"{len(VARIANTS)} variantes x {len(SYMBOLS)} symbols "
          f"x {len(DATES_TO_TEST)} dates x {TOP_K} combos\n")
    print(f"Output dir : {OUTPUT_DIR}")
    print(f"Checkpoint : {out_csv} ({len(done_keys)} (variant,symbol,as_of) deja traites)\n")
    print(f"Dates : {[d.isoformat() for d in DATES_TO_TEST]}\n")

    cache: dict[tuple, dict] = {}
    t0 = time.perf_counter()
    n_new_rows = 0

    n_steps = len(VARIANTS) * len(SYMBOLS) * len(DATES_TO_TEST)
    step = 0
    for variant, cfg in VARIANTS.items():
        for symbol in SYMBOLS:
            for as_of in DATES_TO_TEST:
                step += 1
                tag = f"[{step}/{n_steps}] {variant} / {symbol} / {as_of}"
                key3 = (variant, symbol, as_of.isoformat())
                if key3 in done_keys:
                    print(f"{tag}  [resume skip]", flush=True)
                    continue
                print(tag, flush=True)
                params = _build_params(cfg, symbol, as_of, provider)
                key = (symbol, as_of, _params_signature(params))

                scan = cache.get(key)
                if scan is None:
                    try:
                        scan = run_backtest_scan(params, symbol, as_of,
                                                 progress_callback=_log)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [ERR scan] {exc}", flush=True)
                        continue
                    if "error" in scan:
                        print(f"  [SKIP] {scan['error']}", flush=True)
                        continue
                    cache[key] = scan
                else:
                    print(f"  [cache hit] reuse scan {key[:2]}", flush=True)

                new_rows = _validate_scan_result(
                    variant, scan, symbol, as_of, rng,
                    random_pick=cfg["random_pick"], provider=provider,
                )
                _append_rows(new_rows, out_csv)
                n_new_rows += len(new_rows)
                done_keys.add(key3)

    elapsed = time.perf_counter() - t0
    print(f"\nTotal elapsed : {elapsed/60:.1f} min ; new_rows={n_new_rows}")

    if not out_csv.exists() or out_csv.stat().st_size == 0:
        print("  [WARN] aucun row genere, pas de rapport.")
        return out_csv
    df = pd.read_csv(out_csv)
    print(f"  read {out_csv} ({len(df)} rows total)")

    if not df.empty:
        summary = _generate_summary(df)
        summary_csv = OUTPUT_DIR / "validation_summary.csv"
        summary.to_csv(summary_csv, index=False)
        print(f"  wrote {summary_csv}")

        _generate_scatters(df)
        report_path = _generate_report(df, summary, elapsed)
        print(f"  wrote {report_path}")

    return out_csv


# ── Metriques agregees ─────────────────────────────────────────────────────────
def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rho a la main : correl(rank(a), rank(b))."""
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra -= ra.mean(); rb -= rb.mean()
    denom = math.sqrt(float((ra ** 2).sum() * (rb ** 2).sum()))
    if denom <= 0:
        return float("nan")
    return float((ra * rb).sum() / denom)


def _generate_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par variante avec les 8 metriques d'evaluation."""
    rows = []
    # Filtrage : on retire les replays dont le mode est theoretical/no_data
    # (le pnl_real n'a pas vraiment ete observe).
    df_obs = df[~df["mode"].isin(["theoretical", "no_data"])]

    for variant, sub in df_obs.groupby("variant", sort=False):
        pred = sub["pnl_pred_at_real_spot"].to_numpy()
        real = sub["pnl_real"].to_numpy()
        ranks = sub["rank"].to_numpy()

        mae   = float(np.mean(np.abs(pred - real))) if len(pred) else float("nan")
        bias  = float(np.mean(pred - real))         if len(pred) else float("nan")
        rmse  = float(np.sqrt(np.mean((pred - real) ** 2))) if len(pred) else float("nan")
        rho_rank   = _spearman(ranks, real)
        rho_pred   = _spearman(pred, real)
        hit_rate   = float(np.mean(real > 0)) * 100 if len(real) else float("nan")
        topk_mean  = float(np.mean(real))            if len(real) else float("nan")
        top1_mean  = float(np.mean(sub.loc[sub["rank"] == 1, "pnl_real"])) \
                       if (sub["rank"] == 1).any() else float("nan")
        top10_mean = float(np.mean(sub.loc[sub["rank"] == TOP_K, "pnl_real"])) \
                       if (sub["rank"] == TOP_K).any() else float("nan")
        calib = (real.mean() / pred.mean()) if pred.mean() != 0 else float("nan")

        rows.append({
            "variant": variant,
            "n_obs": len(sub),
            "spearman_rank_real": rho_rank,
            "spearman_pred_real": rho_pred,
            "topk_mean_real_pct": topk_mean,
            "top1_mean_real_pct": top1_mean,
            "top10_mean_real_pct": top10_mean,
            "hit_rate_pct": hit_rate,
            "MAE_pts": mae,
            "RMSE_pts": rmse,
            "bias_pred_minus_real_pts": bias,
            "calibration_ratio": calib,
        })
    return pd.DataFrame(rows)


def _generate_scatters(df: pd.DataFrame) -> None:
    """Un scatter pred vs real par variante."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df_obs = df[~df["mode"].isin(["theoretical", "no_data"])]
    for variant, sub in df_obs.groupby("variant", sort=False):
        if sub.empty:
            continue
        pred = sub["pnl_pred_at_real_spot"].to_numpy()
        real = sub["pnl_real"].to_numpy()

        rho = _spearman(pred, real)
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.scatter(pred, real, alpha=0.5, s=20, color="steelblue")
        lo = float(min(pred.min(), real.min(), -50))
        hi = float(max(pred.max(), real.max(),  50))
        ax.plot([lo, hi], [lo, hi], "r--", lw=1, label="y=x")
        ax.fill_between([lo, hi], [lo - 10, hi - 10], [lo + 10, hi + 10],
                        color="red", alpha=0.05, label="±10 pts")
        ax.set_xlabel("pnl_pred (% net debit)")
        ax.set_ylabel("pnl_real (% net debit)")
        ax.set_title(f"{variant} — n={len(pred)} — Spearman ρ = {rho:.3f}")
        ax.axhline(0, color="gray", lw=0.5)
        ax.axvline(0, color="gray", lw=0.5)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(alpha=0.3)
        out = OUTPUT_DIR / f"validation_scatter_{variant}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=110)
        plt.close(fig)


def _generate_report(df: pd.DataFrame,
                     summary: pd.DataFrame,
                     elapsed_seconds: float) -> Path:
    out = OUTPUT_DIR / "validation_report.md"
    sample_dates = sorted(df["as_of"].unique())
    n_variants = df["variant"].nunique()
    n_combos_per_variant = df.groupby("variant").size().to_dict()

    # Verdict simple : variante avec meilleur Spearman_rank ET meilleur topk_mean,
    # apres exclusion du baseline `random`.
    cand = summary[summary["variant"] != "random"].copy()
    if not cand.empty:
        cand = cand.dropna(subset=["spearman_rank_real", "topk_mean_real_pct"])
    if cand.empty:
        verdict = "Pas de donnees suffisantes pour un verdict."
    else:
        ref = cand[cand["variant"] == "current"].iloc[0] if (cand["variant"] == "current").any() else None
        winners = []
        if ref is not None:
            for _, row in cand.iterrows():
                if row["variant"] == "current":
                    continue
                gains = []
                if row["spearman_rank_real"] > ref["spearman_rank_real"] + 0.02:
                    gains.append("Spearman+")
                if row["topk_mean_real_pct"] > ref["topk_mean_real_pct"] + 1.0:
                    gains.append("TopK+")
                if abs(row["bias_pred_minus_real_pts"]) < abs(ref["bias_pred_minus_real_pts"]) - 1.0:
                    gains.append("|Bias|↓")
                if len(gains) >= 2:
                    winners.append((row["variant"], gains))
        if not winners:
            verdict = ("Aucune variante ne bat `current` simultanement sur ≥2 metriques. "
                       "Conclusion : garder le moteur actuel ; les ecarts FEAT-028 sur "
                       "le P&L absolu ne penalisent pas significativement le ranking.")
        else:
            verdict_lines = [f"- **{v}** gagne sur : {', '.join(g)}" for v, g in winners]
            verdict = "Variantes qui battent `current` :\n" + "\n".join(verdict_lines)

    rdr_rows = []
    for _, row in summary.iterrows():
        rdr_rows.append(
            f"| {row['variant']:14s} "
            f"| {row['spearman_rank_real']:+.3f} "
            f"| {row['topk_mean_real_pct']:+6.2f} % "
            f"| {row['hit_rate_pct']:5.1f} % "
            f"| {row['MAE_pts']:5.1f} "
            f"| {row['bias_pred_minus_real_pts']:+5.1f} "
            f"| {row['calibration_ratio']:+.2f} "
            f"| {int(row['n_obs']):4d} |"
        )

    md = f"""# Validation ranking — FEAT-029

## Echantillon

- Dates : {len(sample_dates)} (de {sample_dates[0]} a {sample_dates[-1]})
- Symbols : {", ".join(SYMBOLS)}
- Top-K = {TOP_K}
- Variantes testees : {n_variants}
- Combos par variante : {n_combos_per_variant}
- Total elapsed : {elapsed_seconds/60:.1f} min
- Mode filter : on exclut les points replay en mode `theoretical` ou `no_data`

## Tableau recap

| Variante       | Spearman ρ (rank↔real) | Top-K mean | Hit rate | MAE   | Bias  | Calib | n |
|----------------|-----------------------|------------|----------|-------|-------|-------|---|
""" + "\n".join(rdr_rows) + f"""

> Spearman > 0 = meilleurs rangs gagnent plus en moyenne.
> Top-K mean = moyenne des `pnl_real` (% net debit) sur tous les combos top-K.
> Bias > 0 = scoring trop optimiste (predit plus que realise).
> Calib = mean(real) / mean(pred) — bande predite est-elle realiste.

## Verdict

{verdict}

## Top-K mean return par rang

(courbe lue dans `validation_full.csv` : agreger `pnl_real` par `(variant, rank)`)

## Scatters

Voir les fichiers `validation_scatter_<variant>.png` dans le meme dossier.

- Le scatter de `random` doit etre un nuage diffus (ρ ≈ 0).
- Le scatter de `current` doit montrer une correlation positive.

## Limitations

- 3 symbols : echantillon minimal. Etendre a 10+ pour conclusions robustes.
- Top-K biaise vers `pred elevé` : la zone basse est sous-echantillonnee.
- `theoretical` mode exclu : si > 30 % des points y tombent, refletcher la robustesse.
"""

    out.write_text(md, encoding="utf-8")
    return out


if __name__ == "__main__":
    run_validation()
