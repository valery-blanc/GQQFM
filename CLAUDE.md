# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Options P&L Profile Scanner** — a Python/Streamlit app that scans 4-leg option combinations to find positions matching user-defined profit/loss profiles. GPU-accelerated via CuPy on an NVIDIA RTX 5070 Ti (Blackwell, CUDA 12.8+).

Full specs: `docs/specs/option_scanner_spec_v2.md`

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Streamlit UI
streamlit run ui/app.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_engine.py

# Run with GPU benchmark
pytest tests/test_performance.py -v
```

## Architecture

```
options-scanner/
├── data/       # DataProvider protocol, OptionContract/OptionsChain dataclasses, yfinance adapter
├── engine/     # Black-Scholes GPU pricing (CuPy), batch P&L computation (V×C×M tensor)
├── templates/  # Strategy templates: calendar_strangle, double_calendar, reverse_iron_condor
├── scoring/    # Log-normal loss probability, filter criteria, composite scorer
├── ui/         # Streamlit app + Plotly chart components
└── tests/
```

### Key design points

**GPU engine** (`engine/`): The core computation uses CuPy arrays. P&L is computed as a 3D batch of shape `(V, C, M)` — vol scenarios × combinations × spot price points. Memory budget targets ~6 GB for 500K combos × 200 spots × 3 vol scenarios on 16 GB VRAM. Batching logic must respect this budget.

**Combinator** (`engine/` + `templates/`): Template-driven, not exhaustive enumeration. Each template (e.g. `CalendarStrangle`) defines how to vary strikes/expirations/quantities to generate candidate combinations. This keeps the search space at 100K–500K instead of billions.

**DataProvider** (`data/`): Defined as a Protocol so it's swappable (Yahoo Finance for V1, IBKR/Tradier later). Initial filtering drops contracts with `bid=0`, spread >20%, or open interest <10. Only strikes ±20% of spot and expirations 2–90 days out are kept.

**Scorer** (`scoring/`): Filtering runs on GPU before any CPU transfer. Loss probability uses a log-normal distribution over the holding period. Composite score = weighted sum of gain/loss ratio, loss probability, expected return.

## Tech stack

| Layer | Library |
|---|---|
| GPU compute | `cupy-cuda12x ≥13.0`, CUDA 12.8+ |
| Data | `yfinance ≥0.2.36` |
| UI | `streamlit ≥1.31`, `plotly ≥5.18` |
| Math | `numpy`, `scipy`, `pandas` |

## Roadmap

- **V1 (MVP)**: CalendarStrangle template only, Yahoo Finance, full GPU engine, Streamlit UI
- **V2**: Double Calendar + Reverse Iron Condor templates, CSV/JSON export, improved scoring
- **V3**: IBKR/Tradier data sources, backtesting, real-time alerts, vol smile modeling

## Workflow Rules

### Task Tracking
For any task that involves more than 3 files or more than 3 steps:
1. BEFORE starting, create/update a checklist in `docs/tasks/TASKS.md`
2. Mark each sub-step with `[ ]` (todo), `[x]` (done), or `[!]` (blocked)
3. Update the checklist AFTER completing each sub-step
4. If the session is interrupted, the checklist is the source of truth for resuming work

### Resuming Work
When starting a new session or after /clear, ALWAYS:
1. Read `docs/tasks/TASKS.md` to check current progress
2. Identify the first unchecked item
3. Resume from there — do NOT restart completed work

### Documentation Synchronization (OBLIGATOIRE)

**À chaque demande de modification, bug fix ou nouvelle feature — quelle que soit
la façon dont elle est formulée (message direct, fichier temp_*.txt, description
orale) — TOUJOURS :**

1. **Créer ou mettre à jour le fichier de bug** (`docs/bugs/BUG-XXX-*.md`)
   ou de feature (`docs/specs/FEAT-XXX-*.md`) correspondant.

2. **Mettre à jour `docs/specs/option_scanner_spec.md`** — OBLIGATOIRE, SANS EXCEPTION.
   Ce fichier est la source de vérité de l'application. Il doit refléter à tout
   moment le comportement réel du code. Mettre à jour :
   - La section concernée (UI, architecture, algorithmes, etc.)
   - Le numéro de version en en-tête (FEAT-XXX / BUG-XXX)
   - La structure du projet si des fichiers sont ajoutés/supprimés
   - Les cas limites si un nouveau cas est géré
   Ne pas attendre qu'on le demande. Si la feature est trop petite pour un §
   dédié, intégrer l'info dans la section la plus proche.

3. **Mettre à jour `docs/tasks/TASKS.md`** — toujours, sans condition :
   ajouter l'entrée si elle n'existe pas, cocher `[x]` les étapes terminées.

Cette règle s'applique MÊME pour les petites modifications demandées directement
dans le chat. Si c'est trop petit pour un fichier BUG/FEAT dédié, au minimum
mettre à jour `docs/specs/option_scanner_spec.md` si le comportement change.

### Règle de confirmation avant commit (OBLIGATOIRE)

**Aucun commit ne doit être créé avant que l'utilisateur ait testé et confirmé.**

Ordre impératif pour tout bug fix ou feature :

```
[code] → [docs] → [relancer streamlit] → [demander test sur http://localhost:8501/] → [attendre OK] → [commit]
```

Pour relancer Streamlit (tuer l'instance existante et redémarrer) :
```bash
pkill -f "streamlit run" 2>/dev/null; C:/Users/Val/AppData/Local/Programs/Python/Python311/python.exe -m streamlit run ui/app.py
```

- Le commit regroupe TOUJOURS : code source + fichiers de doc + TASKS.md
- Si l'utilisateur signale un problème après test → corriger, relancer,
  re-demander confirmation AVANT de committer
- **Si un crash ou erreur est découvert lors du test** → créer `docs/bugs/BUG-XXX-*.md`
  (même si le problème a déjà été corrigé), mettre à jour `docs/specs/option_scanner_spec.md`
  avec la règle à retenir, et référencer dans `docs/tasks/TASKS.md`
- Aucune exception : même pour une modification d'une seule ligne

### Bug Fix Workflow
1. Documenter le bug dans `docs/bugs/BUG-XXX-short-name.md` (symptôme,
   reproduction, logs/traceback, section spec impactée)
2. Analyser la cause racine AVANT d'écrire le fix (Plan Mode)
3. Implémenter le fix
4. Mettre à jour toute la documentation :
   - `docs/bugs/BUG-XXX-*.md` → statut `FIXED`, fix appliqué décrit
   - **`docs/specs/option_scanner_spec.md` → OBLIGATOIRE** : mettre à jour la section du comportement corrigé
   - `docs/tasks/TASKS.md` → cocher `[x]` toutes les étapes terminées
5. **Lancer l'application** : `streamlit run ui/app.py`
6. **Demander à l'utilisateur de tester et attendre sa confirmation explicite**
   — NE PAS committer avant que l'utilisateur confirme que c'est OK
7. Une fois confirmé : committer TOUS les fichiers modifiés en un seul commit
   (code + docs + TASKS.md) : `"FIX BUG-XXX: description courte"`

### Feature Evolution Workflow
1. Écrire la spec dans `docs/specs/FEAT-XXX-short-name.md` (contexte,
   comportement, spec technique, impact sur l'existant)
2. Analyser l'impact sur le code existant (Plan Mode) : risques, conflits,
   lacunes de la spec
3. Décomposer en tâches dans `docs/tasks/TASKS.md`
4. Implémenter
5. Mettre à jour toute la documentation :
   - `docs/specs/FEAT-XXX-*.md` → statut `DONE`, implémentation décrite
   - **`docs/specs/option_scanner_spec.md` → OBLIGATOIRE** : intégrer le nouveau comportement,
     incrémenter la version
   - `docs/tasks/TASKS.md` → cocher `[x]` toutes les étapes terminées
6. **Lancer l'application** : `streamlit run ui/app.py`
7. **Demander à l'utilisateur de tester et attendre sa confirmation explicite**
   — NE PAS committer avant que l'utilisateur confirme que c'est OK
8. Une fois confirmé : committer TOUS les fichiers modifiés en un seul commit
   (code + docs + TASKS.md) : `"FEAT-XXX: description courte"`
9. Mettre à jour CLAUDE.md si des règles d'architecture ont changé

## Création de skills personnalisés

Les skills Claude Code de Val suivent ces conventions :

- **Nom** : toujours préfixé `vb-` (ex: `vb-init`, `vb-release`) pour éviter les conflits avec les skills officiels
- **Structure** : un dossier par skill dans `~/.claude/skills/`, contenant un fichier `SKILL.md`
  ```
  ~/.claude/skills/vb-monSkill/SKILL.md   ✅
  ~/.claude/skills/vb-monSkill.md         ❌ (fichier plat non détecté)
  ```
- **Invocation** : `/vb-monSkill`
