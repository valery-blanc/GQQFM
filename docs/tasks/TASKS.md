# TASKS

## Done

- [x] config.py + requirements.txt + pyproject.toml
- [x] data/models.py (OptionContract, OptionsChain, Leg, Combination, ScoringCriteria)
- [x] data/provider_base.py (Protocol DataProvider)
- [x] data/provider_yfinance.py (implémentation Yahoo Finance)
- [x] engine/backend.py (abstraction GPU/CPU : xp = cupy ou numpy)
- [x] engine/black_scholes.py (BS vectorisé + intrinsic_value)
- [x] engine/pnl.py (compute_pnl_batch, combinations_to_tensor, batching mémoire)
- [x] engine/combinator.py (generate_combinations par template)
- [x] templates/base.py (LegSpec, TemplateDefinition)
- [x] templates/calendar_strangle.py
- [x] templates/double_calendar.py
- [x] templates/reverse_iron_condor.py
- [x] scoring/probability.py (distribution log-normale, trapèzes)
- [x] scoring/filters.py (filter_combinations sur GPU)
- [x] scoring/scorer.py (score composite normalisé)
- [x] ui/app.py (pipeline complet Streamlit)
- [x] ui/components/sidebar.py
- [x] ui/components/chart.py (Plotly P&L profile)
- [x] ui/components/results_table.py
- [x] ui/components/combo_detail.py
- [x] tests/test_black_scholes.py
- [x] tests/test_pnl.py
- [x] tests/test_combinator.py
- [x] tests/test_scoring.py
- [x] Suppression de option_scanner_spec.md (remplacé par v2)

## FEAT-001 — Nouveaux templates Call Diagonal Backspread + Call Ratio Diagonal

- [x] templates/call_diagonal_backspread.py (nouveau fichier)
- [x] templates/call_ratio_diagonal.py (nouveau fichier)
- [x] templates/__init__.py — ajout imports et enregistrement ALL_TEMPLATES
- [x] templates/base.py — ajout use_adjacent_expiry_pairs dans TemplateDefinition
- [x] engine/combinator.py — support adjacent expiry pairs + réécriture (fix BUG-001)
- [x] engine/pnl.py — nombre de legs dynamique (max legs, pas hardcodé à 4)
- [x] docs/specs/FEAT-001-new-templates.md
- [x] docs/bugs/BUG-001-combinator-indentation.md
- [x] docs/bugs/BUG-002-new-templates-no-combos.md
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour section 4

## FEAT-002 — Multi-ticker

- [x] ui/components/sidebar.py — input "Sous-jacent(s)" multi-ticker
- [x] ui/app.py — run_multi_scan, top 100 agrégé
- [x] ui/components/results_table.py — colonne Ticker + fix rows.append
- [x] docs/specs/FEAT-002-multi-ticker.md
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour section UI

## Corrections diverses

- [x] data/provider_yfinance.py — fallback lastPrice + bisection IV (off-hours)
- [x] BUG-003 : data/provider_yfinance.py — re-pricing BS avec IV consensus (fix prix stales hors-séance)

## FEAT-003 — Colonne Legs multi-lignes

- [x] ui/components/results_table.py — legs sur N lignes, format S3 call AAPL 09AUG2024 245.5
- [x] ui/components/results_table.py — police 82% via CSS injection
- [x] docs/specs/FEAT-003-legs-multiline.md
- [x] engine/combinator.py — max_iterations=2_000_000 (timeout protection)
- [x] ui/components/chart.py — hauteur 4× + fix opacity plotly (rgba)
- [x] ui/components/sidebar.py — valeurs par défaut assouplies
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour complète (sections 5, 8, 9, 12, 13, Annexe A)

## FEAT-004 — Screener automatique de sous-jacents

### events/ (module partagé)
- [x] events/__init__.py
- [x] events/models.py (EventImpact, EventScope, MarketEvent)
- [x] events/fomc_calendar.py (table statique 2026)
- [x] events/finnhub_calendar.py (API Finnhub + TRACKED_EVENTS)
- [x] events/calendar.py (EventCalendar : load, classify_events_for_pair)

### screener/ (module screener)
- [x] screener/__init__.py
- [x] screener/models.py (OptionsMetrics interne, ScreenerResult public)
- [x] screener/universe.py (UNIVERSE ~128 tickers)
- [x] screener/stock_filter.py (filtre rapide batch yfinance)
- [x] screener/event_filter.py (earnings/ex-div filter)
- [x] screener/options_analyzer.py (analyse détaillée, select_expirations, HV30, ATM IV)
- [x] screener/scorer.py (score composite, disqualification, classement)
- [x] screener/screener.py (UnderlyingScreener.screen(), pipeline)

### config.py
- [x] config.py — ajout constantes screener + EventCalendar

### UI
- [x] ui/components/sidebar.py — section screener (bouton, progress, résultats, inject tickers)

### Tests (39/39 passent)
- [x] tests/test_event_calendar.py (T10-T13 + 5 tests supplémentaires)
- [x] tests/test_screener_scoring.py (T1-T5 + tests pénalités/disqualification)
- [x] tests/test_screener_filters.py (T6-T9 + tests select_expirations)
- [x] tests/test_screener_integration.py (T14 + test champs ScreenerResult)

### Docs
- [x] docs/specs/FEAT-004-screener.md
- [x] docs/specs/option_scanner_spec_v2.md — ajout section 14 screener + version FEAT-004

## FEAT-005 — Intégration EventCalendar dans le scanner

- [x] data/models.py — event_score_factor + events_in_sweet_zone dans Combination
- [x] config.py — SCANNER_NEAR_EXPIRY_RANGE + SCANNER_FAR_EXPIRY_RANGE
- [x] engine/combinator.py — paramètre event_calendar + _select_event_pairs + multi-paires
- [x] scoring/scorer.py — paramètre event_score_factors (multiplicateur)
- [x] ui/app.py — chargement EventCalendar + passage event_factors au scorer
- [x] ui/components/results_table.py — colonne Events optionnelle
- [x] ui/components/chart.py — annotation dorée events_in_sweet_zone
- [x] tests/test_combinator_events.py (Tests 1-5)
- [x] tests/test_scorer_events.py (Tests 6-9)
- [x] docs/specs/FEAT-005-scanner-events.md
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour section 3, 5, 8, 13

## PERF-001 — Parallélisation screener

- [x] config.py — SCREENER_MAX_WORKERS = 5
- [x] screener/event_filter.py — filter_by_events parallélisé (ThreadPoolExecutor, étape 4)
- [x] screener/options_analyzer.py — batch_compute_hv30 + hv30_precomputed dans analyze_ticker
- [x] screener/screener.py — batch HV30 avant boucle + étape 5 parallélisée (ThreadPoolExecutor)
- [x] docs/specs/PERF-001-screener-parallelisation.md
- [x] docs/specs/option_scanner_spec_v2.md — section 14.2 + roadmap V2 + config

## BUG-004 — Screener retourne 0 résultats hors-séance

- [x] screener/options_analyzer.py — get_atm_iv : fallback IV depuis lastPrice (approximation ATM)
- [x] screener/options_analyzer.py — compute_chain_liquidity : spread=0.0 + sentinelle OI=999_999 quand données indisponibles hors-séance
- [x] screener/scorer.py — no_open_interest : désactivé quand OI=999_999 (sentinelle)
- [x] docs/bugs/BUG-004-screener-zero-results-offhours.md
- [x] docs/specs/option_scanner_spec_v2.md — section 14 screener mis à jour

## FEAT-006 — Correction du filtrage événementiel dans la sélection d'expirations

- [x] engine/combinator.py — _select_event_pairs : algorithme 4 étapes (remplace fallback permissif)
- [x] data/models.py — event_warning : str | None = None dans Combination
- [x] ui/components/combo_detail.py — affichage st.warning(event_warning) au-dessus des legs
- [x] ui/components/chart.py — annotation rouge si event_warning
- [x] tests/test_select_event_pairs.py — 6 tests (Tests 1-6)
- [x] docs/specs/FEAT-006-scanner-events.md — statut DONE
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour section 5, version FEAT-006

## FEAT-007 — Améliorations UI (Events dates, Finnhub indicator, API key file)

- [x] ui/components/results_table.py — colonne Events avec dates DD/MM
- [x] ui/components/sidebar.py — indicateur Finnhub dans section GPU Info
- [x] events/calendar.py — resolve_api_key() : paramètre > env var > finnhub.key > config.py
- [x] finnhub.key — fichier de clé API

## FEAT-008 — Pricing américain Bjerksund-Stensland 1993

- [x] data/models.py — div_yield dans OptionContract, OptionsChain, Leg
- [x] data/provider_yfinance.py — fetch dividendYield depuis ticker.info
- [x] engine/combinator.py — propagation div_yield dans les Legs
- [x] engine/pnl.py — tenseur div_yields + wiring vers bs_american_price
- [x] engine/black_scholes.py — _bs93_phi, _bs93_american_call, bs_american_price (put-call transformation)
- [x] tests/test_black_scholes.py — 8 tests américains (intrinsèque, vectorisé, bounds)
- [x] tests/test_combinator_events.py — fix test sweet_zone (format dates DD/MM)

## RECO-1 — Grille spot élargie ±25%

- [x] config.py — SPOT_RANGE_LOW=0.75, SPOT_RANGE_HIGH=1.25

## RECO-2 — Warning ex-dividende dans combo_detail

- [x] ui/components/combo_detail.py — _check_ex_div_warning + paramètre symbol
- [x] ui/app.py — passage symbol à render_combo_detail

## RECO-3 — Micro-optimisation xp.clip dans pnl.py

- [x] engine/pnl.py — safe_tte et safe_vol optimisés

## BUG-005 — Choix pricer BS/américain non câblé

- [x] engine/pnl.py — _compute_pnl_batch_chunk : branching bs_price / bs_american_price selon use_american_pricer
- [x] ui/app.py — passage de params["use_american_pricer"] à compute_pnl_batch

## FEAT-009 — Suite de tests propriétés (pricers + PnL)

- [x] requirements.txt — ajout `hypothesis >= 6.100`
- [x] tests/test_pricer_properties.py — TestEuropeanProperties (parité, bornes, monotonie)
- [x] tests/test_pricer_properties.py — TestAmericanProperties (intrinsèque, q=0)
- [x] tests/test_pricer_properties.py — TestDividendBoundary (sensibilité q, continuité seuil)
- [x] tests/test_pricer_properties.py — TestPnLAttribution (inversion, linéarité, expiration)
- [x] docs/specs/FEAT-009-pricer-properties.md
- [x] Première exécution + analyse des findings (17 passed / 6 failed)
- [x] BUG-006 ouvert : overflow float32 + put dégénéré quand r≈0

## BUG-006 — BS-1993 overflow float32 et put dégénéré quand r ≈ 0

- [x] docs/bugs/BUG-006-bs93-overflow-and-r0-degeneracy.md (status FIXED)
- [x] engine/black_scholes.py — fallback put européen quand `rate ≤ 1e-6`
- [x] engine/black_scholes.py — garde-fou isfinite final → intrinsèque
- [x] tests/test_pricer_properties.py — TestZeroRateFallback (6 cas régression)
- [x] tests/test_pricer_properties.py — domaine hypothesis resserré (vol≥0.10, r≥0.005)
- [x] docs/specs/option_scanner_spec_v2.md — §5.2 note sur cas r ≈ 0
- [x] pytest : 29/29 propriétés + 23/23 existants (BS, PnL) passed
- [x] validation utilisateur (commit 85e450e déjà pushé)

## FIX UI — Ligne spot courant invisible (blanc sur fond blanc)

- [x] ui/components/chart.py — couleur "black" + dash="dash" (commit d27d439)

## FEAT-010 — Panneau "Plan de sortie" dans combo_detail

- [x] ui/components/combo_detail.py — fonction `_render_exit_plan`
- [x] docs/specs/FEAT-010-exit-plan-panel.md
- [x] docs/specs/option_scanner_spec_v2.md — section UI
- [x] commit e7c88b6 (1ère version : %  fixes +30 %/-50 %)

## FEAT-010b — Plan de sortie calibré sur la courbe P&L réelle

- [x] ui/components/combo_detail.py — target = max P&L dans ±3 % spot, stop = max_loss_pct
- [x] ui/components/combo_detail.py — signature `_render_exit_plan` : ajout pnl_tensor, spot_range, current_spot
- [x] ui/app.py — passage pnl_tensor / spot_range / current_spot à render_combo_detail
- [x] docs/specs/option_scanner_spec_v2.md — section UI réécrite
- [ ] validation utilisateur, puis commit unique
