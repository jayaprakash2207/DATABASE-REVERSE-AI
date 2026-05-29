"""
Stored Procedure Semantic Analyzer

Deep analysis of SQL stored procedures:
  - Input/output parameter classification
  - CRUD operation detection per table
  - Join graph (which tables join which)
  - Transaction boundary detection
  - Dynamic SQL risk scoring
  - Nested procedure call graph with depth levels
  - Temporary table lifecycle
  - Business rule detection (constraint enforcement, conditional writes)
  - Hidden write operations (INSERT inside IF blocks, triggers called)
  - Update/Delete risk scoring (missing WHERE, unbounded updates)
  - Result set column extraction from SELECT statements
  - Table dependency graph

Outputs:
  stored_procedure_lineage.json  (deep semantic format)
  stored_procedures.json         (enriched format for M3 agent / skill)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from adapters.sql_common.ddl_parser import DDLProcedure, DDLParseResult


@dataclass
class SPLineageNode:
    """Single stored procedure in the lineage graph."""
    name:              str
    schema:            str
    proc_type:         str
    parameters_in:     list[str]          = field(default_factory=list)
    parameters_out:    list[str]          = field(default_factory=list)
    tables_read:       list[str]          = field(default_factory=list)
    tables_written:    list[str]          = field(default_factory=list)
    tables_deleted:    list[str]          = field(default_factory=list)
    joins:             list[dict]         = field(default_factory=list)
    transaction_depth: int                = 0
    has_dynamic_sql:   bool               = False
    has_temp_tables:   bool               = False
    dynamic_sql_risk:  str                = "NONE"  # NONE | LOW | MEDIUM | HIGH
    nested_calls:      list[str]          = field(default_factory=list)
    call_depth:        int                = 0       # depth in call graph (0 = root)
    business_rules:    list[str]          = field(default_factory=list)
    risk_flags:        list[str]          = field(default_factory=list)
    crud_summary:      dict               = field(default_factory=dict)
    result_sets:       list[dict]         = field(default_factory=list)
    table_dependencies: list[str]         = field(default_factory=list)
    source_file:       str                = ""
    line_number:       Optional[int]      = None

    def to_dict(self) -> dict:
        return {
            "name":              self.name,
            "schema":            self.schema,
            "proc_type":         self.proc_type,
            "parameters_in":     self.parameters_in,
            "parameters_out":    self.parameters_out,
            "tables_read":       self.tables_read,
            "tables_written":    self.tables_written,
            "tables_deleted":    self.tables_deleted,
            "table_dependencies": self.table_dependencies,
            "joins":             self.joins,
            "transaction_depth": self.transaction_depth,
            "has_dynamic_sql":   self.has_dynamic_sql,
            "has_temp_tables":   self.has_temp_tables,
            "dynamic_sql_risk":  self.dynamic_sql_risk,
            "nested_calls":      self.nested_calls,
            "call_depth":        self.call_depth,
            "business_rules":    self.business_rules,
            "risk_flags":        self.risk_flags,
            "crud_summary":      self.crud_summary,
            "result_sets":       self.result_sets,
            "source_file":       self.source_file,
            "line_number":       self.line_number,
        }


class SPAnalyzer:
    """
    Performs deep semantic analysis on stored procedures and functions.
    """

    def analyze_all(
        self, ddl_result: DDLParseResult
    ) -> list[SPLineageNode]:
        """Analyze all procedures and functions in a DDL parse result."""
        nodes: list[SPLineageNode] = []
        for proc in ddl_result.procedures + ddl_result.functions:
            nodes.append(self.analyze(proc))
        return nodes

    def analyze(self, proc: DDLProcedure) -> SPLineageNode:
        node = SPLineageNode(
            name        = proc.name,
            schema      = proc.schema,
            proc_type   = proc.proc_type,
            source_file = proc.source_file,
            line_number = proc.line_number,
        )

        body = proc.body

        # Parameters
        node.parameters_in  = [p.name for p in proc.parameters if p.direction in ("IN", "INOUT")]
        node.parameters_out = [p.name for p in proc.parameters if p.direction in ("OUT", "INOUT")]

        # Basic CRUD from DDLProcedure (already done in parser)
        node.tables_read    = proc.tables_read
        node.tables_written = proc.tables_written
        node.tables_deleted = proc.tables_deleted
        node.nested_calls   = proc.nested_calls
        node.has_dynamic_sql = proc.has_dynamic_sql
        node.has_temp_tables = proc.has_temp_tables

        # CRUD summary
        node.crud_summary = {op: True for op in proc.crud_operations}

        # All table dependencies (read + write + delete, deduplicated)
        all_tbls = list(dict.fromkeys(
            proc.tables_read + proc.tables_written + proc.tables_deleted
        ))
        node.table_dependencies = [t for t in all_tbls if t]

        # Result set columns (from SELECT list)
        node.result_sets = self._extract_result_sets(body)

        # Deep join analysis
        node.joins = self._extract_joins(body)

        # Transaction depth
        node.transaction_depth = self._count_transaction_depth(body)

        # Dynamic SQL risk
        node.dynamic_sql_risk = self._score_dynamic_sql(body)

        # Business rules (conditional writes, validations)
        node.business_rules = self._detect_business_rules(body)

        # Risk flags
        node.risk_flags = self._detect_risks(body, node)

        return node

    def _extract_result_sets(self, body: str) -> list[dict]:
        """Extract projected columns from the first SELECT statement (result set shape)."""
        result_cols: list[dict] = []
        # Find SELECT ... FROM blocks, skip those preceded by INSERT INTO
        sel_re = re.compile(
            r'\bSELECT\s+(DISTINCT\s+|TOP\s+\d+\s+)?([\s\S]+?)\s+FROM\b',
            re.IGNORECASE,
        )
        m = None
        for candidate in sel_re.finditer(body):
            preceding = body[max(0, candidate.start() - 60): candidate.start()]
            if re.search(r'\bINSERT\b', preceding, re.I):
                continue
            m = candidate
            break
        if not m:
            return result_cols
        col_list = m.group(2).strip()
        if col_list.startswith("*"):
            return [{"column": "*", "alias": None, "source_table": None}]
        # Split by comma (handle nested parens crudely by splitting at top-level commas)
        depth = 0
        current: list[str] = []
        buf = ""
        for ch in col_list:
            if ch == "(":
                depth += 1
                buf += ch
            elif ch == ")":
                depth -= 1
                buf += ch
            elif ch == "," and depth == 0:
                current.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            current.append(buf.strip())
        for col_expr in current[:20]:  # cap at 20 columns
            # Alias: last word after AS, or last word if table.col pattern
            alias_m = re.search(r'\bAS\s+(\w+)\s*$', col_expr, re.I)
            alias = alias_m.group(1) if alias_m else None
            tbl_col = re.match(r'(?:\[?(\w+)\]?\.)?\[?(\w+)\]?', col_expr)
            src_tbl = tbl_col.group(1) if tbl_col else None
            col_name = tbl_col.group(2) if tbl_col else col_expr[:50]
            if alias:
                col_name = alias
            result_cols.append({
                "column":       col_name,
                "alias":        alias,
                "source_table": src_tbl,
            })
        return result_cols

    def _extract_joins(self, body: str) -> list[dict]:
        """Extract JOIN relationships between tables."""
        joins: list[dict] = []
        join_re = re.compile(
            r'\b((?:LEFT|RIGHT|FULL|INNER|CROSS|OUTER)\s+(?:OUTER\s+)?JOIN|JOIN)\s+'
            r'(?:\[?(\w+)\]?\.)?(?:\[?(\w+)\]?)'
            r'\s+(?:AS\s+\w+\s+)?ON\s+(.+?)(?=\b(?:LEFT|RIGHT|FULL|INNER|CROSS|JOIN|WHERE|GROUP|ORDER|HAVING|UNION|$))',
            re.IGNORECASE | re.DOTALL,
        )
        for m in join_re.finditer(body):
            join_type = m.group(1).strip().upper()
            table     = m.group(3) or m.group(2) or "?"
            condition = re.sub(r'\s+', ' ', m.group(4).strip())[:200]
            joins.append({
                "join_type": join_type,
                "table":     table,
                "condition": condition,
            })
        return joins

    def _count_transaction_depth(self, body: str) -> int:
        begins = len(re.findall(r'\bBEGIN\s+(?:TRAN|TRANSACTION)\b', body, re.I))
        commits = len(re.findall(r'\bCOMMIT\b', body, re.I))
        rollbacks = len(re.findall(r'\bROLLBACK\b', body, re.I))
        if begins > 0:
            return begins
        return 0

    def _score_dynamic_sql(self, body: str) -> str:
        if not re.search(r'\bEXEC\s*\(|\bSP_EXECUTESQL\b|EXECUTE\s*\(', body, re.I):
            return "NONE"
        # User input concatenation = HIGH risk
        if re.search(r'@\w+\s*\+|N?\'\s*\+\s*@', body, re.I):
            return "HIGH"
        # sp_executesql with parameters = LOW risk
        if re.search(r'\bSP_EXECUTESQL\b', body, re.I):
            return "LOW"
        return "MEDIUM"

    def _detect_business_rules(self, body: str) -> list[str]:
        rules: list[str] = []

        # IF/ELSE conditional writes
        if re.search(r'\bIF\s*\(.+\)\s*(?:BEGIN\s+)?(?:INSERT|UPDATE|DELETE)', body, re.I | re.DOTALL):
            rules.append("Conditional write: INSERT/UPDATE/DELETE inside IF block")

        # Raise error / throw (validation enforcement)
        if re.search(r'\bRAISERROR\b|\bTHROW\b', body, re.I):
            rules.append("Validation enforcement: RAISERROR/THROW present")

        # EXISTS checks before write
        if re.search(r'\bIF\s+(?:NOT\s+)?EXISTS\s*\(\s*SELECT', body, re.I):
            rules.append("Existence check before write operation")

        # Row count checks (@@ROWCOUNT)
        if re.search(r'@@ROWCOUNT', body, re.I):
            rules.append("Row count validation (@@ROWCOUNT)")

        # Return codes
        if re.search(r'\bRETURN\s+\-?\d+', body):
            rules.append("Return code pattern (status/error codes)")

        # Cursor usage
        if re.search(r'\bDECLARE\s+\w+\s+CURSOR\b', body, re.I):
            rules.append("Cursor-based row-by-row processing")

        return rules

    def _detect_risks(self, body: str, node: SPLineageNode) -> list[str]:
        flags: list[str] = []

        # Unbounded UPDATE (no WHERE)
        upd_re = re.compile(r'\bUPDATE\s+\w+\s+SET\b.{0,500}', re.I | re.DOTALL)
        for m in upd_re.finditer(body):
            segment = m.group(0)
            if not re.search(r'\bWHERE\b', segment, re.I):
                flags.append(f"RISK: UPDATE without WHERE clause on table segment")
                break

        # Unbounded DELETE
        del_re = re.compile(r'\bDELETE\s+(?:FROM\s+)?\w+(?!\s+WHERE)', re.I)
        for m in del_re.finditer(body):
            # Check if WHERE appears shortly after
            after = body[m.start():m.start()+300]
            if not re.search(r'\bWHERE\b', after, re.I):
                flags.append("RISK: DELETE without WHERE clause")
                break

        # Dynamic SQL with concatenation (injection risk)
        if node.dynamic_sql_risk == "HIGH":
            flags.append("RISK: Dynamic SQL with string concatenation (SQL injection vector)")

        # Deeply nested transactions
        if node.transaction_depth > 2:
            flags.append(f"RISK: Nested transaction depth {node.transaction_depth} (deadlock risk)")

        # Cursor-based processing on large tables
        if any("Cursor" in r for r in node.business_rules):
            flags.append("PERF: Cursor-based processing (consider set-based alternative)")

        # Missing error handling
        if node.transaction_depth > 0 and not re.search(r'\bBEGIN\s+TRY\b|\bROLLBACK\b', body, re.I):
            flags.append("RISK: Transaction without TRY/CATCH error handling")

        return flags

    # ------------------------------------------------------------------
    # Call graph depth assignment
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_call_depths(nodes: list["SPLineageNode"]) -> None:
        """BFS to assign call_depth to each node (0 = called by nobody)."""
        name_map: dict[str, SPLineageNode] = {n.name.lower(): n for n in nodes}
        # Build caller sets
        callee_set: set[str] = set()
        for n in nodes:
            for callee in n.nested_calls:
                callee_set.add(callee.lower())
        # Roots: procs that are not called by anyone else
        roots = [n for n in nodes if n.name.lower() not in callee_set]
        visited: set[str] = set()
        queue: list[tuple[SPLineageNode, int]] = [(r, 0) for r in roots]
        while queue:
            node, depth = queue.pop(0)
            key = node.name.lower()
            if key in visited:
                continue
            visited.add(key)
            node.call_depth = depth
            for callee_name in node.nested_calls:
                child = name_map.get(callee_name.lower())
                if child and child.name.lower() not in visited:
                    queue.append((child, depth + 1))
        # Any remaining (cycles / disconnected) keep depth=0

    def save(
        self,
        nodes: list[SPLineageNode],
        output_dir: str | Path,
    ) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Assign call graph depths
        self._assign_call_depths(nodes)

        # ── stored_procedure_lineage.json (existing deep format) ──────────
        lineage_data = {
            "procedure_count":   len(nodes),
            "command_procs":     sum(1 for n in nodes if "INSERT" in n.crud_summary
                                     or "UPDATE" in n.crud_summary
                                     or "DELETE" in n.crud_summary),
            "query_procs":       sum(1 for n in nodes if n.crud_summary.get("SELECT") and
                                     not any(k in n.crud_summary for k in ("INSERT","UPDATE","DELETE"))),
            "with_dynamic_sql":  sum(1 for n in nodes if n.has_dynamic_sql),
            "high_risk":         sum(1 for n in nodes if n.dynamic_sql_risk == "HIGH"),
            "with_transactions":  sum(1 for n in nodes if n.transaction_depth > 0),
            "procedures":        [n.to_dict() for n in nodes],
        }
        (out / "stored_procedure_lineage.json").write_text(
            json.dumps(lineage_data, indent=2, default=str), encoding="utf-8"
        )

        # ── stored_procedures.json (enriched M3-skill format) ─────────────
        # Build call graph adjacency: caller → [callees]
        call_graph: list[dict] = []
        for n in nodes:
            if n.nested_calls:
                call_graph.append({
                    "caller":  n.name,
                    "callees": n.nested_calls,
                    "depth":   n.call_depth,
                })

        # Collect all distinct tables touched across all procs
        all_tables: set[str] = set()
        for n in nodes:
            all_tables.update(n.table_dependencies)

        sp_data = {
            "procedure_count":   len(nodes),
            "function_count":    sum(1 for n in nodes if n.proc_type in ("FUNCTION", "SCALAR_FUNCTION", "TABLE_FUNCTION")),
            "tables_touched":    sorted(all_tables),
            "call_graph":        call_graph,
            "max_call_depth":    max((n.call_depth for n in nodes), default=0),
            "procedures": [
                {
                    "name":              n.name,
                    "schema":            n.schema,
                    "type":              n.proc_type,
                    "inputs":            [{"name": p} for p in n.parameters_in],
                    "outputs":           [{"name": p} for p in n.parameters_out],
                    "crud_operations":   list(n.crud_summary.keys()),
                    "tables_read":       n.tables_read,
                    "tables_written":    n.tables_written,
                    "tables_deleted":    n.tables_deleted,
                    "table_dependencies": n.table_dependencies,
                    "joins":             n.joins,
                    "result_sets":       n.result_sets,
                    "nested_calls":      n.nested_calls,
                    "call_depth":        n.call_depth,
                    "has_dynamic_sql":   n.has_dynamic_sql,
                    "dynamic_sql_risk":  n.dynamic_sql_risk,
                    "has_transaction":   n.transaction_depth > 0,
                    "transaction_depth": n.transaction_depth,
                    "has_temp_tables":   n.has_temp_tables,
                    "business_rules":    n.business_rules,
                    "risk_flags":        n.risk_flags,
                    "source_file":       n.source_file,
                    "line_number":       n.line_number,
                }
                for n in nodes
            ],
        }
        (out / "stored_procedures.json").write_text(
            json.dumps(sp_data, indent=2, default=str), encoding="utf-8"
        )

        print(
            f"[SPAnalyzer] {len(nodes)} procedures analyzed "
            f"-> stored_procedure_lineage.json + stored_procedures.json"
        )
