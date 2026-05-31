# DATABASE-REVERSE-AI — Detailed Project Documentation

## 1) Project Overview
DATABASE-REVERSE-AI is an AI-assisted reverse-engineering platform that analyzes an existing software system (application code + SQL/database assets) and reconstructs enterprise data architecture artifacts.

It combines:
- **Deterministic extraction and analysis** (always available)
- **Optional AI synthesis** (for narrative architecture reports)

Primary goal: produce review-ready architecture, schema, lineage, governance, DDD, and validation outputs for legacy/modernization assessments.

---

## 2) What the System Produces
After a run, the project generates artifacts in:
- `<project-root>/REVIEW` (human-facing reports)
- `<project-root>/memory/extracted` (structured extraction data)
- `<project-root>/memory/m3` (analysis intermediate outputs)

Typical outputs include:
- `inventory.json` (project inventory and stack signals)
- `entities.json`, `relationships.json`, `apis.json`
- `semantic_model.json` (Universal Semantic Model)
- `enterprise_graph.json`, `dependency_graph.json`
- Governance, lineage, redundancy, and validation JSON files
- Final Markdown reports like `DATA_ARCHITECTURE.md`, `SCHEMA_CATALOG.md`, `DATA_LINEAGE.md`, `GOVERNANCE_REPORT.md`, `DDD_ANALYSIS.md`, `VALIDATION_REPORT.md`, etc.
- `run_summary.json` and `architecture-summary.json`

---

## 3) Supported Technology Ecosystem
The tool supports multi-language, multi-framework extraction through adapters and a normalized model layer.

### Languages
- C# / .NET
- Java
- Python
- JavaScript / TypeScript
- SQL-first/database-first inputs

### ORM / Data Technologies (detected and normalized)
- EF Core
- Spring JPA / Hibernate
- Django ORM / SQLAlchemy
- Sequelize / Mongoose / TypeORM / Prisma
- SQL Server / PostgreSQL / MySQL / SQLite

### API Styles (detected)
- ASP.NET Core style APIs
- Spring MVC
- Django/FastAPI/Flask
- Express routes
- Minimal API style patterns

---

## 4) Core Architecture

### 4.1 High-Level Modules
- `main.py` — End-to-end orchestrator for the full pipeline
- `discovery.py` — standalone inventory/stack scanner
- `adapters/` — technology-specific extractors implementing a shared adapter interface
- `models/` — Universal Semantic Model (USM) dataclasses + tech context
- `parsers/` — deterministic code parsers (entities, APIs, relationships)
- `engine/` — universal relationship, governance, lineage, SQL analysis engines
- `scripts/` — graph generation, DDD analysis, redundancy checks, validation, report helpers
- `agents/` — M3 AI architecture agent for narrative report generation
- `skills/` + `prompts/` — AI prompt/skill assets used by the agent

### 4.2 Canonical Internal Data Contract (USM)
The project normalizes extraction output into the Universal Semantic Model, including:
- `UniversalEntity`
- `UniversalField`
- `UniversalRelationship`
- `UniversalEndpoint`
- `UniversalRepository`
- `UniversalHandler`
- `UniversalGovernanceFinding`

This model enables technology-agnostic downstream analysis.

---

## 5) End-to-End Pipeline (main.py)
`main.py` orchestrates the workflow in this order:

1. **Discovery** → scans files and stack hints, writes `REVIEW/inventory.json`
2. **Technology detection + layout detection**
3. **Universal adapter extraction** for non-.NET paths (when applicable)
4. **Entity + API extraction (parallel)**
5. **Semantic type resolution**
6. **Relationship detection**
7. **Universal relationship enrichment** (cross-domain/inverse improvements)
8. **Enterprise knowledge graph generation**
9. **Governance detection** + universal governance engine
10. **Lineage analysis**
11. **DDD analysis**
12. **SQL-first analysis block** (relationships, lineage, governance, SQL reports)
13. **Roslyn semantic enhancement** (C# enrichment)
14. **Redundancy analysis**
15. **Validation engine**
16. **AI report generation (optional)** via M3 agent
17. **Run summary generation** (`REVIEW/run_summary.json`)

### Deterministic vs AI path
- If `--skip-ai` is used, deterministic reports are still generated.
- AI report generation is additive and optional.

---

## 6) Discovery Scanner (discovery.py)
`discovery.py` is focused on quick repository intelligence:
- Language identification by extension
- Framework detection by package/content signals
- Database signal detection via package/config and SQL dialect hints
- Architecture pattern scoring (e.g., clean architecture, DDD, CQRS, layered)
- Semantic file-map classification

Output: `REVIEW/inventory.json`

Use this when you need inventory/stack metadata without full extraction/analysis.

---

## 7) Adapter Layer
Adapters implement `BaseAdapter` and return only USM outputs.

Registered default adapters include:
- `.NET EF Core`
- `Java Spring`
- `Python Django`
- `Node.js Express`
- `SQL Server`
- `PostgreSQL`
- `MySQL`
- `SQLite`

`AdapterRegistry` performs:
1. tech context detection,
2. adapter selection (`can_handle`),
3. extraction merge into one `SemanticModel`.

---

## 8) Analysis Engines
Key analysis capabilities:
- **RelationshipEngine**: enriches and improves relationship graph quality
- **GovernanceEngine**: cross-technology governance rule checks
- **LineageEngine / SQLLineageEngine**: lineage and transformation chain extraction
- **SQLRelationshipEngine**: SQL FK/relationship extraction
- **SQLGovernanceEngine**: SQL-centric risk and quality findings
- **SQLKnowledgeGraph**: SQL-aware graph generation

Additional script analyzers:
- `DDDAnalyzer`
- `RedundancyAnalyzer`
- `ValidationEngine`
- `EnterpriseKnowledgeGraph`

---

## 9) AI Report Layer (agents/m3_agent.py)
The M3 Data Architect agent:
1. loads extracted/analysis context JSON,
2. creates domain summaries,
3. invokes Claude in chunked mode,
4. merges AI sections,
5. writes consolidated report set into `REVIEW/`.

If AI context is unavailable or skipped, deterministic output remains available.

---

## 10) Execution & CLI Usage

### 10.1 Setup
```bash
cd DATABASE-REVERSE-AI
pip install -r requirements.txt
cp .env.example .env
```

Set `ANTHROPIC_API_KEY` in `.env` only if you want AI-generated report sections.
(`DATABASE-REVERSE-AI` above means your local cloned repository directory.)

### 10.2 Main pipeline
```bash
python main.py --project /absolute/path/to/target/project
```

### 10.3 Useful flags
```bash
python main.py --project /absolute/path --skip-ai
python main.py --project /absolute/path --only-extract
python main.py --project /absolute/path --no-cache
python main.py --project /absolute/path --cache-dir /absolute/path/to/cache
```

### 10.4 Discovery-only mode
```bash
python discovery.py --project /absolute/path/to/target/project
```

Path note:
- On Linux/macOS: `/absolute/path/to/project`
- On Windows: `C:\\absolute\\path\\to\\project`

---

## 11) Output Interpretation (Practical Reading Order)
For stakeholder submission, recommended reading order:
1. `REVIEW/run_summary.json` (quick metrics snapshot)
2. `REVIEW/inventory.json` (detected stack + architecture hints)
3. `REVIEW/DATA_ARCHITECTURE.md`
4. `REVIEW/SCHEMA_CATALOG.md`
5. `REVIEW/ENTITY_RELATIONSHIPS.md`
6. `REVIEW/DATA_LINEAGE.md` and `REVIEW/SQL_LINEAGE_REPORT.md`
7. `REVIEW/GOVERNANCE_REPORT.md` + `REVIEW/LEGACY_GOVERNANCE_REPORT.md`
8. `REVIEW/DDD_ANALYSIS.md`
9. `REVIEW/REDUNDANCY_ANALYSIS.md`
10. `REVIEW/VALIDATION_REPORT.md`

---

## 12) Operational Notes
- Tree-sitter availability improves parser confidence and fidelity.
- AI report generation requires model/API availability, but deterministic analysis does not.
- The system is designed to be resilient: many pipeline phases catch errors and continue best-effort.
- For SQL-heavy systems, SQL-first analysis block can enrich results even when code-side entity extraction is sparse.

---

## 13) Strengths for Enterprise Review Submissions
This repository is strong for external technical review because it provides:
- Multi-stack technology coverage
- Deterministic + explainable extraction layers
- Unified semantic model for cross-language analysis
- Governance, lineage, DDD, redundancy, and validation perspectives in one run
- Auditor-friendly artifact outputs in Markdown + JSON
