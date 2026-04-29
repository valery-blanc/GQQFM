# FEAT-020 — Bouton "Lancer le scan" déplacé en zone principale

**Status:** IN PROGRESS
**Date:** 2026-04-29

## Context
Le bouton "Lancer le scan" est actuellement dans la sidebar. Ça pose deux problèmes :
1. Sur petit écran, la sidebar est cachée → bouton inaccessible
2. Ce n'est pas intuitif : le bouton principal d'une page devrait être dans la zone principale

## Behavior
- Pages **Live** et **Backtest** : bouton "Lancer le scan" affiché dans la zone principale, en haut du contenu
- Page **Tracker** : pas de scan → pas de bouton
- Le bouton de la sidebar est supprimé (ou remplacé par un lien vers la zone principale)

## Technical spec
- `ui/app.py` : déplacer/dupliquer le déclenchement du scan depuis la sidebar vers le corps principal
- `ui/components/sidebar.py` : supprimer ou désactiver le bouton scan

## Impact on existing code
- `ui/app.py` : logique de déclenchement du scan (actuellement liée au bouton sidebar)
- `ui/components/sidebar.py` : suppression du bouton
