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
- [x] commit e4ae78e

## FEAT-011 — Échéances DTE configurables (sliders sidebar) + défauts durcis

- [x] config.py — `SCANNER_NEAR_EXPIRY_RANGE = (14, 35)` (était (5, 21))
- [x] config.py — `SCANNER_FAR_EXPIRY_RANGE = (35, 90)` (était (25, 90))
- [x] engine/combinator.py — ajout `_build_default_pairs` + paramètres `near_expiry_range` / `far_expiry_range`
- [x] engine/combinator.py — branche `use_adjacent_expiry_pairs=True` filtre maintenant near/far par DTE absolu
- [x] ui/components/sidebar.py — 2 sliders DTE dans expander Avancé
- [x] ui/app.py — passage des plages au combinator + chargement event_calendar utilise `far_max` user
- [x] tests/test_combinator_events.py — fix test multi-paires (passe plages explicites)
- [x] commit 4bf6615

## FEAT-012 — Live ^IRX + fix bug plages DTE strictes

- [x] data/risk_free_rate.py — `fetch_risk_free_rate()` (live + fallback)
- [x] data/provider_yfinance.py — `get_risk_free_rate` utilise le live
- [x] ui/components/sidebar.py — `_cached_risk_free_rate` (TTL 1h) + caption source
- [x] engine/combinator.py — `_select_event_pairs` réduit à 2 étapes (suppression extension près-min/loin-max)
- [x] tests/test_select_event_pairs.py — `test_step2_near_extension` réécrit en `test_strict_range_no_near_extension`
- [x] docs/specs/FEAT-012-rfr-live.md
- [x] docs/specs/option_scanner_spec_v2.md — version + §A3
- [x] commit b033b7b

## FEAT-013 — Backtesting historique via Polygon.io free tier

- [x] data/cache_polygon.py — cache SQLite (TTL infini, key = path+params)
- [x] data/provider_polygon.py — `PolygonHistoricalProvider`, `resolve_polygon_key`, throttle 13s + retry 30s
- [x] backtesting/__init__.py + backtesting/replay.py — `backtest_combo`, `BacktestPoint`
- [x] data/provider_polygon.py — `progress_callback` dans `get_options_chain`
- [x] backtesting/replay.py — `progress_callback` dans `backtest_combo`
- [x] ui/page_backtest.py — page complète : scan + sélection combo + replay graph
- [x] ui/components/sidebar.py — radio Mode + date_input `as_of` conditionnel
- [x] ui/app.py — routage Live / Backtest
- [x] .gitignore — `polygon.key` + `data/.polygon_cache.db`
- [x] polygon.key — clé utilisateur (gitignored)
- [x] Test local : SPY @ 2025-09-15, calendar 665 call → +$288 réalisé après expiration (matched manuellement)
- [x] docs/specs/FEAT-013-backtest-polygon.md
- [x] validation utilisateur sur ANQA — OK (2026-04-28)

## FEAT-014 — Massive (ex-Polygon) plan payant

- [x] data/provider_polygon.py — throttle supprimé (`_RATE_LIMIT_SECONDS = 0.0`)
- [x] data/provider_polygon.py — `_minute_bar_at` + `scan_time` dans `get_contract_close` / `get_underlying_close` / `get_options_chain`
- [x] data/provider_polygon.py — ETA dynamique sur latence réelle + `SCAN_TIME_OPTIONS`
- [x] data/provider_polygon.py — `get_risk_free_rate(as_of)` via yfinance historique
- [x] data/risk_free_rate.py — `fetch_historical_risk_free_rate(as_of)`
- [x] ui/page_backtest.py — ^IRX historique dans `run_backtest_scan` + `scan_time` passé au provider
- [x] ui/components/sidebar.py — défaut `max_combinations=100K`, `as_of=2026-02-05`, selectbox heure ET
- [x] docs/specs/FEAT-014-massive-paid-tier.md
- [x] docs/specs/option_scanner_spec_v2.md — version FEAT-014 + roadmap V2/V3 mise à jour
- [x] validation utilisateur sur ANQA — OK (2026-04-28)

## BUG-007 — Replay 429 + crash Plotly add_vline

- [x] backtesting/replay.py — pré-fetch plage complète en 1 appel/ticker (supprime 429 en rafale)
- [x] ui/page_backtest.py — `leg.expiration.isoformat()` pour add_vline (int+date TypeError)
- [x] ui/page_backtest.py — séparer add_vline / add_annotation (_mean(X) crash sur axe date)
- [x] docs/bugs/BUG-007-replay-429-and-plotly-vline.md

## BUG-008 — DTE calculés sur date.today() au lieu de as_of en backtest

- [x] engine/combinator.py — `as_of` propagé dans `generate_combinations`, `_build_default_pairs`, `_select_event_pairs`
- [x] ui/page_backtest.py — passage `as_of` à `generate_combinations`
- [x] docs/bugs/BUG-008-dte-today-vs-asof.md

## BUG-009 — Jours restants / ex-div utilisent date.today() en backtest

- [x] ui/components/combo_detail.py — `as_of` propagé dans `render_combo_detail`, `_render_exit_plan`, `_check_ex_div_warning`
- [x] ui/page_backtest.py — passage `as_of` + `days_before_close` à `render_combo_detail`
- [x] docs/bugs/BUG-009-days-remaining-today-vs-asof.md

## FEAT-015 — Profil P&L à J-N avant expiration short (0-10j, défaut 3)

- [x] engine/pnl.py — `combinations_to_tensor(days_before_close=0)` — exit_date = close_date - N
- [x] ui/components/sidebar.py — slider "Profil P&L à J-N" dans Avancé (0-10, défaut 3)
- [x] ui/app.py + ui/page_backtest.py — `days_before_close` propagé au tenseur et combo_detail
- [x] ui/components/combo_detail.py — `_render_exit_plan` utilise `days_before_close`, label dynamique J-N
- [x] docs/specs/FEAT-015-pnl-days-before-close.md
- [x] validation utilisateur sur ANQA — OK (2026-04-28)

## FEAT-016 — Replay horaire (précision 1h)

- [x] backtesting/replay.py — `_prefetch_hourly_range` (pagination next_url) + `backtest_combo_hourly`
- [x] backtesting/replay.py — filtrage NYSE : 9h-15h ET, lun-ven
- [x] ui/page_backtest.py — 2e bouton "Lancer le replay (précision horaire)" + `_plot_replay_hourly`
- [x] ui/page_backtest.py — rangeslider Plotly + rangebreaks weekends/heures fermées
- [x] ui/page_backtest.py — slider jours par défaut = (combo.close_date - as_of).days
- [x] ui/page_backtest.py — titre graphe affiche nb barres + plage dates réelle
- [x] docs/specs/FEAT-016-hourly-replay.md

## BUG-010 à BUG-014 — Corrections replay (2026-04-28)

- [x] BUG-010 : weekends et fériés supprimés du replay (carry-forward → skip)
- [x] BUG-011 : slider bt_days_forward session state + tickformat .2f + rangebreaks heures
- [x] BUG-012 : slider clé unique par combo + mode dollar si net_debit≈0 + hover pré-formaté strings
- [x] BUG-013 : zoom initial replay horaire supprimé (données masquées derrière la fenêtre)
- [x] BUG-014 : pagination next_url manquante dans _prefetch_hourly_range (tronquait à 86 barres)
- [x] docs/bugs/BUG-010-014-replay-fixes.md
- [x] validation utilisateur sur ANQA — OK (2026-04-28)

## FEAT-017 — Gain max réaliste ±1σ + ratio market/theoretical replay

- [x] scoring/filters.py — realistic_max_gain() : max P&L dans ±iv×√(T/365)
- [x] scoring/scorer.py — gain_loss_ratio basé sur gain réaliste
- [x] ui/app.py + ui/page_backtest.py — max_gain_real_pct + realistic_range_pct
- [x] ui/components/results_table.py — colonne "Gain ±1σ %"
- [x] ui/components/combo_detail.py — bannière "Gain ±1σ"
- [x] ui/page_backtest.py — caption ratio market/theoretical après replay
- [x] docs/specs/FEAT-017-gain-realiste.md

## FEAT-018 — Résolution intraday configurable (1h / 15min / 5min)

- [x] backtesting/replay.py — _prefetch_intraday_range(multiplier, timespan) + filtre NYSE 9h30-16h
- [x] backtesting/replay.py — RESOLUTIONS + backtest_combo_hourly(resolution=) généralisé
- [x] ui/page_backtest.py — sélecteur résolution + rangebreaks adaptés + fix layout
- [x] docs/specs/FEAT-018-resolution-intraday.md

## FEAT-019 — Tracker de prix réels (Avignon Docker)

- [x] tracker/collector.py — collecte Polygon snapshot toutes les 30min (bid/ask/mid/spot/iv)
- [x] tracker/api.py — FastAPI REST : /health /combos /prices/{id} /pnl/{id}
- [x] tracker/main.py — scheduler APScheduler + uvicorn
- [x] tracker/Dockerfile + requirements.txt + docker-compose.yml (bind mount disque hôte)
- [x] ui/page_tracker.py — liste combos + suppression + graphe comparaison replay vs réel
- [x] ui/components/combo_detail.py — bouton "Tracker ce combo"
- [x] ui/app.py — routing page tracker dans sidebar
- [x] docs/specs/FEAT-019-tracker-prix-reels.md

## BUG-020 — Tracker sync git push cassé → 0 combos sur Avignon

- [x] tracker/api.py — POST /combos + DELETE /combos/{id} + COMBOS_PATH → DATA_DIR
- [x] tracker/collector.py — COMBOS_PATH → DATA_DIR + init_combos_file()
- [x] tracker/main.py — suppression pull_repo + appel init_combos_file()
- [x] tracker/docker-compose.yml — bind mount ~/tracker-data:/data (survie crash Docker)
- [x] ui/components/combo_detail.py — POST /combos au lieu de git push
- [x] ui/page_tracker.py — GET /combos + DELETE /combos/{id} au lieu de fichier local
- [x] docs/bugs/BUG-020-tracker-sync-git-push.md
- [x] Déploiement : rebuild Docker sur Avignon + mkdir ~/tracker-data

## BUG-021 — Radio P&L %/$ ferme le graphe (page Tracker)

- [x] ui/page_tracker.py — show_key en session_state (toggle persistant)
- [x] ui/page_tracker.py — pnl_data mis en cache dans session_state (pnl_key)
- [x] ui/page_tracker.py — radio rendu dans bloc persistant, pas dans if button_clicked
- [x] docs/bugs/BUG-021-radio-pnl-ferme-graphe.md

## FEAT-020 — Bouton "Lancer le scan" en zone principale

- [x] ui/components/sidebar.py — suppression du bouton (scan_clicked = False)
- [x] ui/app.py — st.button "🔍 Lancer le scan" en zone principale (live)
- [x] ui/page_backtest.py — st.button "🔍 Lancer le scan" en zone principale (backtest)
- [x] docs/specs/FEAT-020-scan-button-main-area.md

## FEAT-021 — Saisie directe d'un combo (bypass scan)

- [x] ui/combo_parser.py — parse_combo_string(), resolve_combo_live(), resolve_combo_backtest(), build_single_combo_results()
- [x] ui/app.py — expander "Saisir un combo directement" + bouton "Analyser"
- [x] ui/page_backtest.py — idem pour le mode backtest (Polygon @ as_of)
- [x] docs/specs/FEAT-021-combo-direct-input.md

## FEAT-022 — Nom du combo affiché sur la page Tracker

- [x] ui/page_tracker.py — _combo_to_label() + st.code(label) dans chaque expander
- [x] docs/specs/FEAT-022-combo-name-tracker.md
