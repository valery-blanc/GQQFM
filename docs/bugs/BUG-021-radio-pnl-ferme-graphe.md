# BUG-021 — Radio P&L %/$ ferme le graphe et perd la sélection

**Status:** IN PROGRESS
**Date:** 2026-04-29

## Symptom
Page Tracker prix réel : quand on clique sur le radio "$ (absolu)", le graphe disparaît.
En le rouvrant (bouton "Afficher P&L réel"), la sélection est revenue à "% (/ débit)".

## Root cause
Le bouton "Afficher P&L réel" conditionne l'affichage (`if show_real or show_bt`).
Streamlit re-exécute tout le script à chaque interaction, y compris le clic sur le radio.
Le radio est à l'intérieur du bloc conditionnel → il disparaît au prochain rerun,
et la valeur n'est pas persistée en session_state.

## Fix applied
- Sortir le radio du bloc conditionnel : le placer toujours visible (indépendamment du bouton)
- Persister la sélection dans `st.session_state[f"pnl_mode_{combo_id}"]`
- Utiliser `st.session_state` pour piloter le graphe au lieu de réagir au click du bouton

## Spec section impacted
FEAT-019 — Tracker de prix réels, section UI page_tracker.py
