# Enterprise Data Architecture Agent

AI-powered system that analyzes legacy enterprise applications and generates
enterprise-grade Data Architecture artifacts automatically.

## Architecture

```
Legacy Application
       ↓
  Parser Layer (Tree-sitter AST)
       ↓
  Structured JSON Extraction
       ↓
  Relationship Mapping
       ↓
  M3 Data Architecture Agent (Claude AI)
       ↓
  Validation Engine
       ↓
  Enterprise Architecture Outputs
```

## Project Structure

```
enterprise-data-architect/
├── parsers/
│   ├── ast_parser.py          # Tree-sitter C# AST parser
│   ├── entity_extractor.py    # Domain entity detection
│   ├── api_extractor.py       # API endpoint extraction
│   └── relationship_detector.py # Relationship graph builder
├── agents/
│   └── m3_agent.py            # M3 Claude-powered architect agent
├── scripts/
│   ├── lineage_analyzer.py    # Data lineage tracing
│   ├── governance_detector.py # Governance rule detection
│   ├── redundancy_analyzer.py # Redundancy analysis
│   └── validation_engine.py   # Cross-validation engine
├── skills/
│   └── m3-data-architect.md   # AI agent skill definition
├── prompts/
│   └── m3_architect_prompt.md # Claude prompt template
├── memory/
│   ├── extracted/             # Parser outputs (JSON)
│   └── m3/                    # Architecture artifacts
├── outputs/                   # Additional exports
├── main.py                    # Orchestrator + CLI
└── requirements.txt
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set API key

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

Or set directly:
```bash
set ANTHROPIC_API_KEY=your_key_here   # Windows
export ANTHROPIC_API_KEY=your_key_here  # Mac/Linux
```

### 3. Run against eShopOnWeb

```bash
python main.py --project ../eShopOnWeb-main
```

### 4. Run without AI (deterministic only)

```bash
python main.py --project ../eShopOnWeb-main --skip-ai
```

### 5. Parse only

```bash
python main.py --project ../eShopOnWeb-main --only-parse
```

## Outputs

All artifacts are written to `memory/m3/`:

| File | Format | Contents |
|------|--------|----------|
| `schema-catalog.json` | JSON | Complete entity + field catalog with domains |
| `erd-summary.md` | Markdown | Entity relationship diagram narrative |
| `governance-report.md` | Markdown | Data validation, constraints, PII/PCI findings |
| `lineage-report.md` | Markdown | Data flow: API → Service → Repository → DB |
| `canonical-model.md` | Markdown | Canonical entity definitions per domain |
| `integration-map.json` | JSON | Cross-domain integration points |
| `redundancy-analysis.md` | Markdown | Duplicate entities, fields, logic |
| `validation-report.md` | Markdown | Cross-validation issues + inconsistencies |
| `run-summary.json` | JSON | Run statistics and output index |

## Rules

- **Never hallucinate** relationships — every finding cites `source_file`
- **Confidence levels:** `confirmed` | `inferred` | `low`
- **Deterministic extraction first** — AI reasoning on top
- Uncertain findings are flagged, not silently included
