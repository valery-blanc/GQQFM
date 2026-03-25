# BUG-002 — Nouveaux templates : aucune combinaison pour SPY/AAPL/NVDA/MSFT

**Statut** : FIXED

## Symptôme

Les templates `call_diagonal_backspread` et `call_ratio_diagonal` ne trouvent aucune combinaison pour SPY, AAPL, NVDA, MSFT. Seul QQQ en trouvait (mais avec le bug BUG-001).

## Reproduction

Scanner SPY (ou AAPL/NVDA/MSFT) avec `call_diagonal_backspread` ou `call_ratio_diagonal`.

## Cause racine

Le combinator utilisait toujours `expirations[0]` comme NEAR et `expirations[-1]` comme FAR. Pour SPY, `expirations[0]` = 2 jours. Les calls à 2 jours sont très chers (valeur temps concentrée). Dans un backspread (long N+1 FAR, short N NEAR), le débit est :

```
net_debit = (N+1) × prix_FAR - N × prix_NEAR
```

Si `prix_NEAR` est très élevé (2 jours), `net_debit` devient négatif → filtré par `if net_debit <= 0: continue`.

Les exemples réels dans la spec utilisent des paires proches (ex: Aug 9 → Aug 16, soit 7 jours d'écart).

## Fix appliqué

1. Ajout de `use_adjacent_expiry_pairs: bool = False` dans `TemplateDefinition` (base.py).
2. Dans `combinator.py` : quand ce flag est True, construction d'une liste de toutes les paires d'expirations séparées de 5 à 45 jours.
3. Les deux nouveaux templates ont `use_adjacent_expiry_pairs=True`.

## Section spec impactée

Section 4.4 — Génération des combinaisons (ajout du paramètre `use_adjacent_expiry_pairs`).
Section 4.3 — Templates (nouveaux templates documentés).
