"""
SQLite Database-First Adapter

Supports both:
  1. SQL DDL scripts (.sql files)
  2. Live SQLite database files (.db / .sqlite / .sqlite3) — reads schema via sqlite3 module
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from adapters.sql_common.ddl_parser import (
    DDLParser, DDLParseResult, DDLTable, DDLColumn, DDLForeignKey,
    normalize_sql_type, _detect_pii,
)
from adapters.sqlserver.sql_adapter import SQLServerAdapter, _aggregate_from_table
from models.semantic_model import SemanticModel, TechContext
from models.universal import (
    UniversalEntity, UniversalField, UniversalRelationship,
    EntityKind, FieldKind, RelationshipKind,
    Technology, Language, ConfidenceLevel,
)


class SQLiteAdapter(BaseAdapter):

    @property
    def name(self) -> str:
        return "SQLite / Database-First"

    @property
    def supported_technologies(self) -> list[str]:
        return ["sqlite"]

    def can_handle(self, tech_context: TechContext) -> bool:
        root = Path(tech_context.project_root)
        has_db    = bool(list(root.rglob("*.db"))[:1] or
                         list(root.rglob("*.sqlite"))[:1] or
                         list(root.rglob("*.sqlite3"))[:1])
        has_sql   = bool(list(root.rglob("*.sql"))[:1])
        return has_db or (has_sql and "sqlite" in tech_context.db_types)

    def extract(self, tech_context: TechContext) -> SemanticModel:
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )
        root = Path(tech_context.project_root)

        # First try: live .db files
        for db_file in list(root.rglob("*.db")) + list(root.rglob("*.sqlite")) + list(root.rglob("*.sqlite3")):
            try:
                partial = self._extract_from_db(db_file)
                model.entities.extend(partial.entities)
                model.relationships.extend(partial.relationships)
            except Exception as e:
                model.extraction_warnings.append(f"SQLite {db_file}: {e}")

        # Then: .sql DDL files
        parser = DDLParser(default_schema="main")
        ddl    = parser.parse_directory(tech_context.project_root)
        inner  = SQLServerAdapter()
        partial_model = inner._build_from_ddl(ddl, tech_context, self.name)  # type: ignore
        for e in partial_model.entities:
            if not any(x.name == e.name for x in model.entities):
                model.entities.append(e)
        model.relationships.extend(partial_model.relationships)

        return model

    def _extract_from_db(self, db_path: Path) -> SemanticModel:
        """Read schema directly from a SQLite database file."""
        from models.semantic_model import SemanticModel, TechContext
        model = SemanticModel(project_root=str(db_path.parent), adapter_used=self.name)

        conn = sqlite3.connect(str(db_path))
        cur  = conn.cursor()
        try:
            cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = cur.fetchall()

            for table_name, create_sql in tables:
                if not create_sql:
                    continue
                # Get column info from PRAGMA
                cur.execute(f'PRAGMA table_info("{table_name}")')
                col_rows = cur.fetchall()
                # (cid, name, type, notnull, dflt_value, pk)

                cur.execute(f'PRAGMA foreign_key_list("{table_name}")')
                fk_rows = cur.fetchall()
                # (id, seq, table, from, to, on_update, on_delete, match)

                fields: list[UniversalField] = []
                pk_cols: list[str] = []

                for row in col_rows:
                    cid, col_name, col_type, not_null, dflt, is_pk_col = row
                    if is_pk_col:
                        pk_cols.append(col_name)

                    norm_type = normalize_sql_type(col_type or "text")
                    is_fk = any(r[3] == col_name for r in fk_rows)
                    kind  = FieldKind.FOREIGN_KEY if is_fk else FieldKind.PRIMITIVE

                    fields.append(UniversalField(
                        name            = col_name,
                        kind            = kind,
                        raw_type        = col_type or "TEXT",
                        normalized_type = norm_type,
                        is_pk           = bool(is_pk_col),
                        is_required     = bool(not_null),
                        is_nullable     = not bool(not_null),
                        default_val     = dflt,
                        pii_risk        = _detect_pii(col_name),
                        source_file     = str(db_path),
                        confidence      = ConfidenceLevel.HIGH,
                    ))

                entity = UniversalEntity(
                    name        = table_name,
                    kind        = EntityKind.ENTITY,
                    technology  = Technology.EF_CORE,  # SQLite maps here
                    language    = Language.UNKNOWN,
                    namespace   = "main",
                    aggregate   = _aggregate_from_table(table_name),
                    fields      = fields,
                    source_file = str(db_path),
                    confidence  = ConfidenceLevel.HIGH,
                )
                model.entities.append(entity)

                # FK relationships
                for row in fk_rows:
                    fk_id, seq, ref_tbl, from_col, to_col, on_upd, on_del, match = row
                    model.relationships.append(UniversalRelationship(
                        source      = table_name,
                        target      = ref_tbl,
                        kind        = RelationshipKind.MANY_TO_ONE,
                        via         = from_col,
                        technology  = Technology.EF_CORE,
                        cascade_delete = (on_del or "").upper() == "CASCADE",
                        source_file = str(db_path),
                        confidence  = ConfidenceLevel.HIGH,
                        evidence    = "SQLite PRAGMA foreign_key_list",
                    ))

        finally:
            conn.close()

        return model
