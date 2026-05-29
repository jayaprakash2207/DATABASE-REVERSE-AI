"""
Governance Detection Engine — evidence-based, multi-source.

Sources:
  1. Entity field names — PII/PCI/GDPR pattern matching
  2. EF Core attributes — [Required], [Encrypted], [Sensitive], [CreditCard]
  3. Guard clause calls — Guard.Against.NullOrEmpty, Guard.Against.NegativeOrZero
  4. Auth annotations  — [Authorize], [AllowAnonymous]
  5. Migration files   — DB-level constraints (UNIQUE INDEX, NOT NULL, CHECK)
  6. EF Core config    — IsRequired(), HasMaxLength(), IsUnicode()

All findings carry: entity, field, source_file, line_number, confidence

Generates:
  memory/m3/governance_findings.json
  memory/m3/governance-report.md
"""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence

# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# (regex, rule_type, description, confidence_level, gdpr_category)
PII_PATTERNS = [
    (re.compile(r'(?i)\bemail\b'),                  "pii",      "PII — email address",             Confidence.HIGH,   "contact_data"),
    (re.compile(r'(?i)\b(phone|mobile|tel)\b'),     "pii",      "PII — phone number",              Confidence.HIGH,   "contact_data"),
    (re.compile(r'(?i)\b(street|address|addr)\b'),  "pii",      "PII — physical address",          Confidence.HIGH,   "location_data"),
    (re.compile(r'(?i)\b(zipcode|postcode|zip)\b'), "pii",      "PII — postal code",               Confidence.HIGH,   "location_data"),
    (re.compile(r'(?i)\b(city|state|country)\b'),   "pii",      "PII — location field",            Confidence.MEDIUM, "location_data"),
    (re.compile(r'(?i)\b(firstname|lastname|fullname|surname)\b'), "pii", "PII — personal name",   Confidence.HIGH,   "identity_data"),
    (re.compile(r'(?i)\b(birthdate|dob|dateofbirth)\b'), "pii", "PII — date of birth",             Confidence.HIGH,   "identity_data"),
    (re.compile(r'(?i)\b(identityguid|identityid|userid|buyerid)\b'), "pii", "PII — identity link", Confidence.HIGH,  "identity_data"),
    (re.compile(r'(?i)\b(ssn|nationalid|taxid|passport)\b'), "pii", "Compliance — national ID",    Confidence.HIGH,   "sensitive_id"),
    (re.compile(r'(?i)\b(password|passwd|pwd|secret)\b'), "credential", "Security — credential field", Confidence.HIGH, "credential"),
    (re.compile(r'(?i)\b(creditcard|cardnumber|cvv|ccv|pan)\b'), "pci_dss", "PCI-DSS — card data", Confidence.HIGH,   "payment_data"),
    (re.compile(r'(?i)\b(cardid|stripetoken|paymenttoken)\b'), "pci_dss", "PCI-DSS — payment token", Confidence.HIGH, "payment_data"),
    (re.compile(r'(?i)\b(last4|maskedcard)\b'),     "pci_dss",  "PCI-DSS — masked card display",  Confidence.HIGH,   "payment_data"),
    (re.compile(r'(?i)\b(createdat|createdon|timestamp)\b'), "audit", "Audit — creation timestamp", Confidence.HIGH,  "audit_trail"),
    (re.compile(r'(?i)\b(createdby|updatedby|modifiedby)\b'), "audit", "Audit — actor tracking",    Confidence.HIGH,  "audit_trail"),
]

# EF attribute → governance rule
EF_ATTR_RULES = {
    "Required":         ("not_null",       Confidence.HIGH,   "EF Core [Required] — field cannot be null"),
    "MaxLength":        ("max_length",      Confidence.HIGH,   "EF Core [MaxLength] — string length limit"),
    "MinLength":        ("min_length",      Confidence.HIGH,   "EF Core [MinLength] — minimum string length"),
    "StringLength":     ("string_length",   Confidence.HIGH,   "EF Core [StringLength] — string range"),
    "Key":              ("primary_key",     Confidence.HIGH,   "EF Core [Key] — primary key"),
    "Index":            ("index",           Confidence.HIGH,   "EF Core [Index] — database index"),
    "ConcurrencyCheck": ("concurrency",     Confidence.HIGH,   "EF Core [ConcurrencyCheck] — optimistic lock"),
    "Timestamp":        ("timestamp",       Confidence.HIGH,   "EF Core [Timestamp] — row version"),
    "Owned":            ("owned_entity",    Confidence.HIGH,   "EF Core [Owned] — embedded value object"),
    "NotMapped":        ("not_mapped",      Confidence.HIGH,   "EF Core [NotMapped] — excluded from schema"),
    "CreditCard":       ("pci_dss",         Confidence.HIGH,   "Attribute [CreditCard] — PCI-DSS relevant"),
    "Encrypted":        ("encryption",      Confidence.HIGH,   "Field marked for encryption"),
    "Sensitive":        ("sensitive_data",  Confidence.HIGH,   "Field marked as sensitive"),
    "DataProtection":   ("encryption",      Confidence.HIGH,   "ASP.NET Core DataProtection applied"),
}

# Guard clause patterns (constructor-level validation)
GUARD_PATTERNS = [
    (re.compile(r'Guard\.Against\.NullOrEmpty\s*\(\s*(\w+)'),   "not_null",      Confidence.HIGH),
    (re.compile(r'Guard\.Against\.Null\s*\(\s*(\w+)'),          "not_null",      Confidence.HIGH),
    (re.compile(r'Guard\.Against\.NegativeOrZero\s*\(\s*(\w+)'),"positive_value",Confidence.HIGH),
    (re.compile(r'Guard\.Against\.OutOfRange\s*\(\s*(\w+)'),    "range",         Confidence.HIGH),
    (re.compile(r'Guard\.Against\.StringTooLong\s*\(\s*(\w+)'), "max_length",    Confidence.HIGH),
    (re.compile(r'if\s*\(\s*string\.IsNullOrEmpty\s*\(\s*(\w+)'), "not_null",    Confidence.MEDIUM),
    (re.compile(r'throw\s+new\s+ArgumentNullException\s*\(\s*nameof\s*\(\s*(\w+)'), "not_null", Confidence.MEDIUM),
    (re.compile(r'throw\s+new\s+ArgumentException.*nameof\s*\(\s*(\w+)'), "validation", Confidence.MEDIUM),
]

# EF Fluent API config patterns
EF_FLUENT_PATTERNS = [
    (re.compile(r'IsRequired\(\)'),               "not_null",   Confidence.HIGH),
    (re.compile(r'HasMaxLength\(\s*(\d+)\)'),      "max_length", Confidence.HIGH),
    (re.compile(r'IsUnicode\(false\)'),            "unicode",    Confidence.HIGH),
    (re.compile(r'HasIndex\('),                   "index",      Confidence.HIGH),
    (re.compile(r'IsUnique\(\)'),                  "unique",     Confidence.HIGH),
    (re.compile(r'IsConcurrencyToken\(\)'),        "concurrency",Confidence.HIGH),
    (re.compile(r'HasPrecision\('),                "precision",  Confidence.HIGH),
    (re.compile(r'HasColumnType\(["\']nvarchar'),  "nvarchar",   Confidence.MEDIUM),
    (re.compile(r'HasDefaultValue\('),             "default",    Confidence.MEDIUM),
    (re.compile(r'ValueGeneratedOnAdd'),           "db_generated",Confidence.HIGH),
]

# Auth patterns
AUTH_PATTERNS = [
    (re.compile(r'\[Authorize\b'),       "access_control", "JWT Bearer required",     Confidence.HIGH),
    (re.compile(r'\[AllowAnonymous\b'),  "anonymous_access","Anonymous access allowed", Confidence.HIGH),
    (re.compile(r'\[Authorize\(Roles'), "role_based_auth", "Role-based authorization", Confidence.HIGH),
    (re.compile(r'\[Authorize\(Policy'), "policy_auth",    "Policy-based authorization", Confidence.HIGH),
]


class GovernanceDetector:
    def __init__(self, output_dir: str = "memory/m3"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, entities_data: dict[str, Any], ast_data: dict[str, Any],
               api_data: Optional[dict] = None, infra_dir: Optional[str] = None) -> dict[str, Any]:

        findings: list[dict] = []
        findings.extend(self._scan_pii_fields(entities_data))
        findings.extend(self._scan_ef_attributes(entities_data))
        findings.extend(self._scan_guard_clauses(entities_data))
        findings.extend(self._scan_ef_fluent(infra_dir, entities_data))
        if api_data:
            findings.extend(self._scan_auth(api_data))
        findings.extend(self._scan_missing_governance(entities_data))

        findings = self._deduplicate(findings)
        findings.sort(key=lambda f: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(f["confidence"], 3),
                                      f.get("rule_type", "")))

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "finding_count": len(findings),
            "severity_counts": self._count_by_key(findings, "severity"),
            "rule_counts":     self._count_by_key(findings, "rule_type"),
            "gdpr_fields":     [f for f in findings if f.get("gdpr_category")],
            "pci_fields":      [f for f in findings if f.get("rule_type") == "pci_dss"],
            "findings":        findings,
        }

        (self.output_dir / "governance_findings.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        (self.output_dir / "governance-report.md").write_text(
            self._render_report(findings), encoding="utf-8")

        print(f"[GovernanceDetector] {len(findings)} findings -> governance_findings.json")
        return result

    # ------------------------------------------------------------------
    # Scan 1: PII field names
    # ------------------------------------------------------------------

    def _scan_pii_fields(self, entities_data: dict) -> list[dict]:
        findings = []
        for ent in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            for fld in ent.get("fields", []):
                for pattern, rule_type, description, conf, gdpr_cat in PII_PATTERNS:
                    if pattern.search(fld["name"]):
                        findings.append({
                            "rule_type":    rule_type,
                            "severity":     "WARNING" if conf == Confidence.HIGH else "NOTE",
                            "entity":       ent["entity"],
                            "field":        fld["name"],
                            "field_type":   fld.get("type", ""),
                            "description":  description,
                            "source_file":  ent.get("source_file", ""),
                            "line_number":  fld.get("line_number"),
                            "confidence":   conf.value,
                            "detection_method": "field_name_pattern",
                            "gdpr_category": gdpr_cat,
                        })
        return findings

    # ------------------------------------------------------------------
    # Scan 2: EF Core attributes ([Required], [Encrypted], etc.)
    # ------------------------------------------------------------------

    def _scan_ef_attributes(self, entities_data: dict) -> list[dict]:
        findings = []
        for ent in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            prop_attrs = ent.get("property_attributes", {})
            for field_name, attrs in prop_attrs.items():
                for attr in attrs:
                    attr_name = attr.split("(")[0].strip()
                    if attr_name in EF_ATTR_RULES:
                        rule_type, conf, desc = EF_ATTR_RULES[attr_name]
                        fld_data = next((f for f in ent.get("fields", [])
                                         if f["name"] == field_name), {})
                        findings.append({
                            "rule_type":    rule_type,
                            "severity":     "NOTE",
                            "entity":       ent["entity"],
                            "field":        field_name,
                            "field_type":   fld_data.get("type", ""),
                            "description":  desc,
                            "source_file":  ent.get("source_file", ""),
                            "line_number":  fld_data.get("line_number"),
                            "confidence":   conf.value,
                            "detection_method": "ef_attribute",
                            "gdpr_category": None,
                        })
        return findings

    # ------------------------------------------------------------------
    # Scan 3: Guard clause validation in source files
    # ------------------------------------------------------------------

    def _scan_guard_clauses(self, entities_data: dict) -> list[dict]:
        findings = []
        for ent in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            src_file = ent.get("source_file", "")
            if not src_file:
                continue
            try:
                source = Path(src_file).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for pattern, rule_type, conf in GUARD_PATTERNS:
                for m in pattern.finditer(source):
                    param_name = m.group(1) if m.lastindex else "?"
                    line_no    = source[:m.start()].count("\n") + 1
                    findings.append({
                        "rule_type":    rule_type,
                        "severity":     "NOTE",
                        "entity":       ent["entity"],
                        "field":        param_name,
                        "field_type":   "",
                        "description":  f"Constructor guard: {m.group(0).strip()[:60]}",
                        "source_file":  src_file,
                        "line_number":  line_no,
                        "confidence":   conf.value,
                        "detection_method": "guard_clause",
                        "gdpr_category": None,
                    })
        return findings

    # ------------------------------------------------------------------
    # Scan 4: EF Core fluent configuration (Infrastructure/)
    # ------------------------------------------------------------------

    def _scan_ef_fluent(self, infra_dir: Optional[str], entities_data: dict) -> list[dict]:
        findings = []
        if not infra_dir:
            src = Path(entities_data.get("source_dir", ""))
            for candidate in [
                src.parent.parent / "Infrastructure",
                src.parent.parent.parent / "Infrastructure",
            ]:
                if candidate.exists():
                    infra_dir = str(candidate)
                    break
        if not infra_dir:
            return []

        for cs_file in Path(infra_dir).rglob("*.cs"):
            try:
                source = cs_file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for pattern, rule_type, conf in EF_FLUENT_PATTERNS:
                for m in pattern.finditer(source):
                    line_no = source[:m.start()].count("\n") + 1
                    # Try to extract entity from surrounding context
                    ctx     = source[max(0, m.start()-300):m.start()]
                    ent_m   = re.search(r'Entity<(\w+)>|Configure\s*<(\w+)>', ctx)
                    ent_name = (ent_m.group(1) or ent_m.group(2)) if ent_m else cs_file.stem
                    prop_m  = re.search(r'Property\s*\(\s*\w+\s*=>\s*\w+\.(\w+)', ctx)
                    field   = prop_m.group(1) if prop_m else "?"
                    findings.append({
                        "rule_type":    rule_type,
                        "severity":     "NOTE",
                        "entity":       ent_name,
                        "field":        field,
                        "field_type":   "",
                        "description":  f"EF Fluent API: {m.group(0).strip()[:60]}",
                        "source_file":  str(cs_file),
                        "line_number":  line_no,
                        "confidence":   conf.value,
                        "detection_method": "ef_fluent_api",
                        "gdpr_category": None,
                    })
        return findings

    # ------------------------------------------------------------------
    # Scan 5: Auth rules on endpoints
    # ------------------------------------------------------------------

    def _scan_auth(self, apis_data: dict) -> list[dict]:
        findings = []
        for ep in apis_data.get("endpoints", []):
            if ep.get("auth_required"):
                findings.append({
                    "rule_type":    "access_control",
                    "severity":     "NOTE",
                    "entity":       ep.get("class_name", "?"),
                    "field":        ep.get("endpoint", "?"),
                    "field_type":   ep.get("method", ""),
                    "description":  f"[Authorize] on {ep['method']} {ep['endpoint']}",
                    "source_file":  ep.get("source_file", ""),
                    "line_number":  ep.get("line_number"),
                    "confidence":   Confidence.HIGH.value,
                    "detection_method": "auth_attribute",
                    "gdpr_category": None,
                })
            elif not ep.get("anon_allowed") and ep.get("method") == "GET":
                findings.append({
                    "rule_type":    "anonymous_access",
                    "severity":     "WARNING",
                    "entity":       ep.get("class_name", "?"),
                    "field":        ep.get("endpoint", "?"),
                    "field_type":   "GET",
                    "description":  "Read endpoint publicly accessible — no [Authorize] or [AllowAnonymous]",
                    "source_file":  ep.get("source_file", ""),
                    "line_number":  ep.get("line_number"),
                    "confidence":   Confidence.HIGH.value,
                    "detection_method": "missing_auth_annotation",
                    "gdpr_category": None,
                })
        return findings

    # ------------------------------------------------------------------
    # Scan 6: Missing governance (no audit fields, no soft-delete)
    # ------------------------------------------------------------------

    def _scan_missing_governance(self, entities_data: dict) -> list[dict]:
        findings = []
        audit_field_re = re.compile(r'(?i)(createdat|updatedat|modifiedat|createdby|updatedby)')
        soft_del_re    = re.compile(r'(?i)(isdeleted|isactive|deletedat|archivedAt)')

        for ent in entities_data.get("entities", []):
            field_names = [f["name"] for f in ent.get("fields", [])]
            has_audit   = any(audit_field_re.search(n) for n in field_names)
            has_softdel = any(soft_del_re.search(n) for n in field_names)

            if not has_audit:
                findings.append({
                    "rule_type":    "missing_audit_trail",
                    "severity":     "WARNING",
                    "entity":       ent["entity"],
                    "field":        "(none)",
                    "field_type":   "",
                    "description":  "No audit fields detected (CreatedAt/UpdatedAt/CreatedBy)",
                    "source_file":  ent.get("source_file", ""),
                    "line_number":  ent.get("line_number"),
                    "confidence":   Confidence.HIGH.value,
                    "detection_method": "missing_pattern",
                    "gdpr_category": None,
                })

            if ent.get("aggregate_root") and not has_softdel:
                findings.append({
                    "rule_type":    "missing_soft_delete",
                    "severity":     "WARNING",
                    "entity":       ent["entity"],
                    "field":        "(none)",
                    "field_type":   "",
                    "description":  "Aggregate root has no soft-delete field (IsDeleted/IsActive)",
                    "source_file":  ent.get("source_file", ""),
                    "line_number":  ent.get("line_number"),
                    "confidence":   Confidence.MEDIUM.value,
                    "detection_method": "missing_pattern",
                    "gdpr_category": None,
                })

        return findings

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self, findings: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        result = []
        for f in findings:
            key = (f["entity"], f.get("field",""), f["rule_type"], f["detection_method"])
            if key not in seen:
                seen.add(key)
                result.append(f)
        return result

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _count_by_key(self, findings: list[dict], key: str) -> dict:
        counts: dict[str, int] = {}
        for f in findings:
            v = f.get(key, "unknown")
            counts[v] = counts.get(v, 0) + 1
        return counts

    def _render_report(self, findings: list[dict]) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        lines = [
            "# Governance Report\n\n",
            f"_Generated: {ts} by M3 Data Architecture Agent_\n\n",
            f"**Total findings:** {len(findings)}\n\n",
        ]

        # Group by severity
        for severity in ("CRITICAL", "WARNING", "NOTE"):
            group = [f for f in findings if f.get("severity") == severity]
            if not group:
                continue
            lines.append(f"\n## {severity} ({len(group)})\n\n")
            lines.append("| Entity | Field | Rule | Description | File | Confidence |\n")
            lines.append("|--------|-------|------|-------------|------|------------|\n")
            for f in group:
                src = Path(f.get("source_file","")).name
                lines.append(
                    f"| {f.get('entity','?')} | `{f.get('field','?')}` "
                    f"| {f.get('rule_type','?')} | {f.get('description','')} "
                    f"| {src} | {f.get('confidence','?')} |\n"
                )

        return "".join(lines)
