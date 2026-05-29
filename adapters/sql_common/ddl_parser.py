"""
Universal SQL DDL Parser

Parses SQL DDL scripts regardless of dialect (SQL Server, PostgreSQL, MySQL, SQLite).
Handles all major enterprise DDL constructs:
  - CREATE TABLE (columns, constraints, identity/auto-increment)
  - ALTER TABLE ADD CONSTRAINT (foreign keys, unique, check)
  - PRIMARY KEY / FOREIGN KEY (inline + out-of-line)
  - CREATE INDEX
  - CREATE VIEW
  - CREATE PROCEDURE / CREATE FUNCTION
  - CREATE TRIGGER
  - Inline CHECK / DEFAULT / NOT NULL constraints

Output: DDLParseResult with fully normalized Python dataclasses.
Technology details (dialect syntax) are preserved in .raw fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes (output structures)
# ---------------------------------------------------------------------------

@dataclass
class DDLColumn:
    name:             str
    raw_type:         str
    normalized_type:  str
    ordinal:          int          = 0
    is_nullable:      bool         = True
    is_pk:            bool         = False
    is_identity:      bool         = False   # IDENTITY / AUTO_INCREMENT / SERIAL
    is_unique:        bool         = False
    is_indexed:       bool         = False
    max_length:       Optional[int] = None
    precision:        Optional[int] = None
    scale:            Optional[int] = None
    default_value:    Optional[str] = None
    check_expr:       Optional[str] = None
    pii_risk:         Optional[str] = None   # "high" | "medium" | "low"
    line_number:      Optional[int] = None
    raw:              dict          = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name":            self.name,
            "raw_type":        self.raw_type,
            "normalized_type": self.normalized_type,
            "ordinal":         self.ordinal,
            "is_nullable":     self.is_nullable,
            "is_pk":           self.is_pk,
            "is_identity":     self.is_identity,
            "is_unique":       self.is_unique,
            "is_indexed":      self.is_indexed,
            "max_length":      self.max_length,
            "precision":       self.precision,
            "scale":           self.scale,
            "default_value":   self.default_value,
            "pii_risk":        self.pii_risk,
            "line_number":     self.line_number,
        }


@dataclass
class DDLForeignKey:
    columns:             list[str]
    references_table:    str
    references_columns:  list[str]
    constraint_name:     str          = ""
    on_delete:           str          = "NO ACTION"
    on_update:           str          = "NO ACTION"
    source_file:         str          = ""
    line_number:         Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "constraint_name":    self.constraint_name,
            "columns":            self.columns,
            "references_table":   self.references_table,
            "references_columns": self.references_columns,
            "on_delete":          self.on_delete,
            "on_update":          self.on_update,
            "source_file":        self.source_file,
            "line_number":        self.line_number,
        }


@dataclass
class DDLIndex:
    name:        str
    table:       str
    columns:     list[str]
    is_unique:   bool          = False
    is_clustered: bool         = False
    index_type:  str           = "BTREE"   # BTREE | HASH | FULLTEXT | SPATIAL
    source_file: str           = ""
    line_number: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "table":       self.table,
            "columns":     self.columns,
            "is_unique":   self.is_unique,
            "is_clustered": self.is_clustered,
            "index_type":  self.index_type,
        }


@dataclass
class DDLTable:
    name:          str
    schema:        str                   = "dbo"
    columns:       list[DDLColumn]       = field(default_factory=list)
    primary_key:   list[str]             = field(default_factory=list)
    foreign_keys:  list[DDLForeignKey]   = field(default_factory=list)
    indexes:       list[DDLIndex]        = field(default_factory=list)
    unique_keys:   list[list[str]]       = field(default_factory=list)
    check_constraints: list[str]         = field(default_factory=list)
    source_file:   str                   = ""
    line_number:   Optional[int]         = None
    raw:           dict                  = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def to_dict(self) -> dict:
        return {
            "name":             self.name,
            "schema":           self.schema,
            "full_name":        self.full_name,
            "columns":          [c.to_dict() for c in self.columns],
            "primary_key":      self.primary_key,
            "foreign_keys":     [fk.to_dict() for fk in self.foreign_keys],
            "indexes":          [i.to_dict() for i in self.indexes],
            "unique_keys":      self.unique_keys,
            "check_constraints": self.check_constraints,
            "source_file":      self.source_file,
            "line_number":      self.line_number,
            "column_count":     len(self.columns),
            "fk_count":         len(self.foreign_keys),
        }


@dataclass
class DDLView:
    name:          str
    schema:        str           = "dbo"
    definition:    str           = ""
    source_tables: list[str]     = field(default_factory=list)
    columns:       list[str]     = field(default_factory=list)
    source_file:   str           = ""
    line_number:   Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name":          self.name,
            "schema":        self.schema,
            "definition":    self.definition[:500],
            "source_tables": self.source_tables,
            "columns":       self.columns,
            "source_file":   self.source_file,
            "line_number":   self.line_number,
        }


@dataclass
class DDLParameter:
    name:      str
    raw_type:  str
    direction: str   = "IN"   # IN | OUT | INOUT
    default_value: Optional[str] = None

    def to_dict(self) -> dict:
        return {"name": self.name, "raw_type": self.raw_type, "direction": self.direction}


@dataclass
class DDLProcedure:
    name:             str
    schema:           str                 = "dbo"
    proc_type:        str                 = "PROCEDURE"  # PROCEDURE | FUNCTION
    parameters:       list[DDLParameter]  = field(default_factory=list)
    tables_read:      list[str]           = field(default_factory=list)
    tables_written:   list[str]           = field(default_factory=list)
    tables_deleted:   list[str]           = field(default_factory=list)
    tables_all:       list[str]           = field(default_factory=list)
    crud_operations:  list[str]           = field(default_factory=list)
    has_transaction:  bool                = False
    has_dynamic_sql:  bool                = False
    has_temp_tables:  bool                = False
    nested_calls:     list[str]           = field(default_factory=list)
    return_type:      Optional[str]       = None
    body:             str                 = ""
    source_file:      str                 = ""
    line_number:      Optional[int]       = None

    def to_dict(self) -> dict:
        return {
            "name":            self.name,
            "schema":          self.schema,
            "proc_type":       self.proc_type,
            "parameters":      [p.to_dict() for p in self.parameters],
            "tables_read":     self.tables_read,
            "tables_written":  self.tables_written,
            "tables_deleted":  self.tables_deleted,
            "crud_operations": self.crud_operations,
            "has_transaction": self.has_transaction,
            "has_dynamic_sql": self.has_dynamic_sql,
            "has_temp_tables": self.has_temp_tables,
            "nested_calls":    self.nested_calls,
            "source_file":     self.source_file,
            "line_number":     self.line_number,
        }


@dataclass
class DDLTrigger:
    name:        str
    table:       str
    schema:      str         = "dbo"
    timing:      str         = "AFTER"   # AFTER | BEFORE | INSTEAD OF
    events:      list[str]   = field(default_factory=list)  # INSERT | UPDATE | DELETE
    body:        str         = ""
    source_file: str         = ""
    line_number: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "table":       self.table,
            "schema":      self.schema,
            "timing":      self.timing,
            "events":      self.events,
            "source_file": self.source_file,
            "line_number": self.line_number,
        }


@dataclass
class DDLParseResult:
    tables:      list[DDLTable]     = field(default_factory=list)
    views:       list[DDLView]      = field(default_factory=list)
    procedures:  list[DDLProcedure] = field(default_factory=list)
    functions:   list[DDLProcedure] = field(default_factory=list)
    triggers:    list[DDLTrigger]   = field(default_factory=list)
    indexes:     list[DDLIndex]     = field(default_factory=list)   # standalone CREATE INDEX
    warnings:    list[str]          = field(default_factory=list)
    source_file: str                = ""

    def summary(self) -> dict:
        return {
            "tables":     len(self.tables),
            "views":      len(self.views),
            "procedures": len(self.procedures),
            "functions":  len(self.functions),
            "triggers":   len(self.triggers),
            "indexes":    len(self.indexes),
        }

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "tables":      [t.to_dict() for t in self.tables],
            "views":       [v.to_dict() for v in self.views],
            "procedures":  [p.to_dict() for p in self.procedures],
            "functions":   [f.to_dict() for f in self.functions],
            "triggers":    [t.to_dict() for t in self.triggers],
            "indexes":     [i.to_dict() for i in self.indexes],
            "warnings":    self.warnings,
            "summary":     self.summary(),
        }


# ---------------------------------------------------------------------------
# Type normalization
# ---------------------------------------------------------------------------

_SQL_TYPE_MAP: dict[str, str] = {
    # SQL Server
    "int": "integer", "bigint": "integer", "smallint": "integer",
    "tinyint": "integer", "bit": "boolean",
    "decimal": "decimal", "numeric": "decimal", "money": "decimal",
    "smallmoney": "decimal", "float": "decimal", "real": "decimal",
    "char": "string", "varchar": "string", "nchar": "string",
    "nvarchar": "string", "text": "string", "ntext": "string",
    "xml": "xml",
    "datetime": "datetime", "datetime2": "datetime", "date": "date",
    "time": "time", "smalldatetime": "datetime", "datetimeoffset": "datetime",
    "timestamp": "datetime", "rowversion": "bytes",
    "uniqueidentifier": "uuid",
    "binary": "bytes", "varbinary": "bytes", "image": "bytes",
    "sql_variant": "any", "geography": "geometry", "geometry": "geometry",
    "hierarchyid": "string",
    # PostgreSQL extras
    "serial": "integer", "bigserial": "integer", "smallserial": "integer",
    "boolean": "boolean", "bool": "boolean",
    "bytea": "bytes", "json": "json", "jsonb": "json", "uuid": "uuid",
    "inet": "string", "cidr": "string", "macaddr": "string",
    "interval": "duration", "tstzrange": "datetime",
    # MySQL extras
    "tinytext": "string", "mediumtext": "string", "longtext": "string",
    "enum": "string", "set": "string",
    "mediumint": "integer", "int2": "integer", "int4": "integer",
    "int8": "integer", "double": "decimal",
    "tinyblob": "bytes", "blob": "bytes", "mediumblob": "bytes", "longblob": "bytes",
    "year": "integer",
    # SQLite
    "integer": "integer", "real": "decimal", "text": "string",
    "blob": "bytes", "numeric": "decimal",
}


def _dedup_by(items: list, key_fn) -> list:
    """Return items deduplicated by key_fn, preserving first occurrence order."""
    seen: set = set()
    result = []
    for item in items:
        k = key_fn(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result


def normalize_sql_type(raw_type: str) -> str:
    """Normalize SQL type to universal type. Strips precision/scale."""
    base = re.sub(r'\s*\([^)]*\)', '', raw_type.lower()).strip()
    base = base.replace("unsigned", "").replace("zerofill", "").strip()
    return _SQL_TYPE_MAP.get(base, base if base else "unknown")


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

_PII_HIGH   = re.compile(r'\b(ssn|social_sec|password|passwd|credit_card|card_num|cvv|pin|secret|api_key)\b', re.I)
_PII_MEDIUM = re.compile(r'\b(email|phone|mobile|fax|address|birth|dob|salary|wage|tax_id|national_id|passport|license)\b', re.I)
_PII_LOW    = re.compile(r'\b(name|first_name|last_name|full_name|username|user_name|display_name)\b', re.I)


def _detect_pii(col_name: str) -> Optional[str]:
    if _PII_HIGH.search(col_name):
        return "high"
    if _PII_MEDIUM.search(col_name):
        return "medium"
    if _PII_LOW.search(col_name):
        return "low"
    return None


# ---------------------------------------------------------------------------
# Identifier helpers — handle [brackets], `backticks`, "quotes", bare names
# ---------------------------------------------------------------------------

_IDENT = r'(?:\[([^\]]+)\]|`([^`]+)`|"([^"]+)"|(\w+))'


def _unquote(m_groups: tuple) -> str:
    """Extract identifier from a multi-group match of _IDENT."""
    return next((g for g in m_groups if g is not None), "")


def _unquote_str(s: str) -> str:
    """Strip bracket/backtick/quote wrapping from a single identifier."""
    s = s.strip()
    if (s.startswith('[') and s.endswith(']')) or \
       (s.startswith('`') and s.endswith('`')) or \
       (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    return s


def _extract_name(sql_fragment: str) -> str:
    """Extract the first identifier (with optional schema prefix) from a SQL fragment."""
    m = re.match(
        r'\s*(?:' + _IDENT + r'\s*\.\s*)?' + _IDENT,
        sql_fragment,
    )
    if not m:
        return sql_fragment.strip()
    groups = m.groups()
    # Last 4 groups = object name, first 4 = optional schema
    schema_part = groups[:4]
    name_part   = groups[4:]
    name = _unquote(name_part)
    return name if name else _unquote(schema_part)


def _extract_schema_name(sql_fragment: str) -> tuple[str, str]:
    """Extract (schema, name) from a SQL identifier like [dbo].[Orders]."""
    parts_re = re.compile(_IDENT + r'\s*\.\s*' + _IDENT)
    m = parts_re.match(sql_fragment.strip())
    if m:
        groups = m.groups()
        schema = _unquote(groups[:4])
        name   = _unquote(groups[4:])
        return schema, name
    name = _extract_name(sql_fragment)
    return "dbo", name


def _extract_columns_list(s: str) -> list[str]:
    """Extract column names from a comma-separated list, stripping quotes."""
    return [_unquote_str(c.strip()) for c in s.split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Core DDL Parser
# ---------------------------------------------------------------------------

# Strip comments before parsing
_LINE_COMMENT = re.compile(r'--[^\n]*')
_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


def _strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(' ', sql)
    sql = _LINE_COMMENT.sub('', sql)
    return sql


class DDLParser:
    """
    Parses SQL DDL scripts into structured DDLParseResult objects.
    Handles SQL Server, PostgreSQL, MySQL, and SQLite dialects.
    """

    def __init__(self, default_schema: str = "dbo") -> None:
        self._default_schema = default_schema

    def parse_file(self, path: str | Path) -> DDLParseResult:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            result = DDLParseResult(source_file=str(path))
            result.warnings.append(f"Cannot read file: {e}")
            return result
        result = self.parse(text, source_file=str(path))
        result.source_file = str(path)
        return result

    def parse_directory(self, directory: str | Path) -> DDLParseResult:
        """Parse all .sql files in a directory recursively, deduplicating by (schema, name)."""
        root = Path(directory)
        merged = DDLParseResult()
        sql_files = sorted(root.rglob("*.sql"))
        if not sql_files:
            merged.warnings.append(f"No .sql files found in {directory}")
            return merged

        for sql_file in sql_files:
            partial = self.parse_file(sql_file)
            merged.tables.extend(partial.tables)
            merged.views.extend(partial.views)
            merged.procedures.extend(partial.procedures)
            merged.functions.extend(partial.functions)
            merged.triggers.extend(partial.triggers)
            merged.indexes.extend(partial.indexes)
            merged.warnings.extend(partial.warnings)

        # Deduplicate by (schema.name) — keep first occurrence
        # This handles multiple files defining the same schema (e.g. regular + Azure variant)
        merged.tables     = _dedup_by(merged.tables,     lambda x: f"{x.schema}.{x.name}".lower())
        merged.views      = _dedup_by(merged.views,      lambda x: f"{x.schema}.{x.name}".lower())
        merged.procedures = _dedup_by(merged.procedures, lambda x: f"{x.schema}.{x.name}".lower())
        merged.functions  = _dedup_by(merged.functions,  lambda x: f"{x.schema}.{x.name}".lower())
        merged.triggers   = _dedup_by(merged.triggers,   lambda x: x.name.lower())

        return merged

    def parse(self, sql: str, source_file: str = "") -> DDLParseResult:
        """Parse a SQL DDL string and return a DDLParseResult."""
        result = DDLParseResult(source_file=source_file)
        clean  = _strip_comments(sql)

        result.tables.extend(self._parse_tables(clean, sql, source_file))
        result.views.extend(self._parse_views(clean, sql, source_file))
        result.procedures.extend(self._parse_procedures(clean, sql, source_file, "PROCEDURE"))
        result.functions.extend(self._parse_procedures(clean, sql, source_file, "FUNCTION"))
        result.triggers.extend(self._parse_triggers(clean, sql, source_file))
        result.indexes.extend(self._parse_standalone_indexes(clean, source_file))

        # Process ALTER TABLE ADD CONSTRAINT (foreign keys, unique, check)
        self._process_alter_table(clean, result, source_file)

        return result

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------

    def _parse_tables(self, clean: str, original: str, src: str) -> list[DDLTable]:
        tables: list[DDLTable] = []

        pattern = re.compile(
            r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' +
            r'(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)\s*\(',
            re.IGNORECASE,
        )

        for m in pattern.finditer(clean):
            full_name_str = m.group(1)
            schema, name  = _extract_schema_name(full_name_str)
            if not schema:
                schema = self._default_schema
            if not name:
                continue

            # Find matching closing paren
            body_start = m.end() - 1
            body_end   = self._find_matching_paren(clean, body_start)
            if body_end < 0:
                continue
            body = clean[body_start + 1:body_end]

            line_no = clean[:m.start()].count('\n') + 1

            table = DDLTable(
                name        = name,
                schema      = schema,
                source_file = src,
                line_number = line_no,
            )
            self._parse_table_body(body, table, src, line_no)
            tables.append(table)

        return tables

    def _parse_table_body(self, body: str, table: DDLTable, src: str, base_line: int) -> None:
        """Parse column definitions and inline constraints from a CREATE TABLE body."""
        # Split into clauses by top-level commas
        clauses = self._split_top_level(body)
        ordinal = 0

        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue

            upper = clause.upper()

            # --- Inline/table-level CONSTRAINT ---
            if re.match(r'CONSTRAINT\b', upper, re.I):
                self._parse_constraint_clause(clause, table, src)
                continue

            # --- PRIMARY KEY (table-level, no CONSTRAINT prefix) ---
            pk_m = re.match(r'PRIMARY\s+KEY\s+(?:CLUSTERED\s+|NONCLUSTERED\s+)?'
                            r'\(([^)]+)\)', upper)
            if pk_m:
                table.primary_key = _extract_columns_list(clause[pk_m.start(1):pk_m.end(1)])
                for col in table.columns:
                    if col.name in table.primary_key:
                        col.is_pk = True
                continue

            # --- UNIQUE (table-level) ---
            uniq_m = re.match(r'UNIQUE\s+(?:(?:KEY|INDEX)\s+\w+\s+)?\(([^)]+)\)', upper)
            if uniq_m:
                cols = _extract_columns_list(clause[uniq_m.start(1):uniq_m.end(1)])
                table.unique_keys.append(cols)
                for col in table.columns:
                    if col.name in cols and len(cols) == 1:
                        col.is_unique = True
                continue

            # --- FOREIGN KEY (table-level) ---
            fk_m = re.match(r'(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)'
                            r'\s*REFERENCES\s+(.+)', upper, re.I | re.DOTALL)
            if fk_m:
                self._parse_fk_clause(clause, table, src)
                continue

            # --- INDEX (MySQL inline) ---
            idx_m = re.match(r'(?:UNIQUE\s+)?(?:KEY|INDEX)\s+\w+\s*\(([^)]+)\)', upper)
            if idx_m:
                continue  # Handled above or by standalone

            # --- CHECK constraint ---
            if upper.startswith("CHECK"):
                chk_m = re.search(r'CHECK\s*\((.+)', clause, re.I | re.DOTALL)
                if chk_m:
                    table.check_constraints.append(chk_m.group(1).rstrip(')').strip())
                continue

            # --- Column definition ---
            col = self._parse_column_def(clause, ordinal, src, base_line)
            if col:
                # Mark PK if already in primary_key list
                if col.name in table.primary_key:
                    col.is_pk = True
                table.columns.append(col)
                ordinal += 1

        # Sync PK marks after all columns parsed
        for col in table.columns:
            if col.name in table.primary_key:
                col.is_pk = True

    def _parse_column_def(
        self, clause: str, ordinal: int, src: str, base_line: int
    ) -> Optional[DDLColumn]:
        """Parse a single column definition."""
        clause = clause.strip()
        if not clause:
            return None

        # Extract column name
        name_m = re.match(_IDENT, clause)
        if not name_m:
            return None
        name = _unquote(name_m.groups())
        if not name or name.upper() in (
            "CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
            "INDEX", "KEY", "FULLTEXT", "SPATIAL",
        ):
            return None

        rest = clause[name_m.end():].strip()

        # Extract type (everything up to a modifier keyword or end)
        type_m = re.match(
            r'([\w\s]+?(?:\s*\(\s*[\d,\s]+\s*\))?)\s*'
            r'(?:NOT\s+NULL|NULL\b|DEFAULT\b|IDENTITY|AUTO_INCREMENT|SERIAL|'
            r'PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK|GENERATED|$)',
            rest, re.I
        )
        raw_type = type_m.group(1).strip() if type_m else rest.split()[0] if rest.split() else "unknown"

        # Normalize
        norm_type = normalize_sql_type(raw_type)

        # Nullable
        upper = rest.upper()
        is_nullable = not bool(re.search(r'\bNOT\s+NULL\b', upper))

        # Identity / auto-increment
        is_identity = bool(re.search(
            r'\bIDENTITY\b|\bAUTO_INCREMENT\b|\bSERIAL\b|\bGENERATED\s+(?:ALWAYS|BY\s+DEFAULT)',
            upper
        ))

        # Primary key inline
        is_pk = bool(re.search(r'\bPRIMARY\s+KEY\b', upper))

        # Unique inline
        is_unique = bool(re.search(r'\bUNIQUE\b', upper))

        # Default value
        default_val: Optional[str] = None
        def_m = re.search(r'\bDEFAULT\s+(.+?)(?:\s+(?:NOT\s+NULL|NULL|IDENTITY|UNIQUE|CHECK|REFERENCES|PRIMARY|$))', rest, re.I)
        if def_m:
            default_val = def_m.group(1).strip().rstrip(',')

        # Max length from type (e.g. VARCHAR(255), NVARCHAR(MAX))
        max_len: Optional[int] = None
        prec: Optional[int] = None
        scale_val: Optional[int] = None
        len_m = re.search(r'\((\d+)(?:\s*,\s*(\d+))?\)', raw_type)
        if len_m:
            v1 = int(len_m.group(1))
            v2 = int(len_m.group(2)) if len_m.group(2) else None
            if norm_type in ("string", "bytes"):
                max_len = v1
            elif norm_type in ("decimal",):
                prec, scale_val = v1, v2

        pii = _detect_pii(name)

        return DDLColumn(
            name            = name,
            raw_type        = raw_type,
            normalized_type = norm_type,
            ordinal         = ordinal,
            is_nullable     = is_nullable,
            is_pk           = is_pk,
            is_identity     = is_identity,
            is_unique       = is_unique,
            max_length      = max_len,
            precision       = prec,
            scale           = scale_val,
            default_value   = default_val,
            pii_risk        = pii,
        )

    def _parse_constraint_clause(self, clause: str, table: DDLTable, src: str) -> None:
        """Parse a CONSTRAINT clause inside CREATE TABLE."""
        upper = clause.upper()

        # CONSTRAINT name PRIMARY KEY (cols)
        pk_m = re.search(r'PRIMARY\s+KEY\s+(?:CLUSTERED\s+)?'
                         r'(?:\([^)]+\)\s*)?\(([^)]+)\)', upper)
        if pk_m:
            # Find actual column names (not uppercased)
            start = pk_m.start(1)
            table.primary_key = _extract_columns_list(clause[start:start + len(pk_m.group(1))])
            return

        # CONSTRAINT name FOREIGN KEY
        fk_m = re.search(r'FOREIGN\s+KEY', upper)
        if fk_m:
            self._parse_fk_clause(clause, table, src)
            return

        # CONSTRAINT name UNIQUE (cols)
        uniq_m = re.search(r'UNIQUE\s*(?:NONCLUSTERED\s*)?\s*\(([^)]+)\)', upper)
        if uniq_m:
            start = uniq_m.start(1)
            cols = _extract_columns_list(clause[start:start + len(uniq_m.group(1))])
            table.unique_keys.append(cols)
            return

        # CONSTRAINT name CHECK (expr)
        chk_m = re.search(r'CHECK\s*\((.+)', clause, re.I | re.DOTALL)
        if chk_m:
            table.check_constraints.append(chk_m.group(1).rstrip(')').strip())

    def _parse_fk_clause(self, clause: str, table: DDLTable, src: str) -> None:
        """Extract a FOREIGN KEY definition from a table body clause."""
        # CONSTRAINT [name] FOREIGN KEY (cols) REFERENCES [schema].[table] (cols)
        fk_re = re.compile(
            r'(?:CONSTRAINT\s+' + _IDENT + r'\s+)?'
            r'FOREIGN\s+KEY\s*\(([^)]+)\)\s*'
            r'REFERENCES\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)\s*'
            r'(?:\(([^)]+)\))?\s*'
            r'(?:ON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION|RESTRICT))?\s*'
            r'(?:ON\s+UPDATE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION|RESTRICT))?',
            re.IGNORECASE | re.DOTALL,
        )
        m = fk_re.search(clause)
        if not m:
            return

        groups = m.groups()
        # groups: constraint_name_parts(4), fk_cols, ref_table_parts(8), ref_cols, on_del, on_upd
        # constraint name = groups[0..3]
        # fk_cols = groups[4]
        # ref table = groups[5..12]
        # ref_cols = groups[13]
        # on_delete = groups[14]
        # on_update = groups[15]

        # Simpler extraction
        fk_cols_m   = re.search(r'FOREIGN\s+KEY\s*\(([^)]+)\)', clause, re.I)
        ref_full_m  = re.search(r'REFERENCES\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', clause, re.I)
        ref_cols_m  = re.search(r'REFERENCES\s+[^\(]+\(([^)]+)\)', clause, re.I)
        on_del_m    = re.search(r'ON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION|RESTRICT)', clause, re.I)
        on_upd_m    = re.search(r'ON\s+UPDATE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION|RESTRICT)', clause, re.I)
        cname_m     = re.search(r'CONSTRAINT\s+' + _IDENT, clause, re.I)

        if not (fk_cols_m and ref_full_m):
            return

        fk_cols    = _extract_columns_list(fk_cols_m.group(1))
        ref_str    = ref_full_m.group(1)
        _, ref_tbl = _extract_schema_name(ref_str)
        ref_cols   = _extract_columns_list(ref_cols_m.group(1)) if ref_cols_m else []
        on_delete  = on_del_m.group(1).strip() if on_del_m else "NO ACTION"
        on_update  = on_upd_m.group(1).strip() if on_upd_m else "NO ACTION"
        cname      = ""
        if cname_m:
            cname_groups = cname_m.groups()
            cname = _unquote(cname_groups[:4]) if len(cname_groups) >= 4 else ""

        table.foreign_keys.append(DDLForeignKey(
            columns            = fk_cols,
            references_table   = ref_tbl,
            references_columns = ref_cols,
            constraint_name    = cname,
            on_delete          = on_delete.upper(),
            on_update          = on_update.upper(),
            source_file        = src,
        ))

    # ------------------------------------------------------------------
    # CREATE INDEX
    # ------------------------------------------------------------------

    def _parse_standalone_indexes(self, clean: str, src: str) -> list[DDLIndex]:
        indexes: list[DDLIndex] = []
        pattern = re.compile(
            r'\bCREATE\s+(UNIQUE\s+)?(?:CLUSTERED\s+|NONCLUSTERED\s+|FULLTEXT\s+|SPATIAL\s+)?'
            r'INDEX\s+' + _IDENT + r'\s+ON\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)'
            r'\s*\(([^)]+)\)',
            re.IGNORECASE,
        )
        for m in pattern.finditer(clean):
            is_unique = bool(m.group(1))
            # Extract index name
            idx_name = m.group(2) or m.group(3) or m.group(4) or m.group(5) or "idx"
            table_str = m.group(6)
            if not table_str:
                continue
            _, table_name = _extract_schema_name(table_str)
            cols_str  = m.group(10) if len(m.groups()) >= 10 else ""
            if not cols_str:
                # fallback: find columns after ON table (cols)
                col_m = re.search(r'\(([^)]+)\)', clean[m.start():m.start()+300])
                cols_str = col_m.group(1) if col_m else ""
            cols = _extract_columns_list(cols_str) if cols_str else []

            indexes.append(DDLIndex(
                name        = _unquote_str(idx_name),
                table       = table_name,
                columns     = cols,
                is_unique   = is_unique,
                source_file = src,
                line_number = clean[:m.start()].count('\n') + 1,
            ))
        return indexes

    # ------------------------------------------------------------------
    # CREATE VIEW
    # ------------------------------------------------------------------

    def _parse_views(self, clean: str, original: str, src: str) -> list[DDLView]:
        views: list[DDLView] = []
        pattern = re.compile(
            r'\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?VIEW\s+'
            r'(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)\s+'
            r'(?:\([^)]*\)\s+)?AS\s',
            re.IGNORECASE,
        )
        for m in pattern.finditer(clean):
            name_str  = m.group(1)
            schema, name = _extract_schema_name(name_str)
            if not name:
                continue

            # Body: everything after AS until GO / next CREATE or end
            body_start = m.end()
            end_m = re.search(r'\bGO\b|\bCREATE\b', clean[body_start:], re.IGNORECASE)
            body = clean[body_start:body_start + end_m.start()] if end_m else clean[body_start:]
            body = body.strip()

            # Find source tables FROM / JOIN
            src_tables = _extract_referenced_tables(body)

            views.append(DDLView(
                name         = name,
                schema       = schema or self._default_schema,
                definition   = body[:2000],
                source_tables = src_tables,
                source_file  = src,
                line_number  = clean[:m.start()].count('\n') + 1,
            ))
        return views

    # ------------------------------------------------------------------
    # CREATE PROCEDURE / FUNCTION
    # ------------------------------------------------------------------

    def _parse_procedures(
        self, clean: str, original: str, src: str, kind: str
    ) -> list[DDLProcedure]:
        procs: list[DDLProcedure] = []
        kw = "PROCEDURE" if kind == "PROCEDURE" else "FUNCTION"

        pattern = re.compile(
            r'\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:DEFINER\s*=[^\s]+\s+)?'
            r'(?:' + kw + r')\s+'
            r'(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)',
            re.IGNORECASE,
        )
        for m in pattern.finditer(clean):
            name_str = m.group(1)
            schema, name = _extract_schema_name(name_str)
            if not name:
                continue

            # Body: from name to GO / next CREATE / end
            body_start = m.end()
            end_m = re.search(r'\bGO\b(?:\s*\n)|\bCREATE\s+', clean[body_start:], re.IGNORECASE)
            body = clean[body_start:body_start + end_m.start()] if end_m else clean[body_start:]

            # Parameters: between ( ) right after proc name
            params = self._parse_proc_params(body, kind)

            # Analyze body operations
            proc = DDLProcedure(
                name        = name,
                schema      = schema or self._default_schema,
                proc_type   = kind,
                parameters  = params,
                body        = body[:3000],
                source_file = src,
                line_number = clean[:m.start()].count('\n') + 1,
            )
            self._analyze_proc_body(body, proc)
            procs.append(proc)

        return procs

    def _parse_proc_params(self, body: str, kind: str) -> list[DDLParameter]:
        """Extract parameters from a procedure/function body."""
        params: list[DDLParameter] = []

        # SQL Server: @ParamName type [= default] [OUTPUT]
        ss_param = re.compile(
            r'(@\w+)\s+([\w\s()]+?)(?:\s*=\s*([^,\n]+?))?\s*(?:OUTPUT|OUT|READONLY)?(?=[,\n)])',
            re.IGNORECASE,
        )
        for m in ss_param.finditer(body[:500]):
            direction = "OUT" if re.search(r'\bOUT(?:PUT)?\b', m.group(0), re.I) else "IN"
            params.append(DDLParameter(
                name      = m.group(1),
                raw_type  = m.group(2).strip(),
                direction = direction,
                default_value = m.group(3).strip() if m.group(3) else None,
            ))

        # MySQL/PostgreSQL: IN/OUT name type
        if not params:
            pg_param = re.compile(
                r'\b(IN|OUT|INOUT)?\s+(\w+)\s+([\w()]+)',
                re.IGNORECASE,
            )
            paren_m = re.search(r'\(([^)]+)\)', body[:500])
            if paren_m:
                for m in pg_param.finditer(paren_m.group(1)):
                    params.append(DDLParameter(
                        name      = m.group(2),
                        raw_type  = m.group(3),
                        direction = (m.group(1) or "IN").upper(),
                    ))

        return params

    def _analyze_proc_body(self, body: str, proc: DDLProcedure) -> None:
        """Detect CRUD ops, tables, transaction markers, dynamic SQL."""
        upper = body.upper()

        # CRUD detection
        crud: set[str] = set()
        tables_read:    set[str] = set()
        tables_written: set[str] = set()
        tables_deleted: set[str] = set()

        # SELECT ... FROM table
        for m in re.finditer(r'\bFROM\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', body, re.I):
            tbl = _unquote_str(m.group(1).split('.')[-1])
            if tbl and not tbl.upper().startswith('#'):
                tables_read.add(tbl)
                crud.add("SELECT")

        # JOIN table
        for m in re.finditer(r'\bJOIN\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', body, re.I):
            tbl = _unquote_str(m.group(1).split('.')[-1])
            if tbl:
                tables_read.add(tbl)

        # INSERT INTO table
        for m in re.finditer(r'\bINSERT\s+INTO\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', body, re.I):
            tbl = _unquote_str(m.group(1).split('.')[-1])
            if tbl:
                tables_written.add(tbl)
                crud.add("INSERT")

        # UPDATE table
        for m in re.finditer(r'\bUPDATE\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)(?:\s+SET|\s+\w)', body, re.I):
            tbl = _unquote_str(m.group(1).split('.')[-1])
            if tbl:
                tables_written.add(tbl)
                crud.add("UPDATE")

        # DELETE FROM table
        for m in re.finditer(r'\bDELETE\s+(?:FROM\s+)?(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', body, re.I):
            tbl = _unquote_str(m.group(1).split('.')[-1])
            if tbl:
                tables_deleted.add(tbl)
                crud.add("DELETE")

        # Nested EXEC calls
        nested: set[str] = set()
        for m in re.finditer(r'\bEXEC(?:UTE)?\s+(?:\@\w+\s*=\s*)?(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)', body, re.I):
            called = m.group(1)
            if not called.upper().startswith('SP_') and called:
                nested.add(_unquote_str(called.split('.')[-1]))

        proc.crud_operations = sorted(crud)
        proc.tables_read     = sorted(tables_read - tables_written - tables_deleted)
        proc.tables_written  = sorted(tables_written)
        proc.tables_deleted  = sorted(tables_deleted)
        proc.tables_all      = sorted(tables_read | tables_written | tables_deleted)
        proc.has_transaction = bool(re.search(r'\bBEGIN\s+(?:TRAN|TRANSACTION)\b', upper))
        proc.has_dynamic_sql = bool(re.search(r'\bEXEC\s*\(|\bSP_EXECUTESQL\b|EXECUTE\s*\(', upper))
        proc.has_temp_tables = bool(re.search(r'#\w+', body))
        proc.nested_calls    = sorted(nested)

    # ------------------------------------------------------------------
    # CREATE TRIGGER
    # ------------------------------------------------------------------

    def _parse_triggers(self, clean: str, original: str, src: str) -> list[DDLTrigger]:
        triggers: list[DDLTrigger] = []
        pattern = re.compile(
            r'\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?TRIGGER\s+'
            r'(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)\s+'
            r'(AFTER|BEFORE|INSTEAD\s+OF|FOR)\s+'
            r'((?:INSERT|UPDATE|DELETE)(?:\s*,\s*(?:INSERT|UPDATE|DELETE))*)\s+'
            r'ON\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)',
            re.IGNORECASE,
        )
        for m in pattern.finditer(clean):
            name_str  = m.group(1)
            timing    = m.group(5).strip().upper()
            events_str = m.group(6).strip().upper()
            table_str = m.group(7)

            _, trg_name = _extract_schema_name(name_str)
            _, tbl_name = _extract_schema_name(table_str)

            events = [e.strip() for e in events_str.split(",")]

            # Body
            body_start = m.end()
            end_m = re.search(r'\bGO\b(?:\s*\n)|\bCREATE\s+', clean[body_start:], re.IGNORECASE)
            body = clean[body_start:body_start + end_m.start()] if end_m else clean[body_start:]

            triggers.append(DDLTrigger(
                name        = trg_name,
                table       = tbl_name,
                timing      = timing,
                events      = events,
                body        = body[:2000],
                source_file = src,
                line_number = clean[:m.start()].count('\n') + 1,
            ))

        return triggers

    # ------------------------------------------------------------------
    # ALTER TABLE ADD CONSTRAINT
    # ------------------------------------------------------------------

    def _process_alter_table(self, clean: str, result: DDLParseResult, src: str) -> None:
        """Process ALTER TABLE statements to add FK constraints to known tables."""
        table_index = {t.name.lower(): t for t in result.tables}
        for sch_name in table_index:
            pass

        alter_re = re.compile(
            r'\bALTER\s+TABLE\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)\s+'
            r'(?:WITH\s+(?:CHECK|NOCHECK)\s+)?ADD\s+(.+?)(?=;\s*(?:GO|ALTER|CREATE)|GO\b|$)',
            re.IGNORECASE | re.DOTALL,
        )
        for m in alter_re.finditer(clean):
            table_str = m.group(1)
            _, table_name = _extract_schema_name(table_str)
            add_clause = m.group(9) if len(m.groups()) >= 9 else m.group(len(m.groups()))
            if not add_clause:
                continue

            # Find matching table
            tbl = table_index.get(table_name.lower())
            if not tbl:
                # Create a stub table entry for ALTER-only tables
                tbl = DDLTable(name=table_name, schema=self._default_schema, source_file=src)
                result.tables.append(tbl)
                table_index[table_name.lower()] = tbl

            # Parse each sub-clause
            for sub in self._split_top_level(add_clause):
                sub = sub.strip()
                upper = sub.upper()
                if "FOREIGN KEY" in upper:
                    self._parse_fk_clause(sub, tbl, src)
                elif "PRIMARY KEY" in upper:
                    pk_m = re.search(r'PRIMARY\s+KEY\s+(?:CLUSTERED\s+)?\(([^)]+)\)', sub, re.I)
                    if pk_m:
                        tbl.primary_key = _extract_columns_list(pk_m.group(1))
                elif "UNIQUE" in upper:
                    uniq_m = re.search(r'UNIQUE\s*(?:NONCLUSTERED\s*)?\(([^)]+)\)', sub, re.I)
                    if uniq_m:
                        tbl.unique_keys.append(_extract_columns_list(uniq_m.group(1)))

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _find_matching_paren(self, text: str, open_pos: int) -> int:
        """Find position of closing paren matching open_pos."""
        depth = 0
        for i in range(open_pos, len(text)):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _split_top_level(self, text: str) -> list[str]:
        """Split text by commas that are NOT inside parentheses."""
        parts:  list[str] = []
        depth   = 0
        current = []
        for ch in text:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Helper: extract table names from SELECT statements
# ---------------------------------------------------------------------------

def _extract_referenced_tables(sql_body: str) -> list[str]:
    """Extract all table/view names referenced in FROM/JOIN clauses."""
    tables: set[str] = set()
    pat = re.compile(
        r'\b(?:FROM|JOIN)\s+(' + _IDENT + r'(?:\s*\.\s*' + _IDENT + r')?)'
        r'(?:\s+(?:AS\s+)?\w+)?',
        re.IGNORECASE,
    )
    for m in pat.finditer(sql_body):
        name_str = m.group(1)
        _, name = _extract_schema_name(name_str)
        if name and not name.startswith('#') and name.upper() not in ('SELECT', 'WHERE'):
            tables.add(name)
    return sorted(tables)
