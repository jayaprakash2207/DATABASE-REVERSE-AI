"""
SQL Governance Engine

Legacy enterprise database governance analysis.
Detects compliance, quality, and security issues in raw SQL schemas.

Rules:
  - PII / PCI-DSS / GDPR field detection
  - Missing encryption signals (password stored as varchar)
  - Nullable critical fields (PK, FK, required business fields)
  - Missing NOT NULL constraints on key columns
  - Missing indexes on FK columns
  - Missing audit columns (created_at, updated_at, created_by)
  - Soft delete inconsistencies (is_deleted without deleted_at)
  - Naming standard violations (inconsistent conventions)
  - Denormalization risks (repeated column names across tables)
  - Missing PRIMARY KEY
  - Wide tables (>30 columns — normalization risk)
  - Dynamic SQL stored procedures (injection surface)
  - Stored procedures writing to audit tables (enforcement check)

Generates:
  REVIEW/LEGACY_GOVERNANCE_REPORT.md
  REVIEW/DATA_QUALITY_REPORT.md
  memory/extracted/sql_governance.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.semantic_model import SemanticModel
from models.universal import UniversalGovernanceFinding, ConfidenceLevel


# ---------------------------------------------------------------------------
# PII / PCI / GDPR signals
# ---------------------------------------------------------------------------

_PII_HIGH_COLS = re.compile(
    r'\b(ssn|social_sec|password|passwd|credit_card|card_number|card_num|'
    r'cvv|cvc|pin|api_key|secret_key|access_token|refresh_token)\b', re.I
)
_PII_MEDIUM_COLS = re.compile(
    r'\b(email|phone|mobile|fax|address|birth_date|dob|date_of_birth|'
    r'salary|wage|income|tax_id|national_id|passport|driver_license|'
    r'ip_address|user_agent|geo|latitude|longitude)\b', re.I
)
_PII_LOW_COLS = re.compile(
    r'\b(first_name|last_name|full_name|display_name|username|user_name|'
    r'nick_name|profile_pic)\b', re.I
)
_PCI_COLS = re.compile(
    r'\b(card_number|card_num|pan|credit_card|debit_card|expiry|exp_date|'
    r'cvv|cvc|track_data|account_number|routing_number)\b', re.I
)
_AUDIT_COLS = re.compile(
    r'\b(created_at|created_on|createdAt|created_date|'
    r'updated_at|modified_at|updatedAt|modified_date|'
    r'created_by|modified_by|createdBy|updatedBy)\b', re.I
)
_SOFT_DELETE_FLAG = re.compile(r'\b(is_deleted|is_active|deleted|active|is_removed)\b', re.I)
_SOFT_DELETE_DATE = re.compile(r'\b(deleted_at|deactivated_at|removed_at)\b', re.I)

# Encryption gap signals: sensitive data stored as plain string
_PLAIN_SENSITIVE = re.compile(
    r'\b(password|passwd|secret|api_key|access_token|private_key|'
    r'credit_card|card_number|card_num|ssn|social_security)\b', re.I
)
_PLAIN_STRING_TYPES = {"varchar", "nvarchar", "char", "nchar", "text", "ntext", "string"}

# GDPR retention / right-to-erasure signals
_GDPR_PERSONAL = re.compile(
    r'\b(email|phone|mobile|fax|address|birth|dob|name|'
    r'national_id|passport|ip_address|geo|latitude|longitude)\b', re.I
)
_RETENTION_COLS = re.compile(
    r'\b(retention_date|expiry_date|purge_date|delete_after|ttl|expires_at)\b', re.I
)

# Duplicate business key: non-PK columns that look like natural keys
_NATURAL_KEY_PATTERNS = re.compile(
    r'\b(code|number|no|num|ref|reference|sku|barcode|isbn|isin|'
    r'employee_no|order_no|invoice_no|customer_no|account_no)\b', re.I
)


@dataclass
class SQLGovernanceFinding:
    rule_type:      str
    severity:       str      # CRITICAL | WARNING | NOTE
    finding_type:   str      # CONFIRMED | INFERRED | RECOMMENDED
    table:          str
    column:         str      = ""
    description:    str      = ""
    recommendation: str      = ""
    evidence:       str      = ""
    source_file:    str      = ""
    confidence:     str      = "HIGH"
    enforcement_layer: str   = "DATABASE"

    def to_dict(self) -> dict:
        return {
            "rule_type":      self.rule_type,
            "severity":       self.severity,
            "finding_type":   self.finding_type,
            "table":          self.table,
            "column":         self.column,
            "description":    self.description,
            "recommendation": self.recommendation,
            "evidence":       self.evidence,
            "source_file":    self.source_file,
            "confidence":     self.confidence,
            "enforcement_layer": self.enforcement_layer,
        }


class SQLGovernanceEngine:
    """
    Analyzes a SemanticModel (populated by database-first adapters) for
    governance, data quality, and security issues.
    """

    def analyze(self, model: SemanticModel) -> list[SQLGovernanceFinding]:
        findings: list[SQLGovernanceFinding] = []

        entity_names = {e.name for e in model.entities}

        for entity in model.entities:
            findings.extend(self._check_pii_pci(entity))
            findings.extend(self._check_missing_pk(entity))
            findings.extend(self._check_audit_columns(entity))
            findings.extend(self._check_nullable_issues(entity))
            findings.extend(self._check_soft_delete(entity))
            findings.extend(self._check_wide_table(entity))
            findings.extend(self._check_naming_conventions(entity))
            findings.extend(self._check_encryption_gaps(entity))
            findings.extend(self._check_gdpr_retention(entity))
            findings.extend(self._check_check_constraints(entity))
            findings.extend(self._check_duplicate_business_keys(entity))

        # Cross-table rules
        findings.extend(self._check_missing_fk_indexes(model))
        findings.extend(self._check_denormalization(model))
        findings.extend(self._check_sp_dynamic_sql(model))
        findings.extend(self._check_orphan_fk_targets(model))

        return findings

    # ------------------------------------------------------------------
    # PII / PCI-DSS
    # ------------------------------------------------------------------

    def _check_pii_pci(self, entity) -> list[SQLGovernanceFinding]:
        findings = []
        for col in entity.fields:
            name = col.name
            if _PCI_COLS.search(name):
                findings.append(SQLGovernanceFinding(
                    rule_type    = "pci_dss",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = name,
                    description  = f"Column '{name}' contains PCI-DSS sensitive data (payment card data).",
                    recommendation = (
                        "Apply field-level encryption. Never store unencrypted card data. "
                        "Consider PCI tokenization. Scope for PCI DSS compliance audit."
                    ),
                    evidence     = f"Column name matches PCI pattern: {name}",
                    source_file  = entity.source_file or col.source_file or "",
                    confidence   = "HIGH",
                    enforcement_layer = "DATABASE + APPLICATION",
                ))
            elif _PII_HIGH_COLS.search(name):
                findings.append(SQLGovernanceFinding(
                    rule_type    = "pii_high",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = name,
                    description  = f"Column '{name}' stores high-risk PII.",
                    recommendation = (
                        "Apply column-level encryption or masking. "
                        "Document in data catalog. Apply GDPR/CCPA access controls."
                    ),
                    evidence     = f"Column name pattern: {name}",
                    source_file  = entity.source_file or "",
                    confidence   = "HIGH",
                ))
            elif _PII_MEDIUM_COLS.search(name):
                findings.append(SQLGovernanceFinding(
                    rule_type    = "pii_medium",
                    severity     = "WARNING",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = name,
                    description  = f"Column '{name}' stores medium-risk PII (contact/identity data).",
                    recommendation = (
                        "Document in data catalog. Apply GDPR right-to-erasure workflow. "
                        "Restrict read access to authorized roles."
                    ),
                    evidence     = f"Column name pattern: {name}",
                    source_file  = entity.source_file or "",
                    confidence   = "HIGH",
                ))
        return findings

    # ------------------------------------------------------------------
    # Missing PRIMARY KEY
    # ------------------------------------------------------------------

    def _check_missing_pk(self, entity) -> list[SQLGovernanceFinding]:
        has_pk = any(f.is_pk for f in entity.fields)
        if has_pk:
            return []
        return [SQLGovernanceFinding(
            rule_type    = "missing_primary_key",
            severity     = "CRITICAL",
            finding_type = "CONFIRMED",
            table        = entity.name,
            description  = f"Table '{entity.name}' has no PRIMARY KEY.",
            recommendation = (
                "Add a PRIMARY KEY constraint. Consider a surrogate INT IDENTITY column. "
                "Tables without PKs cannot be reliably referenced by FKs or replicated."
            ),
            evidence     = f"No PK column detected in {len(entity.fields)} columns",
            source_file  = entity.source_file or "",
            confidence   = "HIGH",
        )]

    # ------------------------------------------------------------------
    # Audit columns
    # ------------------------------------------------------------------

    def _check_audit_columns(self, entity) -> list[SQLGovernanceFinding]:
        col_names = " ".join(f.name for f in entity.fields)
        if _AUDIT_COLS.search(col_names):
            return []
        return [SQLGovernanceFinding(
            rule_type    = "missing_audit_columns",
            severity     = "WARNING",
            finding_type = "INFERRED",
            table        = entity.name,
            description  = f"Table '{entity.name}' has no audit trail columns (created_at/updated_at/created_by).",
            recommendation = (
                "Add CreatedAt DATETIME NOT NULL DEFAULT GETDATE(), "
                "UpdatedAt DATETIME NULL, CreatedBy NVARCHAR(100) NULL. "
                "Consider a shared audit trigger or base table."
            ),
            evidence     = "No audit column pattern detected",
            source_file  = entity.source_file or "",
            confidence   = "MEDIUM",
        )]

    # ------------------------------------------------------------------
    # Nullable critical fields
    # ------------------------------------------------------------------

    def _check_nullable_issues(self, entity) -> list[SQLGovernanceFinding]:
        findings = []
        for col in entity.fields:
            if col.is_pk and col.is_nullable:
                findings.append(SQLGovernanceFinding(
                    rule_type    = "nullable_primary_key",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = col.name,
                    description  = f"Primary key column '{col.name}' is nullable.",
                    recommendation = "Primary key columns must be NOT NULL.",
                    evidence     = f"Column {col.name}: is_pk=True, is_nullable=True",
                    source_file  = entity.source_file or "",
                    confidence   = "HIGH",
                ))
            # FK columns should not be nullable without explicit business reason
            if col.kind.value == "foreign_key" and col.is_nullable:
                findings.append(SQLGovernanceFinding(
                    rule_type    = "nullable_foreign_key",
                    severity     = "NOTE",
                    finding_type = "INFERRED",
                    table        = entity.name,
                    column       = col.name,
                    description  = f"FK column '{col.name}' is nullable (optional relationship).",
                    recommendation = (
                        "Verify this optional relationship is intentional. "
                        "If required, add NOT NULL constraint."
                    ),
                    evidence     = f"FK column with NULL allowed",
                    source_file  = entity.source_file or "",
                    confidence   = "MEDIUM",
                ))
        return findings

    # ------------------------------------------------------------------
    # Soft delete inconsistency
    # ------------------------------------------------------------------

    def _check_soft_delete(self, entity) -> list[SQLGovernanceFinding]:
        col_names = " ".join(f.name for f in entity.fields)
        has_flag  = bool(_SOFT_DELETE_FLAG.search(col_names))
        has_date  = bool(_SOFT_DELETE_DATE.search(col_names))

        if has_flag and not has_date:
            return [SQLGovernanceFinding(
                rule_type    = "soft_delete_incomplete",
                severity     = "WARNING",
                finding_type = "INFERRED",
                table        = entity.name,
                description  = f"Table '{entity.name}' has soft-delete flag but no deleted_at timestamp.",
                recommendation = "Add deleted_at DATETIME NULL column for audit trail and GDPR right-to-erasure timestamps.",
                evidence     = "is_deleted/is_active flag without deleted_at date",
                source_file  = entity.source_file or "",
                confidence   = "MEDIUM",
            )]
        return []

    # ------------------------------------------------------------------
    # Wide table (normalization risk)
    # ------------------------------------------------------------------

    def _check_wide_table(self, entity) -> list[SQLGovernanceFinding]:
        col_count = len(entity.fields)
        if col_count > 30:
            return [SQLGovernanceFinding(
                rule_type    = "wide_table",
                severity     = "NOTE",
                finding_type = "RECOMMENDED",
                table        = entity.name,
                description  = f"Table '{entity.name}' has {col_count} columns (denormalization risk).",
                recommendation = (
                    f"Consider splitting '{entity.name}' into vertical partitions. "
                    "Wide tables may indicate stored redundancy or missing child tables."
                ),
                evidence     = f"{col_count} columns detected",
                source_file  = entity.source_file or "",
                confidence   = "MEDIUM",
            )]
        return []

    # ------------------------------------------------------------------
    # Naming conventions
    # ------------------------------------------------------------------

    def _check_naming_conventions(self, entity) -> list[SQLGovernanceFinding]:
        findings = []
        # Mixed case styles
        snake_count  = sum(1 for f in entity.fields if '_' in f.name)
        camel_count  = sum(1 for f in entity.fields if f.name != f.name.lower() and '_' not in f.name)
        total = len(entity.fields)
        if total > 3 and snake_count > 0 and camel_count > 0:
            findings.append(SQLGovernanceFinding(
                rule_type    = "naming_inconsistency",
                severity     = "NOTE",
                finding_type = "CONFIRMED",
                table        = entity.name,
                description  = f"Table '{entity.name}' mixes snake_case and camelCase column naming.",
                recommendation = "Standardize to a single naming convention (snake_case preferred for SQL).",
                evidence     = f"{snake_count} snake_case, {camel_count} camelCase columns",
                source_file  = entity.source_file or "",
                confidence   = "HIGH",
            ))
        return findings

    # ------------------------------------------------------------------
    # Encryption gap detection  (Priority 6)
    # ------------------------------------------------------------------

    def _check_encryption_gaps(self, entity) -> list[SQLGovernanceFinding]:
        findings = []
        for col in entity.fields:
            if not _PLAIN_SENSITIVE.search(col.name):
                continue
            norm = getattr(col, "normalized_type", "")
            raw  = getattr(col, "raw_type",        "").lower()
            base = raw.split("(")[0].strip()
            if base in _PLAIN_STRING_TYPES or norm in ("string",):
                findings.append(SQLGovernanceFinding(
                    rule_type    = "encryption_gap",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = col.name,
                    description  = (
                        f"Column '{col.name}' stores sensitive data as plain {raw}. "
                        f"No encryption marker detected."
                    ),
                    recommendation = (
                        "Apply column-level encryption (Always Encrypted in SQL Server, "
                        "pgcrypto in PostgreSQL). Never store passwords as plain text — "
                        "use bcrypt/Argon2 hashes. For card data apply PCI tokenization."
                    ),
                    evidence     = f"Sensitive column name pattern + string type {raw}",
                    source_file  = entity.source_file or "",
                    confidence   = "HIGH",
                    enforcement_layer = "DATABASE + APPLICATION",
                ))
        return findings

    # ------------------------------------------------------------------
    # GDPR retention gap  (Priority 6)
    # ------------------------------------------------------------------

    def _check_gdpr_retention(self, entity) -> list[SQLGovernanceFinding]:
        col_names = " ".join(f.name for f in entity.fields)
        has_gdpr_field = bool(_GDPR_PERSONAL.search(col_names))
        has_retention  = bool(_RETENTION_COLS.search(col_names))
        if has_gdpr_field and not has_retention:
            return [SQLGovernanceFinding(
                rule_type    = "gdpr_retention_gap",
                severity     = "WARNING",
                finding_type = "INFERRED",
                table        = entity.name,
                description  = (
                    f"Table '{entity.name}' stores personal data but has no retention/purge "
                    f"date column. GDPR Article 5(1)(e) requires data minimisation."
                ),
                recommendation = (
                    "Add retention_date or expires_at column. "
                    "Implement a scheduled purge job. "
                    "Document legal basis for retention in data catalog."
                ),
                evidence     = "Personal data columns without retention policy column",
                source_file  = entity.source_file or "",
                confidence   = "MEDIUM",
            )]
        return []

    # ------------------------------------------------------------------
    # CHECK constraint analysis  (Priority 5)
    # ------------------------------------------------------------------

    def _check_check_constraints(self, entity) -> list[SQLGovernanceFinding]:
        """Flag critical business columns that have no CHECK constraint."""
        findings = []
        raw = getattr(entity, "raw", {})
        if not isinstance(raw, dict):
            return []
        check_exprs = raw.get("check_constraints", [])

        # Status/type/enum columns with no CHECK
        for col in entity.fields:
            upper = col.name.upper()
            norm  = getattr(col, "normalized_type", "")
            if norm not in ("string", "integer"):
                continue
            is_status = any(k in upper for k in ("STATUS", "TYPE", "STATE", "FLAG", "KIND", "CATEGORY"))
            if is_status and not check_exprs:
                findings.append(SQLGovernanceFinding(
                    rule_type    = "missing_check_constraint",
                    severity     = "NOTE",
                    finding_type = "RECOMMENDED",
                    table        = entity.name,
                    column       = col.name,
                    description  = (
                        f"Column '{col.name}' looks like an enum/status field "
                        f"but no CHECK constraint found on {entity.name}."
                    ),
                    recommendation = (
                        f"Add CHECK ({col.name} IN ('value1','value2',...)) "
                        f"or use a lookup/reference table with a FK constraint."
                    ),
                    evidence     = f"Column name pattern suggests bounded domain: {col.name}",
                    source_file  = entity.source_file or "",
                    confidence   = "LOW",
                ))
                break  # one finding per table
        return findings

    # ------------------------------------------------------------------
    # Duplicate business key detection  (Priority 5)
    # ------------------------------------------------------------------

    def _check_duplicate_business_keys(self, entity) -> list[SQLGovernanceFinding]:
        """
        Detect columns that appear to be natural/business keys
        but have no UNIQUE constraint enforcing them.
        """
        raw = getattr(entity, "raw", {})
        if not isinstance(raw, dict):
            return []
        unique_keys = raw.get("unique_keys", [])
        unique_cols = {c.lower() for uk in unique_keys for c in uk}
        pk_cols     = {c.lower() for c in raw.get("primary_key", [])}

        findings = []
        for col in entity.fields:
            if col.is_pk or col.is_unique:
                continue
            if col.name.lower() in unique_cols or col.name.lower() in pk_cols:
                continue
            if _NATURAL_KEY_PATTERNS.search(col.name):
                findings.append(SQLGovernanceFinding(
                    rule_type    = "unprotected_business_key",
                    severity     = "WARNING",
                    finding_type = "INFERRED",
                    table        = entity.name,
                    column       = col.name,
                    description  = (
                        f"Column '{col.name}' looks like a business/natural key "
                        f"but has no UNIQUE constraint. Duplicate values are possible."
                    ),
                    recommendation = (
                        f"Add UNIQUE constraint: ALTER TABLE {entity.name} "
                        f"ADD CONSTRAINT UQ_{entity.name}_{col.name} UNIQUE ({col.name})."
                    ),
                    evidence     = (
                        f"Column name '{col.name}' matches natural-key pattern; "
                        f"no UNIQUE constraint found in DDL"
                    ),
                    source_file  = entity.source_file or "",
                    confidence   = "MEDIUM",
                ))
        return findings

    # ------------------------------------------------------------------
    # Orphan FK target detection  (Priority 5)
    # ------------------------------------------------------------------

    def _check_orphan_fk_targets(self, model: SemanticModel) -> list[SQLGovernanceFinding]:
        """Detect FK references to tables that are not in the schema (missing/dropped tables)."""
        known = {e.name.lower() for e in model.entities}
        findings = []
        seen: set[tuple] = set()

        for entity in model.entities:
            raw = getattr(entity, "raw", {})
            if not isinstance(raw, dict):
                continue
            for fk in raw.get("foreign_keys", []):
                if not isinstance(fk, dict):
                    continue
                ref = fk.get("references_table", "")
                if not ref or ref.lower() in known:
                    continue
                key = (entity.name.lower(), ref.lower())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(SQLGovernanceFinding(
                    rule_type    = "orphan_fk_reference",
                    severity     = "WARNING",
                    finding_type = "CONFIRMED",
                    table        = entity.name,
                    column       = ", ".join(fk.get("columns", [])),
                    description  = (
                        f"Table '{entity.name}' has FK to '{ref}' "
                        f"but '{ref}' was not found in the schema."
                    ),
                    recommendation = (
                        f"Verify table '{ref}' exists. If cross-database FK, "
                        f"document the dependency. If dropped, remove the orphan FK."
                    ),
                    evidence     = (
                        f"FOREIGN KEY constraint {fk.get('constraint_name','')} "
                        f"references '{ref}' (not in extracted schema)"
                    ),
                    source_file  = fk.get("source_file", entity.source_file or ""),
                    confidence   = "HIGH",
                ))
        return findings

    # ------------------------------------------------------------------
    # Missing FK indexes (cross-table)
    # ------------------------------------------------------------------

    def _check_missing_fk_indexes(self, model: SemanticModel) -> list[SQLGovernanceFinding]:
        findings = []
        indexed_cols: dict[str, set[str]] = {}  # table → indexed column names
        for entity in model.entities:
            indexed_cols[entity.name] = {f.name for f in entity.fields if f.is_indexed or f.is_pk}

        for rel in model.relationships:
            if not rel.via:
                continue
            fk_col = rel.via.split(",")[0].strip()
            table_indexed = indexed_cols.get(rel.source, set())
            if fk_col not in table_indexed:
                findings.append(SQLGovernanceFinding(
                    rule_type    = "missing_fk_index",
                    severity     = "WARNING",
                    finding_type = "INFERRED",
                    table        = rel.source,
                    column       = fk_col,
                    description  = (
                        f"FK column '{fk_col}' on '{rel.source}' references '{rel.target}' "
                        f"but has no index."
                    ),
                    recommendation = (
                        f"CREATE INDEX IX_{rel.source}_{fk_col} ON {rel.source}({fk_col}). "
                        "Missing FK indexes cause full table scans on JOIN operations."
                    ),
                    evidence     = f"FOREIGN KEY → {rel.target} without index on {fk_col}",
                    source_file  = rel.source_file or "",
                    confidence   = "HIGH",
                ))
        return findings

    # ------------------------------------------------------------------
    # Denormalization (repeated fields across tables)
    # ------------------------------------------------------------------

    def _check_denormalization(self, model: SemanticModel) -> list[SQLGovernanceFinding]:
        from collections import defaultdict
        col_table_map: dict[str, list[str]] = defaultdict(list)

        for entity in model.entities:
            for col in entity.fields:
                if col.is_pk or col.kind.value == "foreign_key":
                    continue
                if len(col.name) < 4:
                    continue
                col_table_map[col.name.lower()].append(entity.name)

        findings = []
        for col_name, tables in col_table_map.items():
            if len(tables) >= 4:
                findings.append(SQLGovernanceFinding(
                    rule_type    = "denormalization_risk",
                    severity     = "NOTE",
                    finding_type = "INFERRED",
                    table        = ", ".join(tables[:5]),
                    column       = col_name,
                    description  = (
                        f"Column '{col_name}' appears in {len(tables)} tables: "
                        f"{', '.join(tables[:5])}."
                    ),
                    recommendation = (
                        "Review if this represents intentional denormalization or "
                        "if a shared reference table / normalization is appropriate."
                    ),
                    evidence     = f"Same column name in {len(tables)} tables",
                    confidence   = "MEDIUM",
                ))
        return findings

    # ------------------------------------------------------------------
    # Stored procedure dynamic SQL risk
    # ------------------------------------------------------------------

    def _check_sp_dynamic_sql(self, model: SemanticModel) -> list[SQLGovernanceFinding]:
        findings = []
        for hdl in model.handlers:
            raw = getattr(hdl, "raw", {})
            if not isinstance(raw, dict):
                continue
            if raw.get("dynamic_sql_risk") == "HIGH":
                findings.append(SQLGovernanceFinding(
                    rule_type    = "sql_injection_risk",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    table        = "",
                    column       = "",
                    description  = (
                        f"Stored procedure '{hdl.name}' builds dynamic SQL via string concatenation."
                    ),
                    recommendation = (
                        "Use sp_executesql with parameterized queries instead of string concatenation. "
                        "Validate all user inputs before incorporating into dynamic SQL."
                    ),
                    evidence     = "String concatenation in EXEC/sp_executesql call",
                    source_file  = hdl.source_file or "",
                    confidence   = "HIGH",
                    enforcement_layer = "APPLICATION + DATABASE",
                ))
        return findings

    # ------------------------------------------------------------------
    # Persist and report
    # ------------------------------------------------------------------

    def save(
        self,
        model: SemanticModel,
        output_dir: str | Path,
        review_dir: str | Path,
    ) -> list[SQLGovernanceFinding]:
        findings = self.analyze(model)
        out = Path(output_dir)
        rev = Path(review_dir)
        out.mkdir(parents=True, exist_ok=True)
        rev.mkdir(parents=True, exist_ok=True)

        (out / "sql_governance.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "finding_count": len(findings),
                    "severity_counts": {
                        "CRITICAL": sum(1 for f in findings if f.severity == "CRITICAL"),
                        "WARNING":  sum(1 for f in findings if f.severity == "WARNING"),
                        "NOTE":     sum(1 for f in findings if f.severity == "NOTE"),
                    },
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2, default=str,
            ),
            encoding="utf-8",
        )

        self._write_governance_report(findings, rev)
        self._write_quality_report(findings, model, rev)

        crit = sum(1 for f in findings if f.severity == "CRITICAL")
        print(f"[SQLGovernanceEngine] {len(findings)} findings ({crit} CRITICAL) -> sql_governance.json")
        return findings

    def _write_governance_report(
        self, findings: list[SQLGovernanceFinding], review_dir: Path
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        lines = [
            "# Legacy Database Governance Report\n\n",
            f"_Generated: {ts}_\n\n",
            f"**Total Findings:** {len(findings)}\n\n",
        ]
        for sev in ("CRITICAL", "WARNING", "NOTE"):
            grp = [f for f in findings if f.severity == sev]
            if not grp:
                continue
            lines.append(f"## {sev} ({len(grp)})\n\n")
            lines.append("| Table | Column | Rule | Description | Recommendation |\n")
            lines.append("|-------|--------|------|-------------|----------------|\n")
            for f in grp:
                desc = f.description.replace("|", "\\|")[:80]
                rec  = f.recommendation.replace("|", "\\|")[:80]
                lines.append(
                    f"| `CONFIRMED` `{f.table}` | `{f.column}` "
                    f"| {f.rule_type} | {desc} | {rec} |\n"
                )
            lines.append("\n")
        (review_dir / "LEGACY_GOVERNANCE_REPORT.md").write_text("".join(lines), encoding="utf-8")

    def _write_quality_report(
        self,
        findings: list[SQLGovernanceFinding],
        model: SemanticModel,
        review_dir: Path,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        tables_with_issues = {f.table for f in findings}
        # Score: weighted penalty per severity; cap deductions at 100
        n_tables = max(len(model.entities), 1)
        crit_count = sum(1 for f in findings if f.severity == "CRITICAL")
        warn_count = sum(1 for f in findings if f.severity == "WARNING")
        note_count = sum(1 for f in findings if f.severity == "NOTE")
        deduction = (crit_count * 5 + warn_count * 2 + note_count * 1) / n_tables
        quality_score = max(0, round(100 - deduction))

        lines = [
            "# Data Quality Report\n\n",
            f"_Generated: {ts}_\n\n",
            f"**Quality Score:** {quality_score}/100\n\n",
            f"**Tables Analyzed:** {len(model.entities)}\n",
            f"**Columns Analyzed:** {sum(len(e.fields) for e in model.entities)}\n",
            f"**Tables with Issues:** {len(tables_with_issues)}\n\n",
            "## Issue Breakdown\n\n",
            "| Rule Type | Count | Severity |\n",
            "|-----------|-------|----------|\n",
        ]
        rule_counts: dict[str, tuple[int, str]] = {}
        for f in findings:
            rule_counts.setdefault(f.rule_type, (0, f.severity))
            count, sev = rule_counts[f.rule_type]
            rule_counts[f.rule_type] = (count + 1, sev)
        for rule, (count, sev) in sorted(rule_counts.items(), key=lambda x: -x[1][0]):
            lines.append(f"| {rule} | {count} | {sev} |\n")

        lines.append("\n## PII / PCI Summary\n\n")
        pii_findings = [f for f in findings if "pii" in f.rule_type or "pci" in f.rule_type]
        if pii_findings:
            lines.append("| Table | Column | Risk Level |\n")
            lines.append("|-------|--------|------------|\n")
            for f in pii_findings:
                lines.append(f"| `{f.table}` | `{f.column}` | {f.rule_type} |\n")
        else:
            lines.append("_No PII/PCI columns detected._\n")

        (review_dir / "DATA_QUALITY_REPORT.md").write_text("".join(lines), encoding="utf-8")
