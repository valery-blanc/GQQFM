# BUG-003 — Prix hors-séance stales : net_debit erroné (ex: $12 au lieu de ~$200)

**Statut** : FIXED

## Symptôme

Pour une position AAPL S1 C245 APR1 / L2 C265 MAY15, notre app affichait **net_debit = $12**
alors que le même trade sur optionsprofitcalculator montre **Entry cost = $212**.

Le profil P&L avait la même forme mais était décalé (moins de pertes chez nous car les prix
d'entrée étaient sous-estimés).

## Cause racine

Hors séance, yfinance retourne bid=ask=0 pour toutes les options. Notre code utilisait
`lastPrice` comme prix mid de fallback. Or `lastPrice` représente le **dernier trade** de
la session précédente, potentiellement quand le sous-jacent était à un prix très différent.

Exemple concret (AAPL spot=$251.64) :
- APR1 $245 call : lastPrice=$10.80 (stale depuis AAPL ~$256), valeur réelle ~$8-9
- MAY15 $265 call : lastPrice=$5.46 (proche de la valeur réelle)
- net_debit (stale) = (-10.80 + 2×5.46) × 100 = **$12**
- net_debit (correct) ≈ (-8.76 + 2×6.57) × 100 = **$438**

Les options ITM à courte échéance sont particulièrement touchées car leur prix change
rapidement avec le spot (delta élevé).

## Fix appliqué

Approche en deux passes par expiration dans `data/provider_yfinance.py` :

1. **Détection hors-séance** : si toutes les options d'une expiration ont bid=ask=0
2. **Calcul IV consensus** : utilise les options OTM (peu sensibles aux mouvements de spot)
   dont la `lastPrice` donne une IV plausible (0.05 ≤ IV ≤ 1.5). Prend la médiane.
3. **Re-pricing BS** : re-price TOUTES les options avec BS(spot_courant, IV_consensus)
   au lieu d'utiliser lastPrice directement.

Ce re-pricing corrige les prix stales des options ITM et normalise le pricing
par rapport au spot courant.

## Limitation résiduelle

L'IV consensus est calculée à partir de lastPrices qui sont eux-mêmes légèrement stales.
En période de forte volatilité ou de grands mouvements de gap, le consensus peut être
décalé de l'IV réelle. Les résultats restent indicatifs hors séance.

## Section spec impactée

Section 3.3 — Filtrage initial des données (comportement hors-séance).
