"""MySQL / MariaDB Database-First Adapter"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from adapters.sql_common.ddl_parser import DDLParser
from adapters.sqlserver.sql_adapter import SQLServerAdapter
from models.semantic_model import TechContext


class MySQLAdapter(BaseAdapter):

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else None

    @property
    def name(self) -> str:
        return "MySQL / MariaDB / Database-First"

    @property
    def supported_technologies(self) -> list[str]:
        return ["mysql"]

    def can_handle(self, tech_context: TechContext) -> bool:
        sql_files = bool(list(Path(tech_context.project_root).rglob("*.sql"))[:1])
        return sql_files and "mysql" in tech_context.db_types

    def extract(self, tech_context: TechContext) -> SemanticModel:
        parser = DDLParser(default_schema="")   # MySQL has no schema prefix typically
        ddl    = parser.parse_directory(tech_context.project_root)
        inner  = SQLServerAdapter(output_dir=str(self._output_dir) if self._output_dir else None)
        if self._output_dir:
            inner._write_raw_extracts(ddl, self._output_dir)
        return inner._build_from_ddl(ddl, tech_context, self.name)  # type: ignore
