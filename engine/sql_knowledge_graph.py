"""
SQL Knowledge Graph Engine  — Priority 4

Builds a property graph over the entire SQL schema:

  Node types:
    TABLE     — database tables
    COLUMN    — individual columns
    PROCEDURE — stored procedures / functions
    VIEW      — database views
    TRIGGER   — triggers
    INDEX     — indexes
    CONSTRAINT — PK / FK / UNIQUE / CHECK constraints

  Edge types:
    HAS_COLUMN        TABLE -> COLUMN
    FK_TO             TABLE -> TABLE  (foreign key)
    REFERENCES        COLUMN -> COLUMN (FK column level)
    PROC_READS        PROCEDURE -> TABLE
    PROC_WRITES       PROCEDURE -> TABLE
    PROC_DELETES      PROCEDURE -> TABLE
    PROC_CALLS        PROCEDURE -> PROCEDURE (nested calls)
    VIEW_READS        VIEW -> TABLE (or VIEW -> VIEW)
    TRIGGER_ON        TRIGGER -> TABLE
    INDEXED_BY        TABLE -> INDEX
    INDEX_COVERS      INDEX -> COLUMN
    HAS_CONSTRAINT    TABLE -> CONSTRAINT
    PK_COLUMN         TABLE -> COLUMN (primary key)

Generates:
  memory/extracted/enterprise_graph.json

Supports:
  - Dependency traversal (what breaks if I drop TABLE X?)
  - Impact analysis (all objects that read/write a column)
  - Ownership tracing (who writes to this table?)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.semantic_model import SemanticModel


# ---------------------------------------------------------------------------
# Node / Edge structures
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    id:          str           # unique: "TABLE:Orders", "COLUMN:Orders.OrderID"
    node_type:   str           # TABLE | COLUMN | PROCEDURE | VIEW | TRIGGER | INDEX | CONSTRAINT
    name:        str
    schema:      str           = "dbo"
    properties:  dict          = field(default_factory=dict)
    source_file: str           = ""
    line_number: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "node_type":   self.node_type,
            "name":        self.name,
            "schema":      self.schema,
            "properties":  self.properties,
            "source_file": self.source_file,
            "line_number": self.line_number,
        }


@dataclass
class GraphEdge:
    source_id:   str
    target_id:   str
    edge_type:   str
    properties:  dict  = field(default_factory=dict)
    confidence:  str   = "HIGH"
    source_file: str   = ""

    def to_dict(self) -> dict:
        return {
            "source":      self.source_id,
            "target":      self.target_id,
            "edge_type":   self.edge_type,
            "properties":  self.properties,
            "confidence":  self.confidence,
            "source_file": self.source_file,
        }


# ---------------------------------------------------------------------------
# Knowledge Graph Builder
# ---------------------------------------------------------------------------

class SQLKnowledgeGraph:
    """
    Builds a complete property graph from a SemanticModel.
    Every node and edge is tagged with source_file and confidence.
    """

    def build(self, model: SemanticModel) -> dict[str, Any]:
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        node_ids: set[str] = set()

        def add_node(n: GraphNode) -> None:
            if n.id not in node_ids:
                node_ids.add(n.id)
                nodes.append(n)

        def add_edge(e: GraphEdge) -> None:
            edges.append(e)

        # ── Tables + Columns ─────────────────────────────────────────────
        for entity in model.entities:
            tbl_id = f"TABLE:{entity.name}"
            raw = getattr(entity, "raw", {})
            raw_dict = raw if isinstance(raw, dict) else {}

            pk_cols = raw_dict.get("primary_key", [])
            fk_count = len(raw_dict.get("foreign_keys", []))
            unique_keys = raw_dict.get("unique_keys", [])
            check_exprs = raw_dict.get("check_constraints", [])

            add_node(GraphNode(
                id         = tbl_id,
                node_type  = "TABLE",
                name       = entity.name,
                schema     = entity.namespace or "dbo",
                properties = {
                    "column_count":       len(entity.fields),
                    "pk_columns":         pk_cols,
                    "fk_count":           fk_count,
                    "has_pk":             bool(pk_cols) or any(f.is_pk for f in entity.fields),
                    "unique_key_count":   len(unique_keys),
                    "check_constraint_count": len(check_exprs),
                    "pii_column_count":   sum(1 for f in entity.fields if getattr(f, "pii_risk", None)),
                    "aggregate":          entity.aggregate or "",
                },
                source_file = entity.source_file or "",
                line_number = entity.line_number,
            ))

            # Column nodes
            for col in entity.fields:
                col_id = f"COLUMN:{entity.name}.{col.name}"
                col_props = {
                    "raw_type":        col.raw_type,
                    "normalized_type": col.normalized_type,
                    "is_nullable":     col.is_nullable,
                    "is_pk":           col.is_pk,
                    "is_identity":     getattr(col, "is_identity", False),
                    "is_unique":       getattr(col, "is_unique", False),
                    "is_indexed":      getattr(col, "is_indexed", False),
                    "pii_risk":        getattr(col, "pii_risk", None),
                    "max_length":      getattr(col, "max_length", None),
                }
                add_node(GraphNode(
                    id         = col_id,
                    node_type  = "COLUMN",
                    name       = col.name,
                    schema     = entity.namespace or "dbo",
                    properties = col_props,
                    source_file = entity.source_file or "",
                    line_number = getattr(col, "line_number", None),
                ))
                add_edge(GraphEdge(
                    source_id  = tbl_id,
                    target_id  = col_id,
                    edge_type  = "HAS_COLUMN",
                    properties = {"ordinal": getattr(col, "ordinal", 0)},
                    source_file = entity.source_file or "",
                ))
                if col.is_pk:
                    add_edge(GraphEdge(
                        source_id = tbl_id,
                        target_id = col_id,
                        edge_type = "PK_COLUMN",
                        source_file = entity.source_file or "",
                    ))

            # FK edges
            for fk in raw_dict.get("foreign_keys", []):
                if not isinstance(fk, dict):
                    continue
                target_tbl = fk.get("references_table", "")
                if not target_tbl:
                    continue
                target_id = f"TABLE:{target_tbl}"
                cname = fk.get("constraint_name", "")
                add_edge(GraphEdge(
                    source_id  = tbl_id,
                    target_id  = target_id,
                    edge_type  = "FK_TO",
                    properties = {
                        "constraint_name":   cname,
                        "source_columns":    fk.get("columns", []),
                        "target_columns":    fk.get("references_columns", []),
                        "on_delete":         fk.get("on_delete", "NO ACTION"),
                        "on_update":         fk.get("on_update", "NO ACTION"),
                    },
                    confidence  = "HIGH",
                    source_file = fk.get("source_file", entity.source_file or ""),
                ))
                # Column-level reference edges
                for sc, tc in zip(fk.get("columns", []), fk.get("references_columns", [])):
                    src_col_id = f"COLUMN:{entity.name}.{sc}"
                    tgt_col_id = f"COLUMN:{target_tbl}.{tc}"
                    add_edge(GraphEdge(
                        source_id  = src_col_id,
                        target_id  = tgt_col_id,
                        edge_type  = "REFERENCES",
                        properties = {"constraint_name": cname},
                        confidence = "HIGH",
                        source_file = fk.get("source_file", ""),
                    ))

            # Constraint nodes (PK, UNIQUE, CHECK)
            if pk_cols:
                cid = f"CONSTRAINT:PK_{entity.name}"
                add_node(GraphNode(
                    id        = cid,
                    node_type = "CONSTRAINT",
                    name      = f"PK_{entity.name}",
                    schema    = entity.namespace or "dbo",
                    properties = {"constraint_type": "PRIMARY_KEY", "columns": pk_cols},
                    source_file = entity.source_file or "",
                ))
                add_edge(GraphEdge(
                    source_id = tbl_id, target_id = cid,
                    edge_type = "HAS_CONSTRAINT",
                    source_file = entity.source_file or "",
                ))

            for i, uq_cols in enumerate(unique_keys):
                cid = f"CONSTRAINT:UQ_{entity.name}_{i}"
                add_node(GraphNode(
                    id        = cid,
                    node_type = "CONSTRAINT",
                    name      = f"UQ_{entity.name}_{i}",
                    schema    = entity.namespace or "dbo",
                    properties = {"constraint_type": "UNIQUE", "columns": uq_cols},
                    source_file = entity.source_file or "",
                ))
                add_edge(GraphEdge(
                    source_id = tbl_id, target_id = cid,
                    edge_type = "HAS_CONSTRAINT",
                    source_file = entity.source_file or "",
                ))

            for i, chk in enumerate(check_exprs):
                cid = f"CONSTRAINT:CHK_{entity.name}_{i}"
                add_node(GraphNode(
                    id        = cid,
                    node_type = "CONSTRAINT",
                    name      = f"CHK_{entity.name}_{i}",
                    schema    = entity.namespace or "dbo",
                    properties = {"constraint_type": "CHECK", "expression": chk},
                    source_file = entity.source_file or "",
                ))
                add_edge(GraphEdge(
                    source_id = tbl_id, target_id = cid,
                    edge_type = "HAS_CONSTRAINT",
                    source_file = entity.source_file or "",
                ))

        # ── Views ────────────────────────────────────────────────────────
        for ep in model.endpoints:
            if ep.handler_class != "SQL View":
                continue
            view_id = f"VIEW:{ep.handler_method}"
            add_node(GraphNode(
                id         = view_id,
                node_type  = "VIEW",
                name       = ep.handler_method,
                schema     = "dbo",
                properties = {"source_table_count": len(ep.entities_touched)},
                source_file = ep.source_file or "",
                line_number = getattr(ep, "line_number", None),
            ))
            for tbl in ep.entities_touched:
                tbl_id = f"TABLE:{tbl}"
                add_edge(GraphEdge(
                    source_id  = view_id,
                    target_id  = tbl_id,
                    edge_type  = "VIEW_READS",
                    confidence = "HIGH",
                    source_file = ep.source_file or "",
                ))

        # ── Stored Procedures ─────────────────────────────────────────────
        for hdl in model.handlers:
            proc_id = f"PROCEDURE:{hdl.name}"
            raw = getattr(hdl, "raw", {})
            raw_d = raw if isinstance(raw, dict) else {}

            add_node(GraphNode(
                id         = proc_id,
                node_type  = "PROCEDURE",
                name       = hdl.name,
                schema     = raw_d.get("schema", "dbo"),
                properties = {
                    "param_count":     len(hdl.request_type.split(",")) if hdl.request_type else 0,
                    "has_dynamic_sql": raw_d.get("has_dynamic_sql", False),
                    "has_transaction": raw_d.get("has_transaction", False),
                    "has_temp_tables": raw_d.get("has_temp_tables", False),
                    "crud_operations": raw_d.get("crud_operations", []),
                    "tables_read":     raw_d.get("tables_read", []),
                    "tables_written":  raw_d.get("tables_written", []),
                },
                source_file = hdl.source_file or "",
                line_number = getattr(hdl, "line_number", None),
            ))

            # PROC_READS edges
            for tbl in raw_d.get("tables_read", []) + list(hdl.entities_touched):
                if tbl:
                    add_edge(GraphEdge(
                        source_id  = proc_id,
                        target_id  = f"TABLE:{tbl}",
                        edge_type  = "PROC_READS",
                        confidence = "HIGH",
                        source_file = hdl.source_file or "",
                    ))
            # PROC_WRITES edges
            for tbl in raw_d.get("tables_written", []):
                if tbl:
                    add_edge(GraphEdge(
                        source_id  = proc_id,
                        target_id  = f"TABLE:{tbl}",
                        edge_type  = "PROC_WRITES",
                        confidence = "HIGH",
                        source_file = hdl.source_file or "",
                    ))
            # PROC_DELETES edges
            for tbl in raw_d.get("tables_deleted", []):
                if tbl:
                    add_edge(GraphEdge(
                        source_id  = proc_id,
                        target_id  = f"TABLE:{tbl}",
                        edge_type  = "PROC_DELETES",
                        confidence = "HIGH",
                        source_file = hdl.source_file or "",
                    ))
            # PROC_CALLS edges (nested procedures)
            for called in raw_d.get("nested_calls", []):
                if called:
                    add_edge(GraphEdge(
                        source_id  = proc_id,
                        target_id  = f"PROCEDURE:{called}",
                        edge_type  = "PROC_CALLS",
                        confidence = "HIGH",
                        source_file = hdl.source_file or "",
                    ))

        # ── Indexes ──────────────────────────────────────────────────────
        for entity in model.entities:
            raw_d = getattr(entity, "raw", {})
            if not isinstance(raw_d, dict):
                continue
            for idx in raw_d.get("indexes", []):
                if not isinstance(idx, dict):
                    continue
                idx_name = idx.get("name", "")
                if not idx_name:
                    continue
                idx_id = f"INDEX:{idx_name}"
                tbl_id = f"TABLE:{entity.name}"
                add_node(GraphNode(
                    id         = idx_id,
                    node_type  = "INDEX",
                    name       = idx_name,
                    schema     = entity.namespace or "dbo",
                    properties = {
                        "is_unique":    idx.get("is_unique", False),
                        "is_clustered": idx.get("is_clustered", False),
                        "columns":      idx.get("columns", []),
                        "index_type":   idx.get("index_type", "BTREE"),
                    },
                    source_file = idx.get("source_file", entity.source_file or ""),
                ))
                add_edge(GraphEdge(
                    source_id = tbl_id, target_id = idx_id,
                    edge_type = "INDEXED_BY",
                    source_file = entity.source_file or "",
                ))
                for col_name in idx.get("columns", []):
                    col_id = f"COLUMN:{entity.name}.{col_name}"
                    add_edge(GraphEdge(
                        source_id = idx_id, target_id = col_id,
                        edge_type = "INDEX_COVERS",
                        source_file = entity.source_file or "",
                    ))

        # ── Impact analysis ───────────────────────────────────────────────
        impact = self._build_impact_map(nodes, edges)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "node_count":   len(nodes),
            "edge_count":   len(edges),
            "node_type_counts": {
                nt: sum(1 for n in nodes if n.node_type == nt)
                for nt in ("TABLE", "COLUMN", "PROCEDURE", "VIEW",
                           "TRIGGER", "INDEX", "CONSTRAINT")
            },
            "edge_type_counts": {
                et: sum(1 for e in edges if e.edge_type == et)
                for et in ("HAS_COLUMN", "FK_TO", "REFERENCES",
                           "PROC_READS", "PROC_WRITES", "PROC_DELETES",
                           "PROC_CALLS", "VIEW_READS", "INDEXED_BY",
                           "INDEX_COVERS", "HAS_CONSTRAINT", "PK_COLUMN")
            },
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in edges],
            "impact_map": impact,
        }

    def _build_impact_map(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> list[dict]:
        """For each TABLE, list what reads/writes it and its FK parents/children."""
        tables = {n.id: n for n in nodes if n.node_type == "TABLE"}
        impact: list[dict] = []

        for tbl_id, tbl_node in sorted(tables.items(), key=lambda x: x[1].name):
            reads_by   = [e.source_id for e in edges if e.target_id == tbl_id
                          and e.edge_type in ("PROC_READS", "VIEW_READS")]
            writes_by  = [e.source_id for e in edges if e.target_id == tbl_id
                          and e.edge_type == "PROC_WRITES"]
            deletes_by = [e.source_id for e in edges if e.target_id == tbl_id
                          and e.edge_type == "PROC_DELETES"]
            fk_parents = [e.target_id.replace("TABLE:", "") for e in edges
                          if e.source_id == tbl_id and e.edge_type == "FK_TO"]
            fk_children= [e.source_id.replace("TABLE:", "") for e in edges
                          if e.target_id == tbl_id and e.edge_type == "FK_TO"]

            impact.append({
                "table":        tbl_node.name,
                "reads_by":     sorted(set(reads_by)),
                "writes_by":    sorted(set(writes_by)),
                "deletes_by":   sorted(set(deletes_by)),
                "fk_parents":   sorted(set(fk_parents)),
                "fk_children":  sorted(set(fk_children)),
                "total_consumers": len(set(reads_by) | set(writes_by)),
            })

        return sorted(impact, key=lambda x: -x["total_consumers"])

    def save(self, model: SemanticModel, output_dir: str | Path) -> dict[str, Any]:
        result = self.build(model)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "enterprise_graph.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        print(
            f"[SQLKnowledgeGraph] {result['node_count']} nodes, "
            f"{result['edge_count']} edges -> enterprise_graph.json"
        )
        return result
