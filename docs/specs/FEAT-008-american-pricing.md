# FEAT-008 — Pricing américain Bjerksund-Stensland 1993

**Statut** : DONE
**Date** : 2026-03-26

## Contexte

La revue de code a identifié que le pricer Black-Scholes européen ne capture pas
la prime d'exercice anticipé des options américaines (la quasi-totalité des options
sur actions US sont de style américain). L'impact est significatif pour les puts
deep ITM et les calls sur actions à dividende élevé.

## Solution

Implémentation de l'approximation analytique de Bjerksund-Stensland 1993 :

- **Calls sans dividende** : retourne le prix européen (exercice anticipé jamais optimal)
- **Calls avec dividende** : approximation B-S 1993 avec frontière d'exercice plate
- **Puts** : transformation put-call P(S,K,T,r,q,σ) = C(K,S,T,q,r,σ)
- **Plancher** : max(valeur américaine, valeur intrinsèque) — sécurité numérique

### Précision

L'approximation B-S 1993 est typiquement < 0.1% d'erreur par rapport aux solutions
numériques exactes (arbres binomiaux, différences finies). C'est le standard
de l'industrie pour le pricing analytique d'options américaines.

### Choix 1993 vs 2002

La version 2002 améliore la précision en utilisant deux frontières d'exercice
(au lieu d'une), mais nécessite la CDF normale bivariée. Cette dernière n'est
pas disponible nativement dans CuPy et serait coûteuse à implémenter de manière
vectorisée sur GPU. La version 1993 offre un excellent compromis précision/performance.

## Fichiers modifiés

| Fichier | Modification |
|---|---|
| `data/models.py` | `div_yield: float = 0.0` dans OptionContract, OptionsChain, Leg |
| `data/provider_yfinance.py` | Fetch `dividendYield` depuis `ticker.info` |
| `engine/combinator.py` | Propagation `div_yield` dans la création des Legs |
| `engine/pnl.py` | Tenseur `div_yields` + appel `bs_american_price` au lieu de `bs_price` |
| `engine/black_scholes.py` | `_bs93_phi`, `_bs93_american_call`, `bs_american_price` |
| `tests/test_black_scholes.py` | 8 tests : sans div, avec div, deep ITM, vectorisé, bounds |
| `ui/components/combo_detail.py` | Warning ex-dividende (RECO-2) |
| `ui/app.py` | Passage du symbole à `render_combo_detail` |

## Algorithme B-S 1993

### Fonction phi auxiliaire

φ(S, T, γ, H, I, r, b, σ) = exp(λ) × S^γ × [N(d) - (I/S)^κ × N(d₂)]

où :
- λ = (-r + γb + ½γ(γ-1)σ²) × T
- d = -[ln(S/H) + (b + (γ-½)σ²)T] / (σ√T)
- κ = 2b/σ² + (2γ - 1)
- d₂ = d - 2ln(I/S) / (σ√T)

### Call américain

1. b = r - q (cost of carry)
2. β = (½ - b/σ²) + √[(b/σ² - ½)² + 2r/σ²]
3. B∞ = (β/(β-1)) × K
4. B₀ = max(K, r/q × K)
5. ht = -(bT + 2σ√T) × B₀ / (B∞ - B₀)
6. I = B₀ + (B∞ - B₀)(1 - e^ht)
7. α = (I - K) × I^(-β)

Si S ≥ I : exercice immédiat → S - K
Sinon : formule à 6 termes phi.

### Put américain (transformation)

P(S, K, T, r, q, σ) = C(K, S, T, q, r, σ)
