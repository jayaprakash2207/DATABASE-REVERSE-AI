"""
SQL Server Database-First Adapter

Parses SQL Server DDL scripts (.sql files) and normalizes them into
the Universal Semantic Model.

Input:  .sql files containing CREATE TABLE, ALTER TABLE, CREATE VIEW,
        CREATE PROCEDURE, CREATE FUNCTION, CREATE INDEX, CREATE TRIGGER.
Output: SemanticModel populated with UniversalEntity, UniversalRelationship,
        UniversalEndpoint (for views/procs), UniversalHandler (for stored procs).

Emits normalized JSON to:
  memory/extracted/tables.json
  memory/extracted/columns.json
  memory/extracted/constraints.json
  memory/extracted/indexes.json
  memory/extracted/views.json
  memory/extracted/stored_procedures.json
  memory/extracted/sql_relationships.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from adapters.sql_common.ddl_parser import DDLParser, DDLTable, DDLParseResult
from models.semantic_model import SemanticModel, TechContext
from models.universal import (
    UniversalEntity, UniversalField, UniversalRelationship,
    UniversalEndpoint, UniversalHandler,
    EntityKind, FieldKind, RelationshipKind, EndpointStyle,
    Technology, Language, ConfidenceLevel, normalize_type,
)


class SQLServerAdapter(BaseAdapter):
    """
    SQL Server database-first adapter.
    Reads .sql DDL scripts, produces a fully populated SemanticModel.
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else None

    @property
    def name(self) -> str:
        return "SQL Server / Database-First"

    @property
    def supported_technologies(self) -> list[str]:
        return ["sqlserver"]

    def can_handle(self, tech_context: TechContext) -> bool:
        sql_files_present = bool(list(
            Path(tech_context.project_root).rglob("*.sql")
        )[:1])
        return (
            sql_files_present
            or "sqlserver" in tech_context.db_types
            or "mssql" in " ".join(tech_context.frameworks).lower()
        )

    # ------------------------------------------------------------------
    # Main extraction
    # ------------------------------------------------------------------

    def extract(self, tech_context: TechContext) -> SemanticModel:
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )

        parser = DDLParser(default_schema="dbo")
        ddl_result = parser.parse_directory(tech_context.project_root)
        model.extraction_warnings.extend(ddl_result.warnings)

        # Persist raw DDL extraction
        if self._output_dir:
            self._write_raw_extracts(ddl_result, self._output_dir)

        # Map DDL → USM
        self._map_tables(ddl_result, model)
        self._map_views(ddl_result, model)
        self._map_procedures(ddl_result, model)
        self._map_triggers(ddl_result, model)

        print(f"[SQLServerAdapter] {len(model.entities)} entities, "
              f"{len(model.relationships)} relationships, "
              f"{len(model.handlers)} procedures/functions, "
              f"{len(model.endpoints)} views")

        return model

    # ------------------------------------------------------------------
    # Table → UniversalEntity
    # ------------------------------------------------------------------

    def _map_tables(self, ddl: DDLParseResult, model: SemanticModel) -> None:
        for tbl in ddl.tables:
            fields = [self._map_column(c, tbl) for c in tbl.columns]
            agg    = _aggregate_from_table(tbl.name)

            entity = UniversalEntity(
                name        = tbl.name,
                kind        = EntityKind.ENTITY,
                technology  = Technology.EF_CORE,  # SQL Server maps to EF_CORE bucket
                language    = Language.UNKNOWN,
                namespace   = tbl.schema,
                aggregate   = agg,
                fields      = fields,
                source_file = tbl.source_file,
                line_number = tbl.line_number,
                confidence  = ConfidenceLevel.HIGH,
                raw         = tbl.to_dict(),
            )
            model.entities.append(entity)

        # Build relationships from FK definitions
        entity_names = {e.name.lower() for e in model.entities}
        for tbl in ddl.tables:
            for fk in tbl.foreign_keys:
                ref = fk.references_table
                if ref.lower() not in entity_names:
                    continue

                kind = RelationshipKind.MANY_TO_ONE
                if len(fk.columns) == 1 and len(tbl.primary_key) == 1 and \
                   fk.columns[0].lower() == tbl.primary_key[0].lower():
                    kind = RelationshipKind.ONE_TO_ONE

                cascade = fk.on_delete.upper() == "CASCADE"

                model.relationships.append(UniversalRelationship(
                    source          = tbl.name,
                    target          = ref,
                    kind            = kind,
                    via             = ", ".join(fk.columns),
                    technology      = Technology.EF_CORE,
                    cascade_delete  = cascade,
                    is_required     = True,
                    source_file     = fk.source_file or tbl.source_file,
                    line_number     = fk.line_number or tbl.line_number,
                    confidence      = ConfidenceLevel.HIGH,
                    evidence        = f"FOREIGN KEY constraint: {fk.constraint_name or 'inline'}",
                    raw             = fk.to_dict(),
                ))

    def _map_column(self, col, tbl: DDLTable) -> UniversalField:
        is_fk = any(col.name in fk.columns for fk in tbl.foreign_keys)
        kind  = FieldKind.FOREIGN_KEY if is_fk else (
            FieldKind.PRIMITIVE if col.normalized_type not in ("json", "bytes") else
            FieldKind.EMBEDDED
        )
        return UniversalField(
            name            = col.name,
            kind            = kind,
            raw_type        = col.raw_type,
            normalized_type = col.normalized_type,
            is_pk           = col.is_pk,
            is_required     = not col.is_nullable,
            is_unique       = col.is_unique,
            is_indexed      = col.is_indexed,
            is_nullable     = col.is_nullable,
            max_length      = col.max_length,
            default_val     = col.default_value,
            pii_risk        = col.pii_risk,
            source_file     = tbl.source_file,
            line_number     = col.line_number,
            confidence      = ConfidenceLevel.HIGH,
            raw             = col.to_dict(),
        )

    # ------------------------------------------------------------------
    # View → UniversalEndpoint (read-only query surface)
    # ------------------------------------------------------------------

    def _map_views(self, ddl: DDLParseResult, model: SemanticModel) -> None:
        for view in ddl.views:
            model.endpoints.append(UniversalEndpoint(
                method          = "GET",
                path            = f"/view/{view.schema}/{view.name}",
                style           = EndpointStyle.DJANGO_VIEW,   # reuse as "SQL View"
                technology      = Technology.EF_CORE,
                language        = Language.UNKNOWN,
                handler_class   = "SQL View",
                handler_method  = view.name,
                entities_touched = view.source_tables,
                source_file     = view.source_file,
                line_number     = view.line_number,
                confidence      = ConfidenceLevel.HIGH,
                raw             = view.to_dict(),
            ))

    # ------------------------------------------------------------------
    # Stored Procedure / Function → UniversalHandler
    # ------------------------------------------------------------------

    def _map_procedures(self, ddl: DDLParseResult, model: SemanticModel) -> None:
        all_procs = ddl.procedures + ddl.functions
        for proc in all_procs:
            crud  = proc.crud_operations
            pattern = "cqrs_query"
            if any(op in crud for op in ("INSERT", "UPDATE", "DELETE")):
                pattern = "cqrs_command"

            entities = proc.tables_all

            model.handlers.append(UniversalHandler(
                name             = proc.name,
                pattern          = pattern,
                request_type     = f"PROC:{proc.proc_type}",
                response_type    = proc.return_type,
                repositories     = [],
                entities_touched = entities,
                source_file      = proc.source_file,
                line_number      = proc.line_number,
                confidence       = ConfidenceLevel.HIGH,
            ))

    # ------------------------------------------------------------------
    # Trigger → UniversalGovernanceFinding (audit / side-effect)
    # ------------------------------------------------------------------

    def _map_triggers(self, ddl: DDLParseResult, model: SemanticModel) -> None:
        from models.universal import UniversalGovernanceFinding
        for trig in ddl.triggers:
            model.findings.append(UniversalGovernanceFinding(
                rule_type    = "trigger",
                severity     = "NOTE",
                finding_type = "CONFIRMED",
                entity       = trig.table,
                field        = "",
                description  = (
                    f"Trigger '{trig.name}' fires {trig.timing} "
                    f"{'/'.join(trig.events)} on table '{trig.table}'."
                ),
                recommendation = (
                    "Review trigger logic for hidden business rules, "
                    "side effects, or implicit audit trail enforcement."
                ),
                source_file  = trig.source_file,
                line_number  = trig.line_number,
                confidence   = ConfidenceLevel.HIGH,
                analyzer     = "SQLServerAdapter",
            ))

    # ------------------------------------------------------------------
    # Helper: build SemanticModel from a pre-parsed DDLParseResult
    # Used by PostgreSQLAdapter, MySQLAdapter, SQLiteAdapter
    # ------------------------------------------------------------------

    def _build_from_ddl(
        self, ddl: "DDLParseResult", tech_context: "TechContext", adapter_name: str
    ) -> "SemanticModel":
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = adapter_name,
        )
        model.extraction_warnings.extend(ddl.warnings)
        self._map_tables(ddl, model)
        self._map_views(ddl, model)
        self._map_procedures(ddl, model)
        self._map_triggers(ddl, model)
        return model

    # ------------------------------------------------------------------
    # Write raw DDL extracts
    # ------------------------------------------------------------------

    def _write_raw_extracts(self, ddl: DDLParseResult, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)

        # tables.json
        (out / "tables.json").write_text(
            json.dumps([t.to_dict() for t in ddl.tables], indent=2, default=str),
            encoding="utf-8",
        )
        # columns.json (flat list for easy querying)
        cols = []
        for t in ddl.tables:
            for c in t.columns:
                d = c.to_dict()
                d["table"] = t.name
                d["schema"] = t.schema
                cols.append(d)
        (out / "columns.json").write_text(
            json.dumps(cols, indent=2, default=str), encoding="utf-8"
        )
        # constraints.json
        constraints = []
        for t in ddl.tables:
            for fk in t.foreign_keys:
                d = fk.to_dict()
                d["table"] = t.name
                d["type"] = "FOREIGN_KEY"
                constraints.append(d)
            if t.primary_key:
                constraints.append({
                    "table": t.name, "type": "PRIMARY_KEY", "columns": t.primary_key
                })
            for uk in t.unique_keys:
                constraints.append({"table": t.name, "type": "UNIQUE", "columns": uk})
        (out / "constraints.json").write_text(
            json.dumps(constraints, indent=2, default=str), encoding="utf-8"
        )
        # indexes.json
        all_indexes = list(ddl.indexes)
        for t in ddl.tables:
            all_indexes.extend([{"table": t.name, **i.to_dict()} for i in t.indexes])
        (out / "indexes.json").write_text(
            json.dumps(all_indexes, indent=2, default=str), encoding="utf-8"
        )
        # views.json
        (out / "views.json").write_text(
            json.dumps([v.to_dict() for v in ddl.views], indent=2, default=str),
            encoding="utf-8",
        )
        # stored_procedures.json
        all_procs = ddl.procedures + ddl.functions
        (out / "stored_procedures.json").write_text(
            json.dumps([p.to_dict() for p in all_procs], indent=2, default=str),
            encoding="utf-8",
        )
        # stored_procedure_lineage.json (deep semantic analysis)
        if all_procs:
            try:
                from adapters.sqlserver.sp_analyzer import SPAnalyzer
                sp_nodes = SPAnalyzer().analyze_all(ddl)
                SPAnalyzer().save(sp_nodes, out)
            except Exception as _sp_err:
                print(f"[SQLServerAdapter] SPAnalyzer skipped: {_sp_err}")
        # sql_relationships.json
        rels = []
        for t in ddl.tables:
            for fk in t.foreign_keys:
                rels.append({
                    "source":           t.name,
                    "target":           fk.references_table,
                    "type":             "FOREIGN_KEY",
                    "via":              fk.columns,
                    "on_delete":        fk.on_delete,
                    "constraint_name":  fk.constraint_name,
                })
        (out / "sql_relationships.json").write_text(
            json.dumps(rels, indent=2, default=str), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DOMAIN_SUFFIXES = re.compile(
    r'(Order|Product|Catalog|Customer|User|Invoice|Payment|Shipping|'
    r'Employee|Supplier|Category|Report|Audit|Log|Event|Task|Role|'
    r'Permission|Document|File|Notification|Message|Comment)',
    re.I,
)

def _aggregate_from_table(table_name: str) -> str:
    """
    Derive aggregate/domain from table name.
    Order, OrderDetail, OrderHistory → OrderAggregate
    Product, ProductCategory → ProductAggregate
    """
    # Strip common plural/suffix patterns
    m = _DOMAIN_SUFFIXES.search(table_name)
    if m:
        return m.group(1).capitalize() + "Aggregate"
    # Strip pluralization
    name = table_name
    for suffix in ("Details", "Detail", "Items", "Item", "Lines", "Line",
                   "History", "Logs", "Log", "Records", "Record"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return (name[:1].upper() + name[1:] + "Aggregate") if name else (table_name + "Aggregate")
