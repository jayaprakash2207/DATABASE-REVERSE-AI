# Database Reverse AI

AI-assisted reverse engineering tool that analyzes an existing codebase (and SQL assets) to reconstruct enterprise data architecture artifacts.

It combines deterministic extraction (entities, APIs, relationships, lineage, governance) with optional AI report generation.

## What it does

- Detects technology stack and architecture hints
- Extracts entities, value objects, API endpoints, handlers, and relationships
- Builds an enterprise knowledge graph
- Runs governance, lineage, redundancy, DDD, validation, and SQL-first analysis
- Produces review-ready architecture documents in `REVIEW/`

## Supported technologies

Through its adapter + Universal Semantic Model layer, the project supports:

- **.NET** (EF Core, ASP.NET Core)
- **Java** (Spring/JPA)
- **Python** (Django, SQLAlchemy, FastAPI, Flask)
- **Node.js** (Express, Sequelize, Mongoose, TypeORM, Prisma)
- **Database-first SQL** (SQL Server, PostgreSQL, MySQL, SQLite)

## High-level flow

Main pipeline (`main.py`) runs:

1. Discovery (`REVIEW/inventory.json`)
2. Technology + layout detection
3. Entity/API extraction (parallel)
4. Semantic type resolution
5. Relationship detection (+ universal relationship enrichment)
6. Enterprise knowledge graph generation
7. Governance analysis (+ universal governance)
8. Lineage, DDD, SQL-first analysis, Roslyn enhancement
9. Redundancy analysis
10. Validation
11. AI report generation (optional)

`main.py` already runs discovery as step 1; `discovery.py` is only for standalone discovery runs.

## Repository structure

```text
adapters/      # Language/database adapters and registry
agents/        # AI agent orchestration (M3 data architect)
core/          # Shared confidence/cache/type/roslyn utilities
engine/        # Universal relationship/lineage/governance/sql engines
models/        # Universal Semantic Model (USM)
parsers/       # Deterministic extractors (entity/api/relationship)
scripts/       # Analysis/report generation modules
discovery.py   # Project scanner and inventory generator
main.py        # End-to-end orchestrator CLI
REVIEW/        # Final markdown/json outputs
memory/        # Extracted and analysis intermediate artifacts
```

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`
- Optional: Anthropic API key (needed only for AI report step)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# set ANTHROPIC_API_KEY in .env if you want AI report generation
```

## Usage

### Show CLI help

```bash
python main.py --help
```

### Run full pipeline

```bash
python main.py --project ../my-project
```

`--project` accepts both absolute and relative paths (e.g., `../my-project`).

### Deterministic-only run (skip AI)

```bash
python main.py --project ../my-project --skip-ai
```

### Extraction-only run

```bash
python main.py --project ../my-project --only-extract
```

### Disable cache

```bash
python main.py --project ../my-project --no-cache
```

### Use custom cache directory

```bash
python main.py --project ../my-project --cache-dir /absolute/path/to/cache
```

## Output locations

- `REVIEW/` — final reports (architecture, schema, relationships, governance, lineage, DDD, validation, summary)
- `memory/extracted/` — extracted intermediate JSON (entities, APIs, relationships, semantic model, graphs)
- `memory/m3/` — analysis intermediate JSON (governance, lineage, redundancy, validation)

## Discovery-only mode

You can run stack discovery separately:

```bash
python discovery.py --project ../my-project
```

This writes `REVIEW/inventory.json`.

## Notes

- AI step depends on environment configuration and model access.
- If AI is skipped or unavailable, deterministic reports are still generated.
- Tree-sitter availability affects parser confidence mode.
