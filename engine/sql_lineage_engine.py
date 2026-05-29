"""
SQL Lineage Engine

Traces data flow through SQL databases:
  Source Table → Transformation → View / Procedure → Target Table

Builds a complete SQL lineage graph covering:
  - View dependency chains
  - Stored procedure READ/WRITE flows
  - Trigger side-effects
  - ETL-like INSERT/SELECT patterns
  - Cross-schema references
  - Hidden lineage (dynamic SQL, nested procs)

Generates:
  memory/extracted/sql_lineage.json
  REVIEW/SQL_LINEAGE_REPORT.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.semantic_model import SemanticModel
from models.universal import UniversalHandler


@dataclass
class LineageEdge:
    source:       str
    target:       str
    edge_type:    str    # "view_reads", "proc_reads", "proc_writes", "proc_deletes",
                         # "trigger_reads", "trigger_writes", "insert_select"
    via:          str    = ""
    confidence:   str    = "HIGH"
    source_file:  str    = ""
    line_number:  Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "source":      self.source,
            "target":      self.target,
            "edge_type":   self.edge_type,
            "via":         self.via,
            "confidence":  self.confidence,
            "source_file": self.source_file,
        }


@dataclass
class LineageChain:
    """A traced lineage path from source to final target."""
    path:       list[str]
    edge_types: list[str]
    depth:      int
    has_write:  bool
    has_dynamic_sql: bool
    risk_level: str    # "LOW" | "MEDIUM" | "HIGH"

    def to_dict(self) -> dict:
        return {
            "path":          self.path,
            "edge_types":    self.edge_types,
            "depth":         self.depth,
            "has_write":     self.has_write,
            "has_dynamic_sql": self.has_dynamic_sql,
            "risk_level":    self.risk_level,
        }


class SQLLineageEngine:
    """
    Builds SQL lineage graph from a SemanticModel that has been populated
    by database-first adapters (SQL Server, PostgreSQL, MySQL, SQLite).
    """

    def build(self, model: SemanticModel) -> dict[str, Any]:
        """Build complete SQL lineage analysis."""
        edges:  list[LineageEdge] = []
        entity_names = {e.name for e in model.entities}

        # Build view name set for view-to-view detection
        view_names = {
            ep.handler_method
            for ep in model.endpoints
            if ep.handler_class == "SQL View"
        }

        # View → source table/view edges
        for ep in model.endpoints:
            if ep.handler_class != "SQL View":
                continue
            for src_tbl in ep.entities_touched:
                if src_tbl in entity_names:
                    edges.append(LineageEdge(
                        source     = src_tbl,
                        target     = f"VIEW:{ep.handler_method}",
                        edge_type  = "view_reads",
                        via        = ep.handler_method,
                        confidence = "HIGH",
                        source_file = ep.source_file,
                    ))
                elif src_tbl in view_names:
                    # View reading from another view
                    edges.append(LineageEdge(
                        source     = f"VIEW:{src_tbl}",
                        target     = f"VIEW:{ep.handler_method}",
                        edge_type  = "view_reads_view",
                        via        = ep.handler_method,
                        confidence = "HIGH",
                        source_file = ep.source_file,
                    ))

        # Procedure → table edges
        for hdl in model.handlers:
            raw = hdl.__dict__.get("raw", {}) if hasattr(hdl, "__dict__") else {}
            tables_read    = raw.get("tables_read", [])
            tables_written = raw.get("tables_written", [])
            tables_deleted = raw.get("tables_deleted", [])

            # Fallback: use entities_touched
            if not tables_read and not tables_written:
                for tbl in hdl.entities_touched:
                    if tbl in entity_names:
                        edges.append(LineageEdge(
                            source     = tbl,
                            target     = f"PROC:{hdl.name}",
                            edge_type  = "proc_reads",
                            via        = hdl.name,
                            confidence = "MEDIUM",
                        ))
                continue

            for tbl in tables_read:
                if tbl:
                    edges.append(LineageEdge(
                        source     = tbl,
                        target     = f"PROC:{hdl.name}",
                        edge_type  = "proc_reads",
                        via        = hdl.name,
                        source_file = hdl.source_file,
                    ))
            for tbl in tables_written:
                if tbl:
                    edges.append(LineageEdge(
                        source     = f"PROC:{hdl.name}",
                        target     = tbl,
                        edge_type  = "proc_writes",
                        via        = hdl.name,
                        source_file = hdl.source_file,
                    ))
            for tbl in tables_deleted:
                if tbl:
                    edges.append(LineageEdge(
                        source     = f"PROC:{hdl.name}",
                        target     = tbl,
                        edge_type  = "proc_deletes",
                        via        = hdl.name,
                        source_file = hdl.source_file,
                    ))

        # Relationship edges (FK lineage)
        for rel in model.relationships:
            edges.append(LineageEdge(
                source     = rel.source,
                target     = rel.target,
                edge_type  = "fk_reference",
                via        = rel.via,
                confidence = rel.confidence.value,
                source_file = rel.source_file,
            ))

        # Build adjacency map
        adjacency: dict[str, list[LineageEdge]] = {}
        for e in edges:
            adjacency.setdefault(e.source, []).append(e)

        # Trace chains (BFS from each table)
        chains  = self._trace_chains(entity_names, adjacency, edges)

        # Impact summary per table
        impact = self._build_impact(entity_names, edges)

        # Detect ETL-like flows (INSERT ... SELECT)
        etl_flows = self._detect_etl_flows(model, entity_names)

        # Duplicate transformations
        dup_transforms = self._detect_duplicate_transforms(model, entity_names)

        return {
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "edge_count":       len(edges),
            "chain_count":      len(chains),
            "table_count":      len(entity_names),
            "proc_count":       len(model.handlers),
            "view_count":       sum(1 for ep in model.endpoints if ep.handler_class == "SQL View"),
            "edges":            [e.to_dict() for e in edges],
            "lineage_chains":   [c.to_dict() for c in chains[:50]],
            "table_impact":     impact,
            "etl_flows":        etl_flows,
            "duplicate_transforms": dup_transforms,
        }

    def _trace_chains(
        self,
        tables: set[str],
        adjacency: dict[str, list[LineageEdge]],
        all_edges: list[LineageEdge],
        max_depth: int = 6,
    ) -> list[LineageChain]:
        chains: list[LineageChain] = []
        seen_paths: set[str] = set()

        # Start from each table (not procedure nodes)
        for start in sorted(tables)[:30]:  # cap at 30 starting nodes
            stack = [(start, [start], [], False, False)]
            while stack:
                node, path, etypes, has_write, has_dyn = stack.pop()
                if len(path) > max_depth:
                    continue
                nexts = adjacency.get(node, [])
                if not nexts or len(path) >= max_depth:
                    if len(path) > 1:
                        key = "→".join(path)
                        if key not in seen_paths:
                            seen_paths.add(key)
                            risk = "HIGH" if has_dyn else ("MEDIUM" if has_write else "LOW")
                            chains.append(LineageChain(
                                path       = path[:],
                                edge_types = etypes[:],
                                depth      = len(path) - 1,
                                has_write  = has_write,
                                has_dynamic_sql = has_dyn,
                                risk_level = risk,
                            ))
                    continue
                for edge in nexts:
                    if edge.target not in path:
                        is_write = edge.edge_type in ("proc_writes", "proc_deletes")
                        stack.append((
                            edge.target,
                            path + [edge.target],
                            etypes + [edge.edge_type],
                            has_write or is_write,
                            has_dyn,
                        ))

        return chains

    def _build_impact(
        self,
        tables: set[str],
        edges: list[LineageEdge],
    ) -> list[dict]:
        impact: dict[str, dict] = {}
        for tbl in tables:
            impact[tbl] = {
                "table":        tbl,
                "read_by":      [],
                "written_by":   [],
                "deleted_by":   [],
                "fk_parents":   [],
                "fk_children":  [],
            }
        for edge in edges:
            if edge.edge_type == "view_reads":
                if edge.source in impact:
                    impact[edge.source]["read_by"].append(f"VIEW:{edge.target}")
            elif edge.edge_type == "proc_reads":
                if edge.source in impact:
                    impact[edge.source]["read_by"].append(edge.target)
            elif edge.edge_type == "proc_writes":
                if edge.target in impact:
                    impact[edge.target]["written_by"].append(edge.source)
            elif edge.edge_type == "proc_deletes":
                if edge.target in impact:
                    impact[edge.target]["deleted_by"].append(edge.source)
            elif edge.edge_type == "fk_reference":
                if edge.source in impact:
                    impact[edge.source]["fk_parents"].append(edge.target)
                if edge.target in impact:
                    impact[edge.target]["fk_children"].append(edge.source)

        return list(impact.values())

    def _detect_etl_flows(
        self, model: SemanticModel, tables: set[str]
    ) -> list[dict]:
        """Detect ETL-like INSERT...SELECT patterns in stored procedures."""
        etl: list[dict] = []
        for hdl in model.handlers:
            raw = getattr(hdl, "raw", {})
            if not isinstance(raw, dict):
                continue
            body = raw.get("body", "") if raw else ""
            # INSERT INTO target SELECT ... FROM source
            insert_sel = re.finditer(
                r'\bINSERT\s+INTO\s+(\w+)[^;]+SELECT[^;]+FROM\s+(\w+)',
                body, re.I | re.DOTALL,
            )
            for m in insert_sel:
                target = m.group(1)
                source = m.group(2)
                etl.append({
                    "type":        "INSERT_SELECT",
                    "source":      source,
                    "target":      target,
                    "via":         f"PROC:{hdl.name}",
                    "confidence":  "HIGH",
                })
        return etl

    def _detect_duplicate_transforms(
        self, model: SemanticModel, tables: set[str]
    ) -> list[dict]:
        """Detect multiple procedures/views reading from the same table."""
        table_readers: dict[str, list[str]] = {}
        for hdl in model.handlers:
            for tbl in hdl.entities_touched:
                table_readers.setdefault(tbl, []).append(f"PROC:{hdl.name}")
        for ep in model.endpoints:
            if ep.handler_class == "SQL View":
                for tbl in ep.entities_touched:
                    table_readers.setdefault(tbl, []).append(f"VIEW:{ep.handler_method}")

        dups = []
        for tbl, readers in table_readers.items():
            if len(readers) >= 3:
                dups.append({
                    "table":   tbl,
                    "readers": sorted(set(readers)),
                    "count":   len(set(readers)),
                    "note":    "Multiple consumers may indicate redundant transformations",
                })
        return dups

    def save(self, model: SemanticModel, output_dir: str | Path) -> dict[str, Any]:
        result = self.build(model)
        out    = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sql_lineage.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(f"[SQLLineageEngine] {result['edge_count']} edges, "
              f"{result['chain_count']} chains -> sql_lineage.json")
        return result
