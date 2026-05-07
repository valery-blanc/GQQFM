# FEAT-027 — Réorganisation des pages (tabs + grille)

**Status:** IN PROGRESS
**Date:** 2026-05-07

## Context

L'interface actuelle utilise un menu latéral (sidebar) pour toute la navigation et les
paramètres. Avec la croissance des fonctionnalités, la sidebar est surchargée. Cette
feature réorganise l'UI autour de tabs et de pages dédiées.

## Behavior

### Navigation
5 tabs (st.tabs) remplacent entièrement le menu latéral :
- **Live** — scan en temps réel
- **Backtest** — scan historique
- **Tracker prix réel** — suivi des combos
- **Screener sous-jacents** — screening automatique
- **Paramètres** — tous les paramètres du scan

### Sidebar
Supprimée. Tous les contrôles migrent dans les tabs.

### Page Paramètres
Contient tout ce qui était dans la sidebar, sauf :
- L'input de ticker (déplacé dans les pages Live et Backtest)
- La date/heure as_of (déplacée dans la page Backtest)
- Le screener (déplacé dans la page Screener)

### Page Live
- Input "Sous-jacent(s)" directement au-dessus du bouton "Lancer le scan"
- Bouton "🔍 Lancer le scan"
- Résultats en **vue grille par défaut**

### Page Backtest
- Date d'entrée (as_of) + heure du scan (ET)
- Input "Sous-jacent(s)"
- Bouton "🔍 Lancer le scan"
- Résultats en **vue grille par défaut**

### Vue grille (Live + Backtest)
- 4 lignes × 6 colonnes = 24 mini-graphes par page
- Chaque mini-graphe : profil P&L simplifié, height=280px, use_container_width
- Navigation : boutons "◀ Préc." et "Suiv. ▶" (24 résultats par page)
- Défaut : vue grille
- Toggle radio : "Grille" | "Vue unique"
- Clic sur "Sélectionner" sous un mini-graphe → passe en vue unique pour ce combo
- Vue unique : graphe principal height=600px (était 1680, réduit pour tenir dans la page)

## Technical spec

### Fichiers créés
- `ui/page_params.py` — rendu des widgets paramètres
- `ui/page_screener.py` — rendu du screener
- `ui/page_live.py` — page Live avec run_scan + grid view

### Fichiers modifiés
- `ui/app.py` — routeur tabs, plus de sidebar
- `ui/components/sidebar.py` — réduit à `get_base_params()` + `_cached_risk_free_rate()`
- `ui/components/chart.py` — ajout `plot_pnl_mini()` ; height principale 1680→600
- `ui/page_backtest.py` — inputs date/ticker en haut, grid view

### Session state keys (params page)
| Key | Widget | Default |
|-----|--------|---------|
| `tmpl_{name}` | checkbox | True |
| `p_max_loss_pct` | number_input | -50.0 |
| `p_max_loss_prob` | number_input | 25.0 |
| `p_min_gain_pct` | number_input | 10.0 |
| `p_min_gl_ratio` | number_input | 0.1 |
| `p_max_debit` | number_input | 10000 |
| `p_min_volume` | number_input | 0 |
| `p_vol_low` | slider | 0.8 |
| `p_vol_high` | slider | 1.2 |
| `p_risk_free_rate` | number_input | live ^IRX |
| `p_max_combos` | number_input | 400000 |
| `p_days_before_close` | slider | 3 |
| `p_near_expiry` | slider | (14,35) |
| `p_far_expiry` | slider | (35,90) |
| `p_pricer` | radio | "Pricer américain : Bjerksund-Stensland" |
| `score_weights` | ScoreWeights | ScoreWeights() |

### Session state keys (live tab)
| Key | Rôle |
|-----|------|
| `live_symbols_input` | ticker input live |
| `live_results` | résultats scan live |
| `live_selected_idx` | combo sélectionné |
| `view_mode_live` | "Grille" \| "Vue unique" |
| `grid_page_live` | page grille (0-based) |

### Session state keys (backtest tab)
| Key | Rôle |
|-----|------|
| `bt_symbols_input` | ticker input backtest |
| `bt_as_of` | date as_of |
| `bt_scan_time_label` | heure scan |
| `bt_results` | résultats scan (inchangé) |
| `bt_selected_idx` | combo sélectionné (inchangé) |
| `view_mode_bt` | "Grille" \| "Vue unique" |
| `grid_page_bt` | page grille (0-based) |

## Impact on existing code

- `run_scan()` et `run_multi_scan()` migrent de `app.py` vers `page_live.py`
- `render_sidebar()` supprimée
- `_find_combo_in_results()` supprimée (inutilisée)
- Le screener injecte dans `live_symbols_input` ET `bt_symbols_input`
- `page_backtest.py` : `params.get("as_of")` remplacé par widget local ; idem `scan_time`, `symbols`
