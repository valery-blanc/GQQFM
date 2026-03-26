---
id: BUG-004
title: Screener retourne 0 résultats hors-séance
status: FIXED
date: 2026-03-26
---

## Symptôme

Le screener automatique ne retourne aucun résultat quand le marché US est fermé
(bid=ask=0 sur les options), alors qu'il fonctionnait pendant les heures de marché.

## Reproduction

Lancer le screener n'importe quel soir après 22h00 heure de Paris (16h00 ET).

## Cause racine

Quand bid=ask=0 (hors-séance), yfinance retourne des valeurs absurdes :

1. **`impliedVolatility` ≈ 0** (calculé à partir du mid=0 → quasi-zéro) →
   filtre `iv_data_missing` dans `check_disqualification` élimine tout ticker.

2. **`openInterest` = 0** pour la quasi-totalité des strikes (yfinance ne met
   pas à jour l'OI après clôture) → filtre `no_open_interest` élimine les
   tickers restants.

3. **spread bid-ask** = 0.20 (valeur par défaut quand aucun mid valide) →
   filtre `spread_too_wide` peut aussi éliminer.

## Fix appliqué

### `screener/options_analyzer.py`

**`get_atm_iv`** : fallback hors-séance via `lastPrice`.

Quand `impliedVolatility < 0.01` pour toutes les options ATM (bid=ask=0),
recalcul de l'IV depuis `lastPrice` via l'approximation ATM :
`C_time ≈ S × σ × sqrt(T/(2π))`.
Résultat typique pour SPY : IV near ≈ 25%, IV far ≈ 23% (cohérent avec l'IV réelle).
Nécessite `expiry` et `today` en paramètres supplémentaires (optionnels).

**`compute_chain_liquidity`** : deux corrections hors-séance.

- Spread : quand `valid_mid` est vide (bid=ask=0 pour TOUTE la chaîne),
  retourne `spread_pct = 0.0` au lieu de 0.20.
  Interprétation : spread non mesurable → non pénalisé.

- OI : quand <5% des options ont OI>0 (yfinance OI stale/absent),
  retourne `avg_oi = 999_999.0` (sentinelle "données indisponibles").

### `screener/scorer.py`

**`no_open_interest`** : désactivé quand OI non disponible.

```python
# Avant
lambda m: (m.avg_oi_near + m.avg_oi_far) / 2 < config.SCREENER_MIN_AVG_OPEN_INTEREST

# Après
lambda m: (
    m.avg_oi_near < 999_000 and m.avg_oi_far < 999_000
    and (m.avg_oi_near + m.avg_oi_far) / 2 < config.SCREENER_MIN_AVG_OPEN_INTEREST
)
```

## Résultat

SPY, QQQ, IWM et autres ETFs liquides passent maintenant le screening
hors-séance avec des IV calculées depuis `lastPrice` (25%, 27%, 34%
respectivement). 85/85 tests passent.

**Note** : les résultats hors-séance restent moins précis qu'en séance
(IV calculée à partir des derniers prix traités, pas du mid actuel).
Le warning "Marché US fermé" dans la sidebar reste affiché.
