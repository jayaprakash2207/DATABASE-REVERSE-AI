"""
Enterprise Data Architecture Agent — Main Orchestrator

Supports: .NET/EF Core, Java Spring, Python Django/FastAPI/Flask,
          Node.js Express/Sequelize/Mongoose/TypeORM/Prisma

Execution flow:
  0. Technology detection    -> TechContext (language + framework detection)
  1. Discovery               -> REVIEW/inventory.json
  2+3. Entity + API extract  -> memory/extracted/entities.json [parallel]
  4. Semantic type resolution
  5. Relationship detection  -> memory/extracted/relationships.json
  5b. Universal enrichment   -> RelationshipEngine cross-domain marking
  6. Knowledge graph         -> memory/extracted/enterprise_graph.json
  7. Governance detection    -> memory/m3/governance_findings.json
  7b. Universal governance   -> GovernanceEngine (tech-agnostic rules)
  8. Lineage analysis        -> memory/m3/lineage_analysis.json
  8b. DDD analysis           -> REVIEW/DDD_ANALYSIS.md
  8c. Roslyn enhancement     -> symbol resolution (C# only)
  9. Redundancy analysis     -> memory/m3/redundancy_analysis.json
  10. Validation             -> memory/m3/validation_results.json
  11. M3 Agent (claude)      -> REVIEW/*.md + REVIEW/architecture-summary.json

Usage:
  python main.py --project ../eShopOnWeb-main
  python main.py --project ../eShopOnWeb-main --skip-ai
  python main.py --project ../eShopOnWeb-main --only-extract
  python main.py --project ../eShopOnWeb-main --no-cache
"""

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from discovery import ProjectScanner
from project_layout import detect_layout, ProjectLayout
from core.type_resolver import TypeResolver
from parsers.entity_extractor import EntityExtractor
from parsers.relationship_detector import RelationshipDetector
from parsers.api_extractor import APIExtractor
from scripts.governance_detector import GovernanceDetector
from scripts.lineage_analyzer import LineageAnalyzer
from scripts.redundancy_analyzer import RedundancyAnalyzer
from scripts.validation_engine import ValidationEngine
from scripts.knowledge_graph import EnterpriseKnowledgeGraph
from scripts.ddd_analyzer import DDDAnalyzer
from core.roslyn_enhancer import RoslynEnhancer
from agents.m3_agent import M3DataArchitectAgent

# Universal adapter layer (multi-language support)
from adapters.registry import AdapterRegistry, detect_tech_context
from engine.technology_detector import TechnologyDetector
from engine.relationship_engine import RelationshipEngine
from engine.governance_engine import GovernanceEngine
from engine.lineage_engine import LineageEngine
from engine.sql_relationship_engine import SQLRelationshipEngine
from engine.sql_knowledge_graph import SQLKnowledgeGraph
from models.semantic_model import SemanticModel
from models.universal import Language

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

EXTRACTED_DIR = ROOT / "memory" / "extracted"
M3_DIR        = ROOT / "memory" / "m3"
REVIEW_DIR    = ROOT / "REVIEW"
CACHE_DIR     = ROOT / "memory" / "cache"


# ---------------------------------------------------------------------------
# Step context manager
# ---------------------------------------------------------------------------

def _step(name: str):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        log.info("=" * 60)
        log.info(f"  STEP: {name}")
        log.info("=" * 60)
        t = time.perf_counter()
        try:
            yield
        except Exception as exc:
            log.error(f"  FAILED: {name} -- {exc}")
            raise
        finally:
            log.info(f"  Done in {time.perf_counter() - t:.2f}s")

    return _ctx()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self, project_path: str, skip_ai: bool = False,
                 only_extract: bool = False, no_cache: bool = False,
                 cache_dir: Optional[str] = None):
        self.project      = Path(project_path).resolve()
        self.skip_ai      = skip_ai
        self.only_extract = only_extract
        self.no_cache     = no_cache
        self.cache_dir    = Path(cache_dir) if cache_dir else CACHE_DIR

        for d in (EXTRACTED_DIR, M3_DIR, REVIEW_DIR):
            d.mkdir(parents=True, exist_ok=True)
        if not no_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        t_total = time.perf_counter()
        log.info("")
        log.info("  Enterprise Data Architecture Agent  [HIGH-FIDELITY AST MODE]")
        log.info(f"  Project  : {self.project}")
        log.info(f"  Skip AI  : {self.skip_ai}")
        log.info(f"  Cache    : {'disabled' if self.no_cache else self.cache_dir}")
        self._log_parser_mode()
        log.info("")

        cache_str = None if self.no_cache else str(self.cache_dir)

        # ----------------------------------------------------------
        # 1. Discovery
        # ----------------------------------------------------------
        inventory: dict = {}
        with _step("1 / Discovery"):
            try:
                scanner   = ProjectScanner(str(self.project))
                inventory = scanner.scan()
                (REVIEW_DIR / "inventory.json").write_text(
                    json.dumps(inventory, indent=2), encoding="utf-8")
                stack = inventory.get("stack", {})
                log.info(f"  Files : {inventory.get('total_files', 0)}")
                log.info(f"  Langs : {[l['name'] for l in stack.get('languages',[]) if l.get('primary')]}")
                log.info(f"  DBs   : {[d['name'] for d in stack.get('databases',[])]}")
                log.info(f"  Arch  : {[a['pattern'] for a in stack.get('architecture_type',[])[:3]]}")
            except Exception as exc:
                log.warning(f"  Discovery failed: {exc}. Continuing.")

        # ----------------------------------------------------------
        # 0. Universal technology detection
        # ----------------------------------------------------------
        tech_detector = TechnologyDetector()
        self._tech_context = tech_detector.detect(self.project)
        log.info(f"  TechCtx  : langs={[l.value for l in self._tech_context.languages]} "
                 f"orms={[o.value for o in self._tech_context.orms]} "
                 f"arch={self._tech_context.architecture}")

        # Detect primary language — controls which extraction path runs
        self._primary_lang = self._tech_context.primary_language()
        is_dotnet = (self._primary_lang == Language.CSHARP or
                     not self._tech_context.languages)

        # ----------------------------------------------------------
        # Layout detection — finds layers for ANY .NET project
        # ----------------------------------------------------------
        layout: ProjectLayout = detect_layout(self.project)
        self._layout = layout
        log.info(f"  Layout: domain={len(layout.domain_dirs)}  "
                 f"infra={len(layout.infra_dirs)}  "
                 f"api={len(layout.api_dirs)}  "
                 f"web={len(layout.web_dirs)}")
        for li in layout.layers:
            log.info(f"    [{li.layer_type:14s} {li.confidence:.2f}] {li.project_name}")

        entity_scan_dirs = layout.domain_dirs or [self.project]
        infra_dir        = layout.primary_infra_dir
        api_scan_dirs    = layout.api_dirs
        web_scan_dirs    = layout.web_dirs

        entities_data: dict = {}
        apis_data:     dict = {}

        # ----------------------------------------------------------
        # 1b. Universal adapter extraction (non-.NET languages)
        # ----------------------------------------------------------
        self._semantic_model: Optional[SemanticModel] = None
        if not is_dotnet:
            with _step("1b / Universal Adapter Extraction"):
                try:
                    registry = AdapterRegistry(extracted_dir=EXTRACTED_DIR)
                    sem_model = registry.run(str(self.project))
                    self._semantic_model = sem_model
                    # Bridge into legacy dicts for downstream steps
                    entities_data  = sem_model.to_legacy_entities_dict()
                    apis_data      = sem_model.to_legacy_apis_dict()
                    entities_data["entity_count"]       = len(sem_model.entities)
                    entities_data["value_object_count"] = sum(
                        1 for e in sem_model.entities if e.kind.value == "value_object"
                    )
                    apis_data["endpoint_count"] = len(sem_model.endpoints)
                    apis_data["handler_count"]  = len(sem_model.handlers)
                    apis_data["dto_count"]      = 0
                    # Save SemanticModel
                    sem_model.save(EXTRACTED_DIR / "semantic_model.json")
                    log.info(f"  Entities  : {entities_data['entity_count']}")
                    log.info(f"  Endpoints : {apis_data['endpoint_count']}")
                    log.info(f"  Relations : {len(sem_model.relationships)}")
                    if sem_model.extraction_warnings:
                        for w in sem_model.extraction_warnings[:3]:
                            log.warning(f"  WARN: {w}")
                except Exception as exc:
                    log.warning(f"  Universal adapter failed: {exc}. Falling back to .NET path.")

        # ----------------------------------------------------------
        # 2 + 3 (parallel): Entity extraction + API extraction
        # ----------------------------------------------------------
        with _step("2+3 / Entity + API Extraction (parallel)"):
            def _extract_entities():
                merged: dict = {
                    "entities": [], "value_objects": [],
                    "entity_count": 0, "value_object_count": 0,
                    "enums": [],
                }
                seen_entities: set[str] = set()
                for d in entity_scan_dirs:
                    if not d.exists():
                        log.warning(f"  Domain dir not found: {d}")
                        continue
                    log.info(f"  Scanning entities: {d}")
                    result = EntityExtractor(
                        output_dir=str(EXTRACTED_DIR),
                        cache_dir=cache_str or str(EXTRACTED_DIR / "_nc"),
                    ).extract_from_dir(str(d))
                    for ent in result.get("entities", []):
                        if ent["entity"] not in seen_entities:
                            merged["entities"].append(ent)
                            seen_entities.add(ent["entity"])
                    for vo in result.get("value_objects", []):
                        if vo["entity"] not in seen_entities:
                            merged["value_objects"].append(vo)
                            seen_entities.add(vo["entity"])
                    merged["enums"].extend(result.get("enums", []))
                merged["entity_count"]       = len(merged["entities"])
                merged["value_object_count"] = len(merged["value_objects"])
                return merged

            def _extract_apis():
                _EMPTY: dict = {
                    "endpoints": [], "endpoint_count": 0,
                    "mediatr_handlers": [], "handler_count": 0,
                    "dtos": [], "dto_count": 0,
                    "page_handlers": [],
                    "mapper_profiles": [],
                    "models": {}, "model_count": 0,
                    "groups": [], "group_count": 0,
                }
                result: dict = dict(_EMPTY)

                all_dirs = api_scan_dirs + web_scan_dirs
                if not all_dirs:
                    # fallback: scan whole project
                    all_dirs = [self.project]

                for d in all_dirs:
                    if not d.exists():
                        continue
                    log.info(f"  Scanning APIs: {d}")
                    try:
                        r = APIExtractor(
                            output_dir=str(EXTRACTED_DIR),
                            cache_dir=cache_str or str(EXTRACTED_DIR / "_nc"),
                        ).extract_from_dir(str(d))
                        for key in ("endpoints", "mediatr_handlers", "page_handlers",
                                    "mediatr_requests", "dtos", "mapper_profiles"):
                            result.setdefault(key, []).extend(r.get(key, []))
                        result["endpoint_count"] = len(result.get("endpoints", []))
                        result["handler_count"]  = len(result.get("mediatr_handlers", []))
                        result["dto_count"]      = len(result.get("dtos", []))
                    except Exception as exc:
                        log.warning(f"  API scan failed ({d.name}): {exc}")

                return result

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_ent = pool.submit(_extract_entities)
                fut_api = pool.submit(_extract_apis)
                entities_data = fut_ent.result()
                apis_data     = fut_api.result()

            log.info(f"  Entities      : {entities_data.get('entity_count', 0)}")
            log.info(f"  Value Objects : {entities_data.get('value_object_count', 0)}")
            log.info(f"  Endpoints     : {apis_data.get('endpoint_count', 0)}")
            log.info(f"  MediatR Hdlrs : {apis_data.get('handler_count', 0)}")
            log.info(f"  DTOs          : {apis_data.get('dto_count', 0)}")
            log.info(f"  Page Handlers : {len(apis_data.get('page_handlers', []))}")

        # ----------------------------------------------------------
        # 4. Semantic type resolution
        # ----------------------------------------------------------
        type_resolver = TypeResolver(output_dir=str(EXTRACTED_DIR))
        with _step("4 / Semantic Type Resolution"):
            type_resolver.build(entities_data)
            tr_result = type_resolver.export()
            log.info(f"  Aliases       : {tr_result.get('alias_count', 0)}")
            log.info(f"  Unresolved    : {tr_result.get('unresolved_count', 0)}")

        # ----------------------------------------------------------
        # 5. Relationship detection
        # ----------------------------------------------------------
        relationships_data: dict = {}
        with _step("5 / Relationship Detection"):
            if entities_data.get("entity_count", 0) == 0:
                log.warning("  No entities — skipping")
            else:
                detector = RelationshipDetector(
                    output_dir=str(EXTRACTED_DIR),
                    infra_dir=str(infra_dir) if infra_dir and infra_dir.exists() else None,
                )
                relationships_data = detector.detect(entities_data, type_resolver)
                log.info(f"  Relationships : {relationships_data.get('relationship_count', 0)}")
                xd = sum(1 for r in relationships_data.get("relationships", [])
                         if r.get("cross_domain"))
                log.info(f"  Cross-domain  : {xd}")

        # ----------------------------------------------------------
        # 5b. Universal relationship enrichment (FK naming + inverse rels)
        # ----------------------------------------------------------
        if self._semantic_model is not None:
            with _step("5b / Universal Relationship Enrichment"):
                try:
                    rel_engine = RelationshipEngine()
                    # Bridge relationships into SemanticModel first
                    from adapters.dotnet.ef_adapter import _parse_relationship_kind, _parse_confidence
                    from models.universal import UniversalRelationship, Technology as Tech
                    for raw_r in relationships_data.get("relationships", []):
                        ur = UniversalRelationship(
                            source      = raw_r.get("source", ""),
                            target      = raw_r.get("target", ""),
                            kind        = _parse_relationship_kind(raw_r.get("relationship", "one_to_many")),
                            via         = raw_r.get("via", ""),
                            technology  = self._semantic_model.entities[0].technology
                                         if self._semantic_model.entities else Tech.EF_CORE,
                            source_file = raw_r.get("source_file", ""),
                            evidence    = raw_r.get("evidence", ""),
                        )
                        self._semantic_model.relationships.append(ur)
                    rel_engine.enrich(self._semantic_model)
                    log.info(f"  Total rels after enrichment: {len(self._semantic_model.relationships)}")
                except Exception as exc:
                    log.warning(f"  Relationship enrichment failed: {exc}")

        if self.only_extract:
            log.info("--only-extract flag set. Stopping.")
            self._write_run_summary(inventory, entities_data, relationships_data,
                                    apis_data, {}, {}, {}, {}, tr_result)
            return

        # ----------------------------------------------------------
        # 6. Enterprise Knowledge Graph
        # ----------------------------------------------------------
        graph_data: dict = {}
        with _step("6 / Enterprise Knowledge Graph"):
            kg = EnterpriseKnowledgeGraph(output_dir=str(EXTRACTED_DIR))
            # Build with empty lineage/governance first; will enrich after
            kg.build(entities_data, relationships_data, apis_data, {}, {})
            graph_data = kg.export()
            log.info(f"  Nodes         : {graph_data.get('node_count', 0)}")
            log.info(f"  Edges         : {graph_data.get('edge_count', 0)}")
            log.info(f"  Orphans       : {len(graph_data.get('orphan_entities', []))}")

        # AST shim for legacy analyzers
        ast_shim = self._build_ast_shim(entities_data, apis_data)

        # ----------------------------------------------------------
        # 7. Governance detection
        # ----------------------------------------------------------
        governance_data: dict = {}
        with _step("7 / Governance Detection"):
            gov = GovernanceDetector(output_dir=str(M3_DIR))
            governance_data = gov.detect(
                entities_data, ast_shim,
                api_data=apis_data,
                infra_dir=str(infra_dir) if infra_dir and infra_dir.exists() else None,
            )
            log.info(f"  Findings      : {governance_data.get('finding_count', 0)}")
            log.info(f"  GDPR fields   : {len(governance_data.get('gdpr_fields', []))}")
            log.info(f"  PCI fields    : {len(governance_data.get('pci_fields', []))}")

        # ----------------------------------------------------------
        # 7b. Universal governance engine
        # ----------------------------------------------------------
        if self._semantic_model is not None:
            with _step("7b / Universal Governance Analysis"):
                try:
                    gov_engine = GovernanceEngine()
                    gov_engine.analyze(self._semantic_model)
                    # Merge USM findings into governance_data for reporting
                    usm_findings = [f.to_dict() for f in self._semantic_model.findings]
                    governance_data.setdefault("usm_findings", []).extend(usm_findings)
                    log.info(f"  USM findings: {len(usm_findings)}")
                    crit = sum(1 for f in usm_findings if f.get("severity") == "CRITICAL")
                    log.info(f"  Critical    : {crit}")
                except Exception as exc:
                    log.warning(f"  Universal governance failed: {exc}")

        # ----------------------------------------------------------
        # 8. Lineage analysis
        # ----------------------------------------------------------
        lineage_data: dict = {}
        with _step("8 / Lineage Analysis"):
            lin = LineageAnalyzer(output_dir=str(M3_DIR))
            lineage_data = lin.analyze(entities_data, apis_data, relationships_data, ast_shim)
            log.info(f"  Flows         : {lineage_data.get('flow_count', 0)}")
            log.info(f"  Complete      : {lineage_data.get('complete_flows', 0)}")
            log.info(f"  Gaps          : {lineage_data.get('gap_flows', 0)}")

        # ----------------------------------------------------------
        # 8b. DDD analysis (new step)
        # ----------------------------------------------------------
        ddd_data: dict = {}
        with _step("8b / DDD Analysis"):
            try:
                ddd = DDDAnalyzer(
                    output_dir=str(REVIEW_DIR),
                    extracted_dir=str(EXTRACTED_DIR),
                )
                ddd_data = ddd.analyze(entities_data, relationships_data, apis_data)
                log.info(f"  Health score  : {ddd_data.get('ddd_health_score', 0)}/100")
                log.info(f"  Agg roots     : {len(ddd_data.get('aggregate_roots', []))}")
                log.info(f"  Value objects : {len(ddd_data.get('value_objects', []))}")
            except Exception as exc:
                log.warning(f"  DDD analysis failed: {exc}")

        # ----------------------------------------------------------
        # 8c. SQL Analysis (database-first: lineage + governance + reports)
        # ----------------------------------------------------------
        sql_analysis: dict = {}
        with _step("8c / SQL Analysis (Database-First)"):
            has_sql_files = bool(list(self.project.rglob("*.sql"))[:1])
            has_db_files  = bool(list(self.project.rglob("*.db"))[:1] or
                                  list(self.project.rglob("*.sqlite"))[:1])
            # Also run if semantic model has stored procs or views
            has_sql_content = (
                self._semantic_model is not None
                and (len(self._semantic_model.handlers) > 0 or
                     any(ep.handler_class == "SQL View"
                         for ep in (self._semantic_model.endpoints or [])))
            )

            if has_sql_files or has_db_files or has_sql_content:
                try:
                    # If semantic model is empty (pure .NET path), build one from SQL
                    if self._semantic_model is None and (has_sql_files or has_db_files):
                        from adapters.sqlserver.sql_adapter import SQLServerAdapter
                        sql_adp = SQLServerAdapter(output_dir=str(EXTRACTED_DIR))
                        self._semantic_model = sql_adp.extract(self._tech_context)
                        # Add live SQLite schemas only if .db files are present
                        if has_db_files:
                            from adapters.sqlite.sqlite_adapter import SQLiteAdapter
                            self._semantic_model.merge(SQLiteAdapter().extract(self._tech_context))
                        self._semantic_model.save(EXTRACTED_DIR / "semantic_model.json")

                    if self._semantic_model:
                        from engine.sql_lineage_engine import SQLLineageEngine
                        from engine.sql_governance_engine import SQLGovernanceEngine
                        from scripts.sql_report_generator import SQLReportGenerator

                        # P1 — SQL Relationship Engine
                        # → relationships.json (USM) + sql_relationships.json (SQL-specific)
                        try:
                            rel_result = SQLRelationshipEngine().save(
                                self._semantic_model, EXTRACTED_DIR
                            )
                            sql_analysis["relationship_count"] = rel_result.get("relationship_count", 0)
                            sql_analysis["fk_count"] = rel_result.get("fk_constraint_count", 0)
                            log.info(f"  SQL relationships : {sql_analysis['relationship_count']} "
                                     f"({sql_analysis['fk_count']} FK constraints)")
                        except Exception as _re:
                            log.warning(f"  SQLRelationshipEngine: {_re}")

                        # P3 — SQL Lineage Engine
                        # → sql_lineage.json + SQL_LINEAGE_REPORT.md
                        sql_lin_result = SQLLineageEngine().save(
                            self._semantic_model, EXTRACTED_DIR
                        )
                        sql_analysis["edge_count"]  = sql_lin_result.get("edge_count", 0)
                        sql_analysis["chain_count"] = sql_lin_result.get("chain_count", 0)
                        log.info(f"  SQL edges     : {sql_analysis['edge_count']}")
                        log.info(f"  SQL chains    : {sql_analysis['chain_count']}")

                        # P4 — SQL Knowledge Graph
                        # → enterprise_graph.json
                        try:
                            graph_result = SQLKnowledgeGraph().save(
                                self._semantic_model, EXTRACTED_DIR
                            )
                            sql_analysis["graph_nodes"] = graph_result.get("node_count", 0)
                            sql_analysis["graph_edges"] = graph_result.get("edge_count", 0)
                            log.info(f"  Graph nodes   : {sql_analysis['graph_nodes']}")
                            log.info(f"  Graph edges   : {sql_analysis['graph_edges']}")
                        except Exception as _ge:
                            log.warning(f"  SQLKnowledgeGraph: {_ge}")

                        # P5+P6 — SQL Governance Engine
                        # → sql_governance.json + LEGACY_GOVERNANCE_REPORT.md + DATA_QUALITY_REPORT.md
                        sql_findings = SQLGovernanceEngine().save(
                            self._semantic_model, EXTRACTED_DIR, REVIEW_DIR
                        )
                        sql_analysis["governance_findings"] = len(sql_findings)
                        log.info(f"  SQL findings  : {len(sql_findings)}")

                        # SQL Report Generator → SQL_LINEAGE_REPORT.md + STORED_PROCEDURE_ANALYSIS.md
                        reporter = SQLReportGenerator(
                            output_dir=str(REVIEW_DIR),
                            extracted_dir=str(EXTRACTED_DIR),
                        )
                        generated = reporter.generate_all()
                        sql_analysis["reports"] = list(generated.keys())
                        for rname, rpath in generated.items():
                            log.info(f"  {rname:35s} -> {Path(rpath).name}")
                except Exception as exc:
                    log.warning(f"  SQL analysis failed: {exc}")
                    import traceback; log.warning(traceback.format_exc())
            else:
                log.info("  Skipped (no SQL files / database-first indicators found)")

        # ----------------------------------------------------------
        # 8d. Roslyn semantic enhancement
        # ----------------------------------------------------------
        roslyn_summary: dict = {}
        with _step("8d / Roslyn Semantic Enhancement"):
            try:
                roslyn = RoslynEnhancer()
                roslyn.load_project(self.project)
                roslyn_summary = roslyn.summary()
                log.info(f"  Types indexed : {roslyn_summary.get('types_indexed', 0)}")
                log.info(f"  With ifaces   : {roslyn_summary.get('with_interfaces', 0)}")
            except Exception as exc:
                log.warning(f"  Roslyn enhancement failed: {exc}")

        # ----------------------------------------------------------
        # 9. Redundancy analysis
        # ----------------------------------------------------------
        redundancy_data: dict = {}
        with _step("9 / Redundancy Analysis"):
            red = RedundancyAnalyzer(output_dir=str(M3_DIR))
            redundancy_data = red.analyze(entities_data, governance_data, ast_shim)
            log.info(f"  Findings      : {redundancy_data.get('finding_count', 0)}")
            log.info(f"  Actionable    : {len(redundancy_data.get('actionable', []))}")

        # ----------------------------------------------------------
        # 10. Validation
        # ----------------------------------------------------------
        validation_data: dict = {}
        with _step("10 / Validation"):
            val = ValidationEngine(output_dir=str(M3_DIR))
            validation_data = val.validate(
                entities_data, relationships_data, governance_data, lineage_data)
            sev = validation_data.get("severity_counts", {})
            log.info(f"  Issues        : {validation_data.get('issue_count', 0)}")
            log.info(f"  CRITICAL      : {sev.get('CRITICAL', 0)}")
            log.info(f"  WARNING       : {sev.get('WARNING', 0)}")
            log.info(f"  NOTE          : {sev.get('NOTE', 0)}")

        # ----------------------------------------------------------
        # 11. M3 Agent
        # ----------------------------------------------------------
        if not self.skip_ai:
            with _step("11 / M3 Agent (Claude Code)"):
                agent = M3DataArchitectAgent(
                    output_dir=str(REVIEW_DIR),
                    skills_dir=str(ROOT / "skills"),
                    extracted_dir=str(EXTRACTED_DIR),
                )
                reports = agent.run()
                for name, path in reports.items():
                    log.info(f"  {name:35s} -> {Path(path).name}")
        else:
            log.info("[11] M3 Agent skipped (--skip-ai)")
            self._write_deterministic_reports(
                entities_data, relationships_data, apis_data,
                governance_data, lineage_data)

        # ----------------------------------------------------------
        # Summary
        # ----------------------------------------------------------
        self._write_run_summary(
            inventory, entities_data, relationships_data, apis_data,
            governance_data, lineage_data, redundancy_data, validation_data,
            tr_result, ddd_data, roslyn_summary, sql_analysis,
        )

        elapsed = time.perf_counter() - t_total
        log.info("")
        log.info(f"  Total time : {elapsed:.1f}s")
        log.info(f"  REVIEW/    : {REVIEW_DIR}")
        log.info(f"  memory/    : {M3_DIR.parent}")
        log.info("")

    # ------------------------------------------------------------------
    # AST shim
    # ------------------------------------------------------------------

    def _build_ast_shim(self, entities_data: dict, apis_data: dict) -> dict:
        files = []
        seen:  dict[str, dict] = {}
        for entity in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            src = entity.get("source_file", "")
            if src not in seen:
                entry = {"file": src, "classes": [], "interfaces": []}
                files.append(entry); seen[src] = entry
            seen[src]["classes"].append({
                "name":       entity["entity"],
                "base_types": entity.get("base_types", []),
                "attributes": [a["name"] for a in entity.get("attributes", [])],
                "properties": [
                    {"name": f["name"], "type": f["type"],
                     "line": f.get("line_number"),
                     "attributes": entity.get("property_attributes", {}).get(f["name"], [])}
                    for f in entity.get("fields", [])
                ],
                "methods": [], "start_line": entity.get("line_number"),
            })
        return {
            "parsed_at":      datetime.now(timezone.utc).isoformat(),
            "source_project": str(self.project),
            "file_count":     len(files),
            "files":          files,
        }

    # ------------------------------------------------------------------
    # Deterministic reports
    # ------------------------------------------------------------------

    def _write_deterministic_reports(
        self, entities_data, relationships_data, apis_data,
        governance_data, lineage_data,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()

        lines = [f"# Data Architecture\n\n_Generated: {ts}_\n\n## Entities\n\n"]
        for e in entities_data.get("entities", []):
            conf = e.get("confidence", {}).get("level", "?")
            lines.append(f"- **{e['entity']}** `{e.get('aggregate','')}` "
                         f"fields={len(e.get('fields',[]))} conf={conf}\n")
        if entities_data.get("value_objects"):
            lines.append("\n## Value Objects\n\n")
            for v in entities_data.get("value_objects", []):
                lines.append(f"- **{v['entity']}** owned_by={v.get('owned_by','?')}\n")
        (REVIEW_DIR / "DATA_ARCHITECTURE.md").write_text("".join(lines), encoding="utf-8")

        lines = [f"# Schema Catalog\n\n_Generated: {ts}_\n\n"]
        lines.append("| Entity | Fields | FKs | Nav Collections | Confidence |\n")
        lines.append("|--------|--------|-----|-----------------|------------|\n")
        for e in entities_data.get("entities", []):
            fnames = ", ".join(f["name"] for f in e.get("fields", [])
                               if not f.get("is_navigation"))[:60]
            fks    = ", ".join(f["name"] for f in e.get("foreign_keys", []))
            navs   = ", ".join(n["target_entity"] for n in e.get("navigation_collection", []))
            conf   = e.get("confidence", {}).get("level", "?")
            lines.append(f"| {e['entity']} | {fnames} | {fks} | {navs} | {conf} |\n")
        (REVIEW_DIR / "SCHEMA_CATALOG.md").write_text("".join(lines), encoding="utf-8")

        sym_map = {"one_to_many":"||--o{","many_to_one":"}o--||",
                   "many_to_many":"}o--o{","embeds_value_object":"||--||",
                   "owns_many":"||--o{","one_to_one":"||--||","references":"..>"}
        lines = [f"# Entity Relationships\n\n_Generated: {ts}_\n\n```\n"]
        for r in relationships_data.get("relationships", []):
            arrow = sym_map.get(r["relationship"], "--")
            cd    = " [CROSS-DOMAIN]" if r.get("cross_domain") else ""
            lines.append(f"{r['source']} {arrow} {r['target']} "
                         f": \"{r.get('via','')}\" [{r.get('confidence','?')}]{cd}\n")
        lines.append("```\n")
        (REVIEW_DIR / "ENTITY_RELATIONSHIPS.md").write_text("".join(lines), encoding="utf-8")

        for src_name, dst_name in [
            ("lineage-report.md",     "DATA_LINEAGE.md"),
            ("governance-report.md",  "GOVERNANCE_REPORT.md"),
            ("redundancy-analysis.md","REDUNDANCY_ANALYSIS.md"),
            ("validation-report.md",  "VALIDATION_REPORT.md"),
        ]:
            src = M3_DIR / src_name
            dst = REVIEW_DIR / dst_name
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        log.info("[11] Deterministic reports written to REVIEW/")

    # ------------------------------------------------------------------
    # Run summary
    # ------------------------------------------------------------------

    def _write_run_summary(
        self, inventory, entities_data, relationships_data, apis_data,
        governance_data, lineage_data, redundancy_data, validation_data,
        type_resolution, ddd_data=None, roslyn_summary=None, sql_analysis=None,
    ) -> None:
        ddd_data        = ddd_data or {}
        roslyn_summary  = roslyn_summary or {}
        sql_analysis    = sql_analysis or {}
        conf_ent = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for e in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            lvl = e.get("confidence", {}).get("level", "LOW")
            conf_ent[lvl] = conf_ent.get(lvl, 0) + 1

        conf_rel = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for r in relationships_data.get("relationships", []):
            lvl = r.get("confidence", "LOW")
            conf_rel[lvl] = conf_rel.get(lvl, 0) + 1

        try:
            import tree_sitter_languages  # noqa
            ts_available = True
        except ImportError:
            ts_available = False

        cache_entries = 0
        if not self.no_cache and self.cache_dir.exists():
            cache_entries = len(list(self.cache_dir.glob("*.json")))

        summary = {
            "run_at":  datetime.now(timezone.utc).isoformat(),
            "project": str(self.project),
            "skip_ai": self.skip_ai,
            "tree_sitter_available": ts_available,
            "tech_context": (
                self._tech_context.to_dict()
                if hasattr(self, "_tech_context") else {}
            ),
            "project_layout": self._layout.summary() if hasattr(self, "_layout") else {},
            "extraction": {
                "files_scanned":    inventory.get("total_files", 0),
                "entities":         entities_data.get("entity_count", 0),
                "value_objects":    entities_data.get("value_object_count", 0),
                "relationships":    relationships_data.get("relationship_count", 0),
                "api_endpoints":    apis_data.get("endpoint_count", 0),
                "mediatr_handlers": apis_data.get("handler_count", 0),
                "dtos":             apis_data.get("dto_count", 0),
                "page_handlers":    len(apis_data.get("page_handlers", [])),
                "type_aliases":     type_resolution.get("alias_count", 0),
                "unresolved_types": type_resolution.get("unresolved_count", 0),
            },
            "analysis": {
                "governance_findings": governance_data.get("finding_count", 0),
                "gdpr_fields":         len(governance_data.get("gdpr_fields", [])),
                "pci_fields":          len(governance_data.get("pci_fields", [])),
                "lineage_flows":       lineage_data.get("flow_count", 0),
                "complete_flows":      lineage_data.get("complete_flows", 0),
                "gap_flows":           lineage_data.get("gap_flows", 0),
                "redundancy_findings": redundancy_data.get("finding_count", 0),
                "actionable_findings": len(redundancy_data.get("actionable", [])),
                "validation_issues":   validation_data.get("issue_count", 0),
                "validation_critical": validation_data.get("severity_counts", {}).get("CRITICAL", 0),
                "validation_warnings": validation_data.get("severity_counts", {}).get("WARNING", 0),
                "confirmed_findings":  validation_data.get("finding_type_counts", {}).get("CONFIRMED", 0),
                "inferred_findings":   validation_data.get("finding_type_counts", {}).get("INFERRED", 0),
                "recommended_findings": validation_data.get("finding_type_counts", {}).get("RECOMMENDED", 0),
            },
            "ddd": {
                "health_score":     ddd_data.get("ddd_health_score", 0),
                "aggregate_roots":  len(ddd_data.get("aggregate_roots", [])),
                "value_objects":    len(ddd_data.get("value_objects", [])),
                "bc_coupling":      len(ddd_data.get("bounded_context_coupling", [])),
                "acl_count":        len(ddd_data.get("anti_corruption_layers", [])),
            },
            "roslyn": roslyn_summary,
            "sql": {
                "edge_count":          sql_analysis.get("edge_count", 0),
                "chain_count":         sql_analysis.get("chain_count", 0),
                "governance_findings": sql_analysis.get("governance_findings", 0),
                "reports":             sql_analysis.get("reports", []),
            },
            "confidence_profile": {
                "entities":      conf_ent,
                "relationships": conf_rel,
            },
            "cache": {
                "cached_entries": cache_entries,
                "cache_dir":      str(self.cache_dir),
            },
            "outputs": {
                "extracted":        str(EXTRACTED_DIR),
                "enterprise_graph": str(EXTRACTED_DIR / "enterprise_graph.json"),
                "dependency_graph": str(EXTRACTED_DIR / "dependency_graph.json"),
                "type_resolution":  str(EXTRACTED_DIR / "type_resolution.json"),
                "analysis":         str(M3_DIR),
                "review":           str(REVIEW_DIR),
            },
        }
        path = REVIEW_DIR / "run_summary.json"
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info(f"  run_summary.json -> {path}")

    def _log_parser_mode(self) -> None:
        try:
            import tree_sitter_languages  # noqa
            log.info("  Parser   : tree-sitter C# AST [HIGH confidence]")
        except ImportError:
            log.info("  Parser   : regex fallback [MEDIUM confidence]")
            log.info("             pip install tree-sitter==0.21.3 tree-sitter-languages")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Enterprise Data Architecture Agent — AST edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --project ../eShopOnWeb-main
  python main.py --project ../eShopOnWeb-main --skip-ai
  python main.py --project ../eShopOnWeb-main --only-extract
  python main.py --project ../eShopOnWeb-main --no-cache
""")
    p.add_argument("--project",      required=True)
    p.add_argument("--skip-ai",      action="store_true")
    p.add_argument("--only-extract", action="store_true")
    p.add_argument("--no-cache",     action="store_true")
    p.add_argument("--cache-dir",    default=None)
    args = p.parse_args()

    project = Path(args.project)
    if not project.exists():
        log.error(f"Project path not found: {project}")
        sys.exit(1)

    Orchestrator(
        project_path=str(project),
        skip_ai=args.skip_ai,
        only_extract=args.only_extract,
        no_cache=args.no_cache,
        cache_dir=args.cache_dir,
    ).run()


if __name__ == "__main__":
    main()
