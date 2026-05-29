"""
PostgreSQL Database-First Adapter

Inherits the generic SQL DDL parser and applies PostgreSQL-specific
normalizations:
  - SERIAL / BIGSERIAL → integer + identity=True
  - BOOLEAN
  - JSONB / JSON
  - UUID
  - ENUM types
  - Schema search_path handling
  - PostgreSQL-specific constraint syntax
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from adapters.sql_common.ddl_parser import DDLParser
from adapters.sqlserver.sql_adapter import SQLServerAdapter
from models.semantic_model import TechContext


class PostgreSQLAdapter(BaseAdapter):
    """
    PostgreSQL database-first adapter.
    Reuses SQLServerAdapter's USM mapping — DDL parser handles dialect differences.
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else None

    @property
    def name(self) -> str:
        return "PostgreSQL / Database-First"

    @property
    def supported_technologies(self) -> list[str]:
        return ["postgresql"]

    def can_handle(self, tech_context: TechContext) -> bool:
        sql_files = bool(list(Path(tech_context.project_root).rglob("*.sql"))[:1])
        return (
            sql_files and (
                "postgresql" in tech_context.db_types
                or "psycopg2" in " ".join(tech_context.frameworks).lower()
            )
        )

    def extract(self, tech_context: TechContext) -> SemanticModel:
        # PostgreSQL uses public schema by default
        parser = DDLParser(default_schema="public")
        ddl    = parser.parse_directory(tech_context.project_root)

        # Reuse SQL Server mapper (DDL is normalized)
        inner = SQLServerAdapter(output_dir=str(self._output_dir) if self._output_dir else None)
        if self._output_dir:
            inner._write_raw_extracts(ddl, self._output_dir)
        model = inner._build_from_ddl(ddl, tech_context, self.name)  # type: ignore
        return model


