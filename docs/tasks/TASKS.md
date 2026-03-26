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

## FEAT-006 — Correction du filtrage événementiel dans la sélection d'expirations

- [x] engine/combinator.py — _select_event_pairs : algorithme 4 étapes (remplace fallback permissif)
- [x] data/models.py — event_warning : str | None = None dans Combination
- [x] ui/components/combo_detail.py — affichage st.warning(event_warning) au-dessus des legs
- [x] ui/components/chart.py — annotation rouge si event_warning
- [x] tests/test_select_event_pairs.py — 6 tests (Tests 1-6)
- [x] docs/specs/FEAT-006-scanner-events.md — statut DONE
- [x] docs/specs/option_scanner_spec_v2.md — mise à jour section 5, version FEAT-006
