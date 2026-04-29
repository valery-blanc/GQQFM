# FEAT-021 — Saisie directe d'un combo (bypass scan)

**Status:** IN PROGRESS
**Date:** 2026-04-29

## Context
Le scan peut prendre du temps. Si l'utilisateur connaît déjà un combo qu'il veut analyser
(copié depuis la page Tracker ou saisi manuellement), il doit pouvoir le saisir directement
sans relancer un scan complet.

## Behavior
Pages **Live** et **Backtest** : champ de saisie texte dans la zone principale.
Format attendu (identique au tableau des résultats) :
```
L1 call SPY 17JUL2026 715 | L2 put SPY 17JUL2026 690 | S1 call SPY 15MAY2026 745 | S2 put SPY 15MAY2026 672
```

Règles :
- `L` = Long (direction +1), `S` = Short (direction -1)
- Le chiffre après L/S = quantité (ex: L2 = long 2 contrats)
- Format date : DDMMMYYYY (ex: 17JUL2026)
- Prix = strike en dollars
- Quand un combo est saisi, la page se comporte comme si le scan n'avait retourné qu'un seul résultat
- La saisie directe et le scan sont alternatifs : si un combo est saisi, le scan n'est pas lancé

## Technical spec
### Parsing
Fonction `parse_combo_string(text: str, symbol: str, spot: float) -> Combination | None`

```
L1 call SPY 17JUL2026 715
→ Leg(option_type="call", direction=+1, quantity=1, strike=715, expiration=2026-07-17, ...)
```

- Parsing du format date DDMMMYYYY avec `datetime.strptime(s, "%d%b%Y")`
- entry_price = 0.0 (inconnu), implied_vol = calculé depuis yfinance si disponible
- contract_symbol reconstruit au format OCC : `{SYMBOL}{YYMMDD}{C/P}{STRIKE*1000:08d}`
- net_debit = 0 si tous les entry_price inconnus

### Integration
- Champ `st.text_area` dans la zone principale, avant les résultats
- Bouton "Analyser ce combo" → parse → affichage identique à 1 résultat de scan
- En mode Backtest : le combo saisi est disponible pour le replay

## Impact on existing code
- `ui/app.py` + `ui/page_backtest.py` : ajout du champ + logique de parsing
- Nouveau module `ui/combo_parser.py`
