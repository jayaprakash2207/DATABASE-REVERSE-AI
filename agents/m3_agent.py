"""
M3 Data Architecture Agent — Universal Enterprise Architecture Reasoning.

Technology-agnostic: supports .NET/EF Core, Java Spring, Python Django,
Node.js Express, and any other framework through the Universal Semantic Model.

Flow:
  1. Load extracted + analyzed JSON (USM format when available)
  2. Summarize per domain (token-efficient, universal terminology)
  3. Send domain chunks to Claude CLI (universal enterprise concepts)
  4. Merge responses
  5. Write all 10 REVIEW/*.md + architecture-summary.json
"""

from __future__ import annotations
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from core.confidence import Confidence

_CLI_TIMEOUT = 600
_MAX_SECTION = 10_000   # chars per JSON section in prompt


class M3DataArchitectAgent:
    def __init__(self, output_dir: str = "REVIEW",
                 skills_dir: str = "skills",
                 extracted_dir: str = "memory/extracted",
                 analysis_dir: str = "memory/m3"):
        self.output_dir    = Path(output_dir)
        self.skills_dir    = Path(skills_dir)
        self.extracted_dir = Path(extracted_dir)
        self.analysis_dir  = Path(analysis_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict[str, str]:
        print("[M3Agent] Loading context...")
        ctx = self._load_context()

        print("[M3Agent] Building domain summaries...")
        summaries = self._build_domain_summaries(ctx)

        print("[M3Agent] Invoking Claude (chunked by domain)...")
        ai_sections = self._invoke_chunked(summaries, ctx)

        print("[M3Agent] Writing all 10 reports...")
        reports = self._write_all_reports(ai_sections, ctx)

        return reports

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------

    def _load_context(self) -> dict[str, Any]:
        loaders = {
            "entities":       "entities.json",
            "relationships":  "relationships.json",
            "erd_map":        "erd_map.json",
            "dep_graph":      "dependency_graph.json",
            "apis":           "apis.json",
            "inventory":      "../../REVIEW/inventory.json",
            "semantic_model": "semantic_model.json",   # Universal Semantic Model (if available)
        }
        analysis_loaders = {
            "governance":     "governance_findings.json",
            "lineage":        "lineage_analysis.json",
            "redundancy":     "redundancy_analysis.json",
            "validation":     "validation_results.json",
        }
        ctx: dict[str, Any] = {}
        for key, fname in loaders.items():
            path = self.extracted_dir / fname
            if not path.exists():
                path = ROOT / "REVIEW" / fname.split("/")[-1]
            if path.exists():
                try:
                    ctx[key] = json.loads(path.read_text(encoding="utf-8"))
                    print(f"  Loaded: {path.name}")
                except Exception as e:
                    print(f"  WARN: {path}: {e}")

        for key, fname in analysis_loaders.items():
            path = self.analysis_dir / fname
            if path.exists():
                try:
                    ctx[key] = json.loads(path.read_text(encoding="utf-8"))
                    print(f"  Loaded: {path.name}")
                except Exception as e:
                    print(f"  WARN: {path}: {e}")

        # SQL-specific context files (database-first analysis)
        sql_loaders = {
            "sql_lineage":        "sql_lineage.json",
            "sql_governance":     "sql_governance.json",
            "sp_lineage":         "stored_procedure_lineage.json",
            "stored_procedures":  "stored_procedures.json",
            "sql_relationships":  "sql_relationships.json",
            "enterprise_graph":   "enterprise_graph.json",
        }
        for key, fname in sql_loaders.items():
            path = self.extracted_dir / fname
            if path.exists():
                try:
                    ctx[key] = json.loads(path.read_text(encoding="utf-8"))
                    print(f"  Loaded: {path.name}")
                except Exception as e:
                    print(f"  WARN: {path}: {e}")

        # If SemanticModel is available, merge its richer data into legacy keys
        if "semantic_model" in ctx:
            sm = ctx["semantic_model"]
            sm_entities = sm.get("entities", [])
            sm_rels     = sm.get("relationships", [])

            # Override entities when code-first extraction found nothing (SQL-only project)
            if not ctx.get("entities", {}).get("entities") and sm_entities:
                legacy_ents = []
                for e in sm_entities:
                    leg = dict(e)
                    leg["entity"] = e.get("name", "?")
                    norm_fields = []
                    for f in e.get("fields", []):
                        nf = dict(f)
                        nf.setdefault("type", f.get("normalized_type", f.get("raw_type", "?")))
                        nf.setdefault("is_fk", False)
                        nf.setdefault("is_navigation", False)
                        norm_fields.append(nf)
                    leg["fields"] = norm_fields
                    leg.setdefault("aggregate_root", False)
                    if not isinstance(leg.get("confidence"), dict):
                        leg["confidence"] = {"level": e.get("confidence", "MEDIUM")}
                    leg.setdefault("foreign_keys", [])
                    leg.setdefault("navigation_collection", [])
                    leg.setdefault("navigation_scalar", [])
                    legacy_ents.append(leg)
                ctx["entities"] = {
                    "entities": legacy_ents,
                    "value_objects": [],
                    "entity_count": len(legacy_ents),
                    "value_object_count": 0,
                }

            # Override relationships if they reference entities from a different project (stale)
            existing_rels = ctx.get("relationships", {})
            our_names = {e.get("name", "").lower() for e in sm_entities}
            stale = False
            if existing_rels.get("relationships") and our_names:
                rel_sources = {r.get("source", "").lower() for r in existing_rels["relationships"][:5]}
                stale = not any(s in our_names for s in rel_sources)
            if stale or not existing_rels.get("relationships"):
                legacy_rels = []
                for r in sm_rels:
                    lr = dict(r)
                    lr.setdefault("relationship", r.get("kind", "references"))
                    lr.setdefault("via_type", "FK")
                    legacy_rels.append(lr)
                ctx["relationships"] = {
                    "relationships": legacy_rels,
                    "relationship_count": len(legacy_rels),
                }

            ctx.setdefault("apis", {"endpoints": sm.get("endpoints", []),
                                    "mediatr_handlers": sm.get("handlers", [])})
            ctx["_tech_context"] = sm.get("tech_context", {})

        return ctx

    # ------------------------------------------------------------------
    # Domain summarization (reduces token usage)
    # ------------------------------------------------------------------

    def _build_domain_summaries(self, ctx: dict) -> dict[str, str]:
        entities  = ctx.get("entities", {})
        rels      = ctx.get("relationships", {})
        apis      = ctx.get("apis", {})
        gov       = ctx.get("governance", {})
        lineage   = ctx.get("lineage", {})
        redundancy = ctx.get("redundancy", {})
        validation = ctx.get("validation", {})
        inventory  = ctx.get("inventory", {})

        # --- Entities summary (full field enumeration — no truncation) ---
        ent_lines = []
        for e in entities.get("entities", []):
            # Full field list: type name [nullable] [FK] [nav]
            field_parts = []
            for f in e.get("fields", []):
                ftype = f.get("type", "?")
                fname = f.get("name", "?")
                tags  = []
                if f.get("is_fk"):       tags.append("FK")
                if f.get("is_navigation"): tags.append("nav")
                tag_s = f"[{','.join(tags)}]" if tags else ""
                field_parts.append(f"{ftype} {fname}{tag_s}")
            fields_str = "; ".join(field_parts) or "none"

            fk_refs  = ", ".join(f.get("references","?") for f in e.get("foreign_keys", []))
            nav_coll = ", ".join(n["target_entity"] for n in e.get("navigation_collection", []))
            nav_scal = ", ".join(f"{n['name']}:{n['target_entity']}"
                                 for n in e.get("navigation_scalar", []))
            ent_lines.append(
                f"  {e['entity']} [L{e.get('line_number','')}] "
                f"agg_root={e.get('aggregate_root')} aggregate={e.get('aggregate')} "
                f"conf={e.get('confidence',{}).get('level','?')}\n"
                f"    FIELDS({len(e.get('fields',[]))}): {fields_str}\n"
                f"    FK_REFS: [{fk_refs}]  COLLECTIONS: [{nav_coll}]  NAV_SCALAR: [{nav_scal}]"
            )
        for vo in entities.get("value_objects", []):
            vo_fields = "; ".join(
                f"{f.get('type','?')} {f.get('name','?')}"
                for f in vo.get("fields", [])
            ) or "none"
            ent_lines.append(
                f"  {vo['entity']} [VO] owned_by={vo.get('owned_by',vo.get('aggregate','?'))} "
                f"conf=HIGH\n    FIELDS({len(vo.get('fields',[]))}): {vo_fields}"
            )
        entities_summary = (
            f"ENTITIES ({entities.get('entity_count',0)} entities, "
            f"{entities.get('value_object_count',0)} value objects):\n" +
            "\n".join(ent_lines)
        )

        # --- Relationships summary ---
        rel_lines = []
        for r in rels.get("relationships", []):
            rel_lines.append(
                f"  {r['source']} --[{r['relationship']}]--> {r['target']} "
                f"via={r.get('via','?')} type={r.get('via_type','?')} "
                f"conf={r.get('confidence','?')} L{r.get('line_number','')}"
            )
        rels_summary = (
            f"RELATIONSHIPS ({rels.get('relationship_count',0)}):\n" +
            "\n".join(rel_lines)
        )

        # --- API summary (technology-agnostic) ---
        api_lines = []
        for ep in apis.get("endpoints", []):
            # Support both legacy (endpoint) and USM (path) key names
            path_val = ep.get("path", ep.get("endpoint", "?"))
            api_lines.append(
                f"  {ep['method']} {path_val} [{ep.get('style','')}] "
                f"auth={ep.get('auth_required')} "
                f"req={ep.get('request_model')} resp={ep.get('response_model')} "
                f"repos={ep.get('repositories',[])} entities={ep.get('entities_touched',[])} "
                f"conf={ep.get('confidence','?')}"
            )
        for hdl in apis.get("mediatr_handlers", apis.get("handlers", [])):
            # "mediatr_handlers" = .NET; "handlers" = USM universal
            hdl_name = hdl.get("class_name", hdl.get("name", "?"))
            req_type = hdl.get("request_type", "?")
            pattern  = hdl.get("pattern", "handler")
            api_lines.append(
                f"  HANDLER:{hdl_name} pattern={pattern} req={req_type} "
                f"repos={hdl.get('repositories',[])} conf={hdl.get('confidence','?')}"
            )
        apis_summary = (
            f"API ENDPOINTS ({apis.get('endpoint_count', len(apis.get('endpoints',[])))}"
            f" endpoints, "
            f"{apis.get('handler_count', len(apis.get('mediatr_handlers',[])+apis.get('handlers',[])))} handlers):\n" +
            "\n".join(api_lines)
        )

        # --- Governance summary (HIGH conf only) ---
        gov_lines = []
        for f in gov.get("findings", [])[:50]:
            if f.get("confidence") == Confidence.HIGH.value:
                gov_lines.append(
                    f"  [{f['severity']}] {f['entity']}.{f.get('field','?')} "
                    f"rule={f['rule_type']} method={f.get('detection_method','?')} "
                    f"gdpr={f.get('gdpr_category','')}"
                )
        gov_summary = f"GOVERNANCE ({gov.get('finding_count',0)} findings):\n" + "\n".join(gov_lines)

        # --- Lineage summary ---
        lin_lines = []
        for flow in lineage.get("flows", [])[:30]:
            layers = " -> ".join(s["layer"] for s in flow.get("steps", []))
            lin_lines.append(
                f"  [{flow.get('type','?')}] {flow['flow']} | "
                f"{layers} | gaps={flow.get('has_gaps')} conf={flow.get('confidence','?')}"
            )
        lin_summary = f"LINEAGE ({lineage.get('flow_count',0)} flows):\n" + "\n".join(lin_lines)

        # --- Redundancy summary ---
        red_lines = []
        for f in redundancy.get("actionable", [])[:20]:
            red_lines.append(
                f"  [{f['type']}] {', '.join(f.get('entities',[f.get('field_name','?')]))} "
                f"overlap={f.get('overlap_pct','')} conf={f.get('confidence','?')}"
            )
        red_summary = (
            f"REDUNDANCY ({redundancy.get('finding_count',0)} findings, "
            f"{len(redundancy.get('actionable',[]))} actionable):\n" +
            "\n".join(red_lines)
        )

        # --- Validation summary ---
        val_lines = []
        for issue in validation.get("critical_issues", []) + validation.get("warnings", [])[:20]:
            val_lines.append(
                f"  [{issue['severity']}] {issue['check']}: "
                f"{issue.get('entity','?')}.{issue.get('field','?')} — "
                f"{issue.get('description','')[:80]}"
            )
        val_summary = (
            f"VALIDATION ({validation.get('issue_count',0)} issues — "
            f"CRITICAL:{len(validation.get('critical_issues',[]))} "
            f"WARNING:{len(validation.get('warnings',[]))}):\n" +
            "\n".join(val_lines)
        )

        # --- Stack summary ---
        stack = inventory.get("stack", {})
        langs = [l["name"] for l in stack.get("languages", []) if l.get("primary")]
        fwks  = stack.get("frameworks", [])[:8]
        stack_summary = (
            f"STACK: languages={langs} "
            f"frameworks={[f.get('name') if isinstance(f,dict) else f for f in fwks]} "
            f"architecture={[a.get('pattern') if isinstance(a,dict) else a for a in stack.get('architecture_type',[])[:3]]}"
        )

        summaries = {
            "entities":    entities_summary,
            "rels":        rels_summary,
            "apis":        apis_summary,
            "governance":  gov_summary,
            "lineage":     lin_summary,
            "redundancy":  red_summary,
            "validation":  val_summary,
            "stack":       stack_summary,
        }

        # --- SQL lineage summary (database-first projects) ---
        sql_lin = ctx.get("sql_lineage", {})
        if sql_lin and sql_lin.get("edge_count", 0) > 0:
            sql_lines = [
                f"SQL LINEAGE: {sql_lin.get('edge_count',0)} edges, "
                f"{sql_lin.get('chain_count',0)} chains, "
                f"{sql_lin.get('table_count',0)} tables, "
                f"{sql_lin.get('view_count',0)} views, "
                f"{sql_lin.get('proc_count',0)} procs\n"
            ]
            for etl in sql_lin.get("etl_flows", [])[:10]:
                sql_lines.append(
                    f"  ETL: {etl['source']} -> {etl['target']} via {etl['via']}\n"
                )
            for chain in sql_lin.get("lineage_chains", [])[:15]:
                if chain.get("risk_level") in ("HIGH", "MEDIUM"):
                    chain_path = " -> ".join(chain["path"])
                    sql_lines.append(
                        f"  [{chain['risk_level']}] {chain_path} "
                        f"(depth={chain['depth']})\n"
                    )
            summaries["sql_lineage"] = "".join(sql_lines)

        # --- Stored procedures summary (prefer enriched stored_procedures.json) ---
        sp_enriched = ctx.get("stored_procedures", {})
        sp_lineage  = ctx.get("sp_lineage", {})
        sp = sp_enriched if sp_enriched.get("procedure_count", 0) > 0 else sp_lineage
        if sp and sp.get("procedure_count", 0) > 0:
            call_graph = sp.get("call_graph", [])
            max_depth  = sp.get("max_call_depth", 0)
            sp_lines = [
                f"STORED PROCEDURES: {sp.get('procedure_count',0)} total | "
                f"functions={sp.get('function_count',0)} | "
                f"call_depth_max={max_depth} | "
                f"tables_touched={len(sp.get('tables_touched',[]))} | "
                f"dynamic_sql={sp.get('with_dynamic_sql', sp_lineage.get('with_dynamic_sql',0))} "
                f"high_risk={sp.get('high_risk', sp_lineage.get('high_risk',0))} "
                f"txn={sp.get('with_transactions', sp_lineage.get('with_transactions',0))}\n"
            ]
            # Call graph (nested calls)
            if call_graph:
                sp_lines.append("  CALL GRAPH (callers with nested calls):\n")
                for cg in call_graph[:10]:
                    sp_lines.append(
                        f"    {cg['caller']} (depth={cg['depth']}) -> {', '.join(cg['callees'][:5])}\n"
                    )
            # Per-procedure details
            proc_list = sp.get("procedures", [])
            for proc in proc_list[:25]:
                risk    = proc.get("dynamic_sql_risk", "NONE")
                flags   = proc.get("risk_flags", [])
                reads   = ", ".join(proc.get("tables_read", [])[:4])
                writes  = ", ".join(proc.get("tables_written", [])[:4])
                inputs  = ", ".join(
                    p.get("name", str(p)) if isinstance(p, dict) else str(p)
                    for p in proc.get("inputs", proc.get("parameters_in", []))[:4]
                )
                outputs = ", ".join(
                    p.get("name", str(p)) if isinstance(p, dict) else str(p)
                    for p in proc.get("outputs", proc.get("parameters_out", []))[:4]
                )
                nested  = ", ".join(proc.get("nested_calls", [])[:3])
                sp_lines.append(
                    f"  PROC:{proc.get('schema','dbo')}.{proc.get('name','?')} "
                    f"type={proc.get('type', proc.get('proc_type','?'))} "
                    f"crud={','.join(proc.get('crud_operations', list(proc.get('crud_summary',{}).keys()))[:4])} "
                    f"depth={proc.get('call_depth',0)} "
                    f"reads=[{reads}] writes=[{writes}]"
                    + (f" inputs=[{inputs}]" if inputs else "")
                    + (f" outputs=[{outputs}]" if outputs else "")
                    + (f" calls=[{nested}]" if nested else "")
                    + (f" dynSQL={risk}" if risk != "NONE" else "")
                    + (f" RISKS:{flags[:2]}" if flags else "")
                    + "\n"
                )
            summaries["stored_procs"] = "".join(sp_lines)

        # --- SQL relationships summary ---
        sql_rels = ctx.get("sql_relationships", {})
        if sql_rels and sql_rels.get("relationship_count", 0) > 0:
            conf = sql_rels.get("confidence_breakdown", {})
            rel_lines = [
                f"SQL RELATIONSHIPS: {sql_rels.get('relationship_count',0)} total | "
                f"FK_constraints={sql_rels.get('fk_constraint_count',0)} "
                f"inferred={sql_rels.get('inferred_count',0)} "
                f"self_refs={sql_rels.get('self_reference_count',0)} | "
                f"HIGH={conf.get('HIGH',0)} MEDIUM={conf.get('MEDIUM',0)} LOW={conf.get('LOW',0)}\n"
            ]
            for r in sql_rels.get("relationships", [])[:25]:
                src_cols = ", ".join(r.get("source_columns", [])[:3])
                tgt_cols = ", ".join(r.get("target_columns", [])[:3])
                cname    = r.get("constraint_name", "")
                on_del   = r.get("on_delete", "NO ACTION")
                rel_lines.append(
                    f"  [{r.get('confidence','?')}] {r.get('source_table','?')}"
                    f"({src_cols}) -> {r.get('target_table','?')}({tgt_cols}) "
                    f"kind={r.get('relationship_kind','?')}"
                    + (f" constraint={cname}" if cname else "")
                    + (f" ON DELETE {on_del}" if on_del != "NO ACTION" else "")
                    + "\n"
                )
            summaries["sql_relationships"] = "".join(rel_lines)

        # --- Enterprise graph summary ---
        graph = ctx.get("enterprise_graph", {})
        if graph and graph.get("node_count", 0) > 0:
            nt = graph.get("node_type_counts", {})
            et = graph.get("edge_type_counts", {})
            graph_lines = [
                f"ENTERPRISE GRAPH: {graph.get('node_count',0)} nodes | "
                f"{graph.get('edge_count',0)} edges\n"
                f"  Node types: TABLE={nt.get('TABLE',0)} COLUMN={nt.get('COLUMN',0)} "
                f"PROCEDURE={nt.get('PROCEDURE',0)} VIEW={nt.get('VIEW',0)} "
                f"INDEX={nt.get('INDEX',0)} CONSTRAINT={nt.get('CONSTRAINT',0)}\n"
                f"  Edge types: FK_TO={et.get('FK_TO',0)} "
                f"PROC_READS={et.get('PROC_READS',0)} PROC_WRITES={et.get('PROC_WRITES',0)} "
                f"VIEW_READS={et.get('VIEW_READS',0)} INDEXED_BY={et.get('INDEXED_BY',0)}\n"
            ]
            # High-impact tables
            impact = sorted(
                graph.get("table_impact", []),
                key=lambda x: -x.get("total_consumers", 0)
            )[:10]
            if impact:
                graph_lines.append("  Top tables by consumer count:\n")
                for t in impact:
                    graph_lines.append(
                        f"    {t['table']}: consumers={t.get('total_consumers',0)} "
                        f"fk_children={len(t.get('fk_children',[]))}\n"
                    )
            summaries["enterprise_graph"] = "".join(graph_lines)

        # --- SQL governance summary ---
        sql_gov = ctx.get("sql_governance", {})
        if sql_gov and sql_gov.get("finding_count", 0) > 0:
            sev_map = sql_gov.get("severity_counts", {})
            gov_lines = [
                f"SQL GOVERNANCE: {sql_gov.get('finding_count',0)} findings | "
                f"CRITICAL={sev_map.get('CRITICAL',0)} "
                f"WARNING={sev_map.get('WARNING',0)} "
                f"NOTE={sev_map.get('NOTE',0)}\n"
            ]
            # Sort by severity: CRITICAL first, then WARNING, then NOTE
            sev_order = {"CRITICAL": 0, "WARNING": 1, "NOTE": 2}
            sorted_findings = sorted(
                sql_gov.get("findings", []),
                key=lambda x: sev_order.get(x.get("severity", "NOTE"), 3),
            )
            for f in sorted_findings[:30]:
                if f.get("severity") in ("CRITICAL", "WARNING"):
                    gov_lines.append(
                        f"  [{f['severity']}] {f.get('table','?')}.{f.get('column','?')} "
                        f"rule={f.get('rule_type','?')}: {f.get('description','')[:80]}\n"
                    )
            summaries["sql_governance"] = "".join(gov_lines)

        return summaries

    # ------------------------------------------------------------------
    # Chunked Claude invocation
    # ------------------------------------------------------------------

    def _invoke_chunked(self, summaries: dict, ctx: dict) -> dict[str, str]:
        skill_path = self.skills_dir / "m3-data-architect.md"
        skill_text = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""

        # Build single comprehensive prompt with summarized evidence
        combined = "\n\n".join(f"### {k.upper()}\n{v}" for k, v in summaries.items())

        # Detect technology stack for context in prompt
        tech_ctx = ctx.get("_tech_context", {})
        langs    = tech_ctx.get("languages", ["unknown"])
        orms     = tech_ctx.get("orms",      ["unknown"])
        arch     = tech_ctx.get("architecture", "unknown")
        db_types = tech_ctx.get("db_types",   [])
        tech_line = (
            f"Technology Stack: languages={langs}, ORMs={orms}, "
            f"architecture={arch}, databases={db_types}"
        )

        # Check if this is a database-first project
        is_db_first = bool(ctx.get("sql_lineage") or ctx.get("sp_lineage"))

        prompt = f"""{skill_text}

---
# IMPORTANT INSTRUCTION
Output ONLY plain text. Do NOT use any tools. Do NOT write any files. Do NOT make any function calls.
The calling system handles all file writing. Your job is ONLY to return the report text below.

---
# ENTERPRISE DATA ARCHITECTURE ANALYSIS

{tech_line}

## Evidence Summary (deterministic extraction — cite these facts, do not invent beyond them)

{combined}

---
# TASK: Generate 10 architecture report sections.

Each section MUST start with exactly `## SECTION_NAME` on its own line.
Every finding MUST cite source_file and confidence level (HIGH/MEDIUM/LOW).
Never invent relationships or entities not present in the evidence above.
Use universal enterprise terminology — do NOT assume any specific framework.

## DATA_ARCHITECTURE
Document: architecture patterns confirmed in evidence, data domains/aggregates,
canonical entity per domain, aggregate root ownership, bounded context boundaries.
Include: domain summary table, bounded context dependency diagram.
Base on evidence — do not assume DDD patterns if not in evidence.

## SCHEMA_CATALOG
For every entity and value object: fields table (Name|NormalizedType|Role),
FK relationships, navigation/association properties,
aggregate root status, ORM attributes detected, governance notes.

## ENTITY_RELATIONSHIPS
Document all relationships with: source, target, cardinality, FK field,
aggregate boundary, confidence level. Include ASCII/Mermaid ERD if possible.

## DATA_LINEAGE
Trace each API endpoint and handler to its entities via repositories/services.
Format: `METHOD /route → Handler/Service → Repository → Entity`
Identify integration boundaries. Flag gaps.

## GOVERNANCE_REPORT
List all PII, PCI-DSS, GDPR, auth, data integrity findings.
Format: table per severity (CRITICAL/WARNING/NOTE).
Include: entity, field, rule_type, evidence, recommendation.

## REDUNDANCY_ANALYSIS
Document actionable redundancy findings: entity clones, repeated fields,
cross-domain field duplication, structural duplicates.
For each: overlap%, shared fields, recommendation.

## CANONICAL_MODEL
Define the canonical (domain-agnostic) business model.
For each concept: canonical name, source entity, fields, invariants, lifecycle.

## INTEGRATION_MAP
Document all integration points: external systems, internal service boundaries,
cross-domain data flows, auth boundaries.
Reference actual classes/modules from evidence, not assumed frameworks.

## MODERNIZATION_RECOMMENDATIONS
Priority-ranked recommendations (P1–P4).
For each: current state, risk, specific code change recommendation.
Derive from validation issues, governance gaps, redundancy findings.

## VALIDATION_REPORT
Summarize cross-layer validation findings.
For each issue: check type, entity, severity, evidence, recommendation.
Flag any CRITICAL issues prominently.

---
Rules:
- Cite source files and line numbers where provided in evidence.
- Use confidence levels from evidence: HIGH/MEDIUM/LOW.
- Label every finding: CONFIRMED (has source+line evidence) / INFERRED / RECOMMENDED.
- Never merge two entities unless evidence confirms they are the same.
- Separate confirmed from inferred findings in each section.
- If a section has no findings, say so explicitly.
- Do NOT hardcode framework-specific assumptions (EF Core, MediatR, Spring, etc.)
  unless those appear explicitly in the evidence.
""".strip()

        # For database-first projects, append SQL-specific report request
        if is_db_first:
            prompt += """

---
# ADDITIONAL SQL DATABASE-FIRST SECTIONS

This project contains SQL DDL / stored procedures. Generate these additional sections:

## DDD_ANALYSIS
Map SQL tables to bounded contexts and aggregates.
Identify natural domain boundaries from FK relationships and naming patterns.
Flag cross-context table references. Suggest aggregate root candidates.

## LEGACY_GOVERNANCE_REPORT
Summarize findings from sql_governance section:
PII/PCI columns without encryption markers, missing PKs, nullable FKs,
missing audit columns, wide tables (>30 cols), naming inconsistencies,
dynamic SQL injection risks. Group by severity. Include remediation priority.
""".strip()

        response = self._call_claude(prompt)
        if not response:
            print("[M3Agent] Claude unavailable — using deterministic fallback")
            return {}

        return self._split_sections(response)

    # ------------------------------------------------------------------
    # Claude CLI call
    # ------------------------------------------------------------------

    def _call_claude(self, prompt: str) -> Optional[str]:
        try:
            result = subprocess.run(
                ["claude", "-p", "--tools", ""],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_CLI_TIMEOUT,
            )
            if result.returncode != 0:
                print(f"[M3Agent] WARN: claude CLI code {result.returncode}: {result.stderr[:200]}")
                return None
            output = result.stdout.strip()
            if not output:
                print("[M3Agent] WARN: empty response")
                return None
            print(f"[M3Agent] Received {len(output):,} chars from Claude CLI")
            return output
        except FileNotFoundError:
            print("[M3Agent] WARN: 'claude' CLI not found in PATH")
            return None
        except subprocess.TimeoutExpired:
            print(f"[M3Agent] WARN: claude CLI timed out after {_CLI_TIMEOUT}s")
            return None

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _split_sections(self, text: str) -> dict[str, str]:
        KEYS = [
            "DATA_ARCHITECTURE", "SCHEMA_CATALOG", "ENTITY_RELATIONSHIPS",
            "DATA_LINEAGE", "GOVERNANCE_REPORT", "REDUNDANCY_ANALYSIS",
            "CANONICAL_MODEL", "INTEGRATION_MAP", "MODERNIZATION_RECOMMENDATIONS",
            "VALIDATION_REPORT",
            # SQL database-first sections
            "DDD_ANALYSIS", "LEGACY_GOVERNANCE_REPORT",
        ]
        pattern = re.compile(
            r'^##\s+(' + '|'.join(re.escape(k) for k in KEYS) + r')\s*$',
            re.MULTILINE | re.IGNORECASE,
        )
        parts = pattern.split(text)
        sections: dict[str, str] = {}
        i = 1
        while i < len(parts) - 1:
            key = parts[i].strip().upper().replace(" ", "_")
            sections[key] = parts[i + 1].strip()
            i += 2
        return sections

    # ------------------------------------------------------------------
    # Report writing (all 10 files)
    # ------------------------------------------------------------------

    def _write_all_reports(self, ai_sections: dict[str, str], ctx: dict) -> dict[str, str]:
        ts = datetime.now(timezone.utc).isoformat()
        header = f"_Generated: {ts} by M3 Data Architecture Agent (Claude Code)_\n\n"

        report_map = {
            "DATA_ARCHITECTURE":             "DATA_ARCHITECTURE.md",
            "SCHEMA_CATALOG":                "SCHEMA_CATALOG.md",
            "ENTITY_RELATIONSHIPS":          "ENTITY_RELATIONSHIPS.md",
            "DATA_LINEAGE":                  "DATA_LINEAGE.md",
            "GOVERNANCE_REPORT":             "GOVERNANCE_REPORT.md",
            "REDUNDANCY_ANALYSIS":           "REDUNDANCY_ANALYSIS.md",
            "CANONICAL_MODEL":               "CANONICAL_MODEL.md",
            "INTEGRATION_MAP":               "INTEGRATION_MAP.md",
            "MODERNIZATION_RECOMMENDATIONS": "MODERNIZATION_RECOMMENDATIONS.md",
            "VALIDATION_REPORT":             "VALIDATION_REPORT.md",
            # SQL database-first reports (only written when AI produces them)
            "DDD_ANALYSIS":                  "DDD_ANALYSIS.md",
            "LEGACY_GOVERNANCE_REPORT":      "LEGACY_GOVERNANCE_REPORT.md",
        }

        written: dict[str, str] = {}
        for section_key, filename in report_map.items():
            content = ai_sections.get(section_key, "")
            if not content:
                content = self._deterministic_fallback(section_key, ctx)

            title   = section_key.replace("_", " ").title()
            full    = f"# {title}\n\n{header}{content.strip()}\n"
            out_path = self.output_dir / filename
            out_path.write_text(full, encoding="utf-8")
            written[section_key] = str(out_path)
            print(f"  -> {filename} ({len(full):,} chars)")

        # architecture-summary.json
        summary = self._build_summary_json(ctx)
        sj_path = self.output_dir / "architecture-summary.json"
        sj_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        written["architecture-summary"] = str(sj_path)

        return written

    # ------------------------------------------------------------------
    # Deterministic fallback sections
    # ------------------------------------------------------------------

    def _deterministic_fallback(self, key: str, ctx: dict) -> str:
        entities  = ctx.get("entities", {})
        rels      = ctx.get("relationships", {})
        apis      = ctx.get("apis", {})
        gov       = ctx.get("governance", {})
        lineage   = ctx.get("lineage", {})
        redundancy = ctx.get("redundancy", {})
        validation = ctx.get("validation", {})

        if key == "DATA_ARCHITECTURE":
            lines = ["## Summary\n\n"]
            lines.append(f"- Entities: {entities.get('entity_count',0)}\n")
            lines.append(f"- Value Objects: {entities.get('value_object_count',0)}\n")
            lines.append(f"- Relationships: {rels.get('relationship_count',0)}\n")
            lines.append(f"- API Endpoints: {apis.get('endpoint_count',0)}\n\n")
            lines.append("## Domains\n\n")
            from collections import defaultdict
            domains: dict[str, list] = defaultdict(list)
            for e in entities.get("entities", []):
                domains[e.get("aggregate") or "Catalog"].append(e["entity"])
            for domain, ents in domains.items():
                lines.append(f"**{domain}:** {', '.join(ents)}\n\n")
            return "".join(lines)

        if key == "SCHEMA_CATALOG":
            lines = []
            for e in entities.get("entities", []) + entities.get("value_objects", []):
                lines.append(f"### {e['entity']}\n\n")
                lines.append(f"**Kind:** {e['kind']} | **Aggregate Root:** {e.get('aggregate_root')} | "
                              f"**Confidence:** {e.get('confidence',{}).get('level','?')}\n\n")
                lines.append("| Field | Type | Role |\n|-------|------|------|\n")
                for f in e.get("fields", []):
                    role = "FK" if f.get("is_fk") else ("Navigation" if f.get("is_navigation") else "Data")
                    lines.append(f"| {f['name']} | {f.get('type','')} | {role} |\n")
                lines.append("\n")
            return "".join(lines)

        if key == "ENTITY_RELATIONSHIPS":
            lines = ["```\n"]
            for r in rels.get("relationships", []):
                sym = {"one_to_many":"||--o{","many_to_one":"}o--||",
                       "embeds_value_object":"||--||","references":"..>",
                       "many_to_many":"}o--o{"}.get(r["relationship"],"--")
                lines.append(f"{r['source']} {sym} {r['target']} : \"{r.get('via','')}\" "
                              f"[{r.get('confidence','?')}]\n")
            lines.append("```\n")
            return "".join(lines)

        if key == "DATA_LINEAGE":
            lines = []
            for flow in lineage.get("flows", [])[:20]:
                lines.append(f"### {flow['flow']}\n\n")
                for step in flow.get("steps", []):
                    lines.append(f"- [{step['layer'].upper()}] `{step['component']}` "
                                  f"[{step.get('confidence','?')}]\n")
                lines.append("\n")
            return "".join(lines) or "_Lineage data not available._\n"

        if key == "GOVERNANCE_REPORT":
            findings = gov.get("findings", [])
            lines = [f"**Total findings:** {len(findings)}\n\n"]
            for sev in ("WARNING", "NOTE"):
                grp = [f for f in findings if f.get("severity") == sev]
                if grp:
                    lines.append(f"## {sev}\n\n")
                    lines.append("| Entity | Field | Rule | Confidence |\n|--------|-------|------|------------|\n")
                    for f in grp[:20]:
                        lines.append(f"| {f.get('entity','?')} | `{f.get('field','?')}` "
                                      f"| {f.get('rule_type','?')} | {f.get('confidence','?')} |\n")
                    lines.append("\n")
            return "".join(lines)

        if key == "REDUNDANCY_ANALYSIS":
            findings = redundancy.get("actionable", [])
            if not findings:
                return "_No actionable redundancy findings detected._\n"
            lines = []
            for f in findings[:15]:
                subj = ", ".join(f.get("entities") or [f.get("field_name","?")])
                lines.append(f"- **{f['type']}**: `{subj}` — {f.get('recommendation','')}\n")
            return "".join(lines)

        if key == "VALIDATION_REPORT":
            issues = validation.get("issues", [])
            lines = [f"**Total issues:** {len(issues)}\n\n"]
            for sev in ("CRITICAL", "WARNING", "NOTE"):
                grp = [i for i in issues if i.get("severity") == sev]
                if grp:
                    lines.append(f"## {sev} ({len(grp)})\n\n")
                    for i in grp[:15]:
                        lines.append(f"- `{i['check']}`: {i.get('entity','?')}.{i.get('field','')} — "
                                      f"{i.get('description','')[:80]}\n")
                    lines.append("\n")
            return "".join(lines)

        return f"_Section {key}: AI analysis unavailable. See memory/extracted/ for raw data._\n"

    # ------------------------------------------------------------------
    # Architecture summary JSON
    # ------------------------------------------------------------------

    def _build_summary_json(self, ctx: dict) -> dict:
        entities   = ctx.get("entities",   {})
        rels       = ctx.get("relationships", {})
        apis       = ctx.get("apis",       {})
        gov        = ctx.get("governance", {})
        validation = ctx.get("validation", {})
        redundancy = ctx.get("redundancy", {})
        inventory  = ctx.get("inventory",  {})

        stack = inventory.get("stack", {})
        return {
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "generated_by":  "M3 Data Architecture Agent (Claude Code)",
            "summary": {
                "entities":        entities.get("entity_count", 0),
                "value_objects":   entities.get("value_object_count", 0),
                "relationships":   rels.get("relationship_count", 0),
                "api_endpoints":   apis.get("endpoint_count", 0),
                "mediatr_handlers": apis.get("handler_count", 0),
            },
            "confidence_profile": {
                "parser": entities.get("parser", "unknown"),
                "entity_confidence": "HIGH" if "ts" in entities.get("parser","") else "MEDIUM",
                "relationship_confidence": "HIGH (ORM fluent) + MEDIUM (naming convention)",
                "api_confidence": "HIGH",
            },
            "tech_context": ctx.get("_tech_context", {}),
            "governance_summary": {
                "total_findings": gov.get("finding_count", 0),
                "pci_findings":   len(gov.get("pci_fields", [])),
                "gdpr_fields":    len(gov.get("gdpr_fields", [])),
                "severity_counts": gov.get("severity_counts", {}),
            },
            "validation_summary": {
                "total_issues":   validation.get("issue_count", 0),
                "severity_counts": validation.get("severity_counts", {}),
            },
            "redundancy_summary": {
                "total_findings":  redundancy.get("finding_count", 0),
                "actionable":      len(redundancy.get("actionable", [])),
            },
            "stack": {
                "languages":   [l["name"] for l in stack.get("languages", []) if l.get("primary")],
                "frameworks":  [f.get("name") if isinstance(f,dict) else f
                                for f in stack.get("frameworks", [])[:10]],
                "databases":   [d.get("name") if isinstance(d,dict) else d
                                for d in stack.get("databases", [])],
                "architecture_type": [a.get("pattern") if isinstance(a,dict) else a
                                      for a in stack.get("architecture_type", [])[:3]],
            },
        }
