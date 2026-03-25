# BUG-001 — Combinator : indentation erronée dans la boucle interne

**Statut** : FIXED

## Symptôme

Pour QQQ, le meilleur résultat du template `call_diagonal_backspread` affichait un Net Debit de **$21** alors qu'une vraie diagonal coûte plusieurs centaines de dollars.

## Reproduction

Scanner QQQ avec le template `call_diagonal_backspread` ou `call_ratio_diagonal`.

## Cause racine

Dans `engine/combinator.py`, le corps de la boucle interne `for leg_selections in product(*leg_candidates):` était mal indenté. Le bloc `legs = []`, les checks de contraintes, le calcul de `net_debit` et l'appel `all_combos.append()` étaient **en dehors** de la boucle — au niveau du `for near_exp, far_exp in expiry_pairs`. Conséquence : seul le **dernier** élément du produit cartésien était traité, donnant un résultat arbitraire.

## Fix appliqué

Réécriture complète de `engine/combinator.py` avec indentation correcte. Toute la logique de construction de `legs`, vérification des contraintes, calcul de `net_debit` et `all_combos.append()` est maintenant correctement à l'intérieur du `for leg_selections in product(...)`.

## Section spec impactée

Section 4.4 — Génération des combinaisons.
