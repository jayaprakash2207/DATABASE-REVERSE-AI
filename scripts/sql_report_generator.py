"""
SQL Report Generator

Generates SQL-specific REVIEW/*.md reports:
  - SQL_LINEAGE_REPORT.md   (from sql_lineage.json)
  - STORED_PROCEDURE_ANALYSIS.md  (from stored_procedure_lineage.json)
  - DATA_QUALITY_REPORT.md  (from sql_governance.json — if not already generated)

Integrated into the main analysis pipeline for database-first projects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SQLReportGenerator:

    def __init__(
        self,
        output_dir:   str = "REVIEW",
        extracted_dir: str = "memory/extracted",
    ) -> None:
        self.output_dir    = Path(output_dir)
        self.extracted_dir = Path(extracted_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self) -> dict[str, str]:
        generated: dict[str, str] = {}

        if (self.extracted_dir / "sql_lineage.json").exists():
            generated["SQL_LINEAGE_REPORT"] = self._write_lineage_report()

        # Prefer enriched stored_procedures.json (dict format from SPAnalyzer);
        # fall back to lineage file, or old list-format stored_procedures.json
        sp_enriched_path = self.extracted_dir / "stored_procedures.json"
        sp_lineage_path  = self.extracted_dir / "stored_procedure_lineage.json"
        use_enriched = False
        if sp_enriched_path.exists():
            try:
                sp_check = json.loads(sp_enriched_path.read_text(encoding="utf-8"))
                use_enriched = isinstance(sp_check, dict) and "procedures" in sp_check
            except Exception:
                pass
        if use_enriched:
            generated["STORED_PROCEDURE_ANALYSIS"] = self._write_sp_report(use_enriched=True)
        elif sp_lineage_path.exists():
            generated["STORED_PROCEDURE_ANALYSIS"] = self._write_sp_report(use_enriched=False)

        return generated

    # ------------------------------------------------------------------
    # SQL Lineage Report
    # ------------------------------------------------------------------

    def _write_lineage_report(self) -> str:
        data = self._load("sql_lineage.json")
        ts   = datetime.now(timezone.utc).isoformat()

        lines = [
            "# SQL Lineage Report\n\n",
            f"_Generated: {ts}_\n\n",
            f"**Edges:** {data.get('edge_count', 0)} | "
            f"**Chains:** {data.get('chain_count', 0)} | "
            f"**Tables:** {data.get('table_count', 0)} | "
            f"**Views:** {data.get('view_count', 0)} | "
            f"**Procedures:** {data.get('proc_count', 0)}\n\n",
        ]

        # ETL flows
        etl = data.get("etl_flows", [])
        if etl:
            lines.append("## ETL-Like Flows (INSERT...SELECT Patterns)\n\n")
            lines.append("| Source | Target | Via | Confidence |\n")
            lines.append("|--------|--------|-----|------------|\n")
            for f in etl:
                lines.append(
                    f"| `{f['source']}` | `{f['target']}` "
                    f"| {f['via']} | {f.get('confidence','?')} |\n"
                )
            lines.append("\n")

        # Duplicate transformations
        dups = data.get("duplicate_transforms", [])
        if dups:
            lines.append("## Tables with Multiple Consumers (Potential Redundant Transformations)\n\n")
            for d in dups:
                lines.append(
                    f"- **{d['table']}**: consumed by {d['count']} components: "
                    f"{', '.join(d['readers'][:5])}\n"
                )
            lines.append("\n")

        # High-risk lineage chains
        chains = [c for c in data.get("lineage_chains", []) if c.get("risk_level") in ("HIGH", "MEDIUM")]
        if chains:
            lines.append("## High / Medium Risk Lineage Chains\n\n")
            for c in chains[:20]:
                path = " → ".join(c["path"])
                lines.append(
                    f"- `{c['risk_level']}` {path} "
                    f"(depth={c['depth']}, write={c['has_write']})\n"
                )
            lines.append("\n")

        # Table impact
        impact = sorted(
            data.get("table_impact", []),
            key=lambda x: -(len(x.get("read_by",[])) + len(x.get("written_by",[])))
        )
        if impact:
            lines.append("## Table Impact Summary\n\n")
            lines.append("| Table | Read By | Written By | FK Parents | FK Children |\n")
            lines.append("|-------|---------|------------|------------|-------------|\n")
            for t in impact[:30]:
                lines.append(
                    f"| **{t['table']}** "
                    f"| {len(t.get('read_by',[]))} "
                    f"| {len(t.get('written_by',[]))} "
                    f"| {len(t.get('fk_parents',[]))} "
                    f"| {len(t.get('fk_children',[]))} |\n"
                )
            lines.append("\n")

        # Lineage flow graph (text)
        edges = data.get("edges", [])
        fk_edges    = [e for e in edges if e["edge_type"] == "fk_reference"]
        view_edges  = [e for e in edges if e["edge_type"] == "view_reads"]
        proc_reads  = [e for e in edges if e["edge_type"] == "proc_reads"]
        proc_writes = [e for e in edges if e["edge_type"] == "proc_writes"]

        if view_edges:
            lines.append("## View → Source Table Dependencies\n\n")
            for e in view_edges[:30]:
                lines.append(f"- `VIEW:{e['target']}` reads from `{e['source']}`\n")
            lines.append("\n")

        if proc_writes:
            lines.append("## Stored Procedure Write Operations\n\n")
            for e in proc_writes[:30]:
                lines.append(f"- `{e['source']}` writes to `{e['target']}`\n")
            lines.append("\n")

        out = self.output_dir / "SQL_LINEAGE_REPORT.md"
        out.write_text("".join(lines), encoding="utf-8")
        print(f"  -> SQL_LINEAGE_REPORT.md ({len(''.join(lines)):,} chars)")
        return str(out)

    # ------------------------------------------------------------------
    # Stored Procedure Analysis Report
    # ------------------------------------------------------------------

    def _write_sp_report(self, use_enriched: bool = True) -> str:
        fname = "stored_procedures.json" if use_enriched else "stored_procedure_lineage.json"
        data  = self._load(fname)
        # Also load lineage for command/query/txn counts if using enriched
        lineage = self._load("stored_procedure_lineage.json") if use_enriched else data
        ts    = datetime.now(timezone.utc).isoformat()
        procs = data.get("procedures", [])

        lines = [
            "# Stored Procedure Analysis\n\n",
            f"_Generated: {ts}_\n\n",
            f"**Total Procedures:** {data.get('procedure_count', 0)} | "
            f"**Functions:** {data.get('function_count', lineage.get('query_procs', 0))} | "
            f"**Command:** {lineage.get('command_procs', 0)} | "
            f"**Query:** {lineage.get('query_procs', 0)} | "
            f"**With Dynamic SQL:** {lineage.get('with_dynamic_sql', 0)} | "
            f"**High Risk:** {lineage.get('high_risk', 0)} | "
            f"**With Transactions:** {lineage.get('with_transactions', 0)}\n\n",
        ]

        # Call graph (if enriched)
        call_graph = data.get("call_graph", [])
        if call_graph:
            lines.append("## Call Graph (Nested Procedure Dependencies)\n\n")
            lines.append(f"Max call depth: **{data.get('max_call_depth', 0)}**\n\n")
            lines.append("| Caller | Callees | Depth |\n")
            lines.append("|--------|---------|-------|\n")
            for cg in call_graph[:30]:
                callees = ", ".join(cg.get("callees", [])[:5])
                lines.append(f"| `{cg['caller']}` | {callees} | {cg.get('depth', 0)} |\n")
            lines.append("\n")

        # Tables touched across all procedures
        all_tables = data.get("tables_touched", [])
        if all_tables:
            lines.append(f"## Tables Touched by Stored Procedures ({len(all_tables)} total)\n\n")
            lines.append(", ".join(f"`{t}`" for t in sorted(all_tables)) + "\n\n")

        # Normalize proc fields (enriched vs lineage schema)
        def _get_crud(p: dict) -> str:
            ops = p.get("crud_operations") or list(p.get("crud_summary", {}).keys())
            return "|".join(sorted(ops))

        def _get_type(p: dict) -> str:
            return p.get("type") or p.get("proc_type", "?")

        def _get_inputs(p: dict) -> list:
            raw = p.get("inputs") or [{"name": x} for x in p.get("parameters_in", [])]
            return [x.get("name", str(x)) if isinstance(x, dict) else str(x) for x in raw]

        def _get_outputs(p: dict) -> list:
            raw = p.get("outputs") or [{"name": x} for x in p.get("parameters_out", [])]
            return [x.get("name", str(x)) if isinstance(x, dict) else str(x) for x in raw]

        # High-risk procedures first
        high_risk = [p for p in procs if p.get("dynamic_sql_risk") == "HIGH" or p.get("risk_flags")]
        if high_risk:
            lines.append("## High-Risk Stored Procedures\n\n")
            for p in high_risk[:20]:
                schema = p.get("schema", "dbo")
                name   = p.get("name", "?")
                lines.append(f"### `{schema}.{name}`\n\n")
                lines.append(f"- **Type:** {_get_type(p)}\n")
                lines.append(f"- **CRUD:** {_get_crud(p) or 'None'}\n")
                lines.append(f"- **Call Depth:** {p.get('call_depth', 0)}\n")
                lines.append(f"- **Tables Read:** {', '.join(p.get('tables_read', [])) or 'None'}\n")
                lines.append(f"- **Tables Written:** {', '.join(p.get('tables_written', [])) or 'None'}\n")
                inputs  = _get_inputs(p)
                outputs = _get_outputs(p)
                if inputs:
                    lines.append(f"- **Input Parameters:** {', '.join(inputs)}\n")
                if outputs:
                    lines.append(f"- **Output Parameters:** {', '.join(outputs)}\n")
                nested = p.get("nested_calls", [])
                if nested:
                    lines.append(f"- **Calls:** {', '.join(nested)}\n")
                result_sets = p.get("result_sets", [])
                if result_sets and result_sets[0].get("column") != "*":
                    cols = ", ".join(r.get("column","?") for r in result_sets[:8])
                    lines.append(f"- **Result Set Columns:** {cols}\n")
                if p.get("risk_flags"):
                    lines.append("- **Risk Flags:**\n")
                    for rf in p["risk_flags"]:
                        lines.append(f"  - {rf}\n")
                if p.get("business_rules"):
                    lines.append("- **Business Rules:**\n")
                    for br in p["business_rules"]:
                        lines.append(f"  - {br}\n")
                lines.append("\n")

        # Full procedure catalog
        lines.append("## All Stored Procedures\n\n")
        lines.append("| Name | Type | CRUD | Tables | Inputs | Outputs | Calls | Depth | Txn | DynSQL |\n")
        lines.append("|------|------|------|--------|--------|---------|-------|-------|-----|--------|\n")
        for p in procs:
            crud    = _get_crud(p)
            tables  = ", ".join((p.get("tables_read", []) + p.get("tables_written", []))[:3])
            inputs  = len(_get_inputs(p))
            outputs = len(_get_outputs(p))
            nested  = len(p.get("nested_calls", []))
            depth   = p.get("call_depth", 0)
            txn     = "✓" if p.get("has_transaction", p.get("transaction_depth", 0) > 0) else "—"
            dyn     = p.get("dynamic_sql_risk","NONE") if p.get("has_dynamic_sql") else "—"
            lines.append(
                f"| `{p.get('name','?')}` | {_get_type(p)} | {crud} "
                f"| {tables[:40]} | {inputs} | {outputs} | {nested} | {depth} | {txn} | {dyn} |\n"
            )
        lines.append("\n")

        out = self.output_dir / "STORED_PROCEDURE_ANALYSIS.md"
        out.write_text("".join(lines), encoding="utf-8")
        print(f"  -> STORED_PROCEDURE_ANALYSIS.md ({len(''.join(lines)):,} chars)")
        return str(out)

    def _load(self, fname: str) -> dict:
        path = self.extracted_dir / fname
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
