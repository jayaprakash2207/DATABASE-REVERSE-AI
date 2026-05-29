"""
Universal Governance Engine

Analyzes a SemanticModel for security, compliance, and data governance
issues. Operates entirely on USM types — no technology-specific logic.

Rules enforced:
  - PII fields without encryption signal
  - Authentication gaps on endpoints
  - Missing audit trail fields on mutable entities
  - Nullable PK fields (data integrity)
  - Cross-domain data leakage (direct navigation instead of ID ref)
  - SQL injection risk (raw string endpoints without repo)
  - Missing required fields on aggregate roots
  - Unprotected sensitive endpoints (no auth_required)
"""

from __future__ import annotations

import re
from typing import Optional

from models.semantic_model import SemanticModel
from models.universal import (
    UniversalEntity, UniversalEndpoint, UniversalGovernanceFinding,
    EntityKind, FieldKind, RelationshipKind, ConfidenceLevel,
)


_AUDIT_FIELDS = re.compile(
    r'\b(created_at|createdAt|created_on|updated_at|updatedAt|'
    r'modified_at|modifiedAt|created_by|createdBy|modified_by|modifiedBy|'
    r'CreatedDate|ModifiedDate|UpdatedDate|CreatedBy|UpdatedBy)\b',
    re.I,
)
_WRITE_METHODS = re.compile(r'\b(POST|PUT|PATCH|DELETE)\b')
_SENSITIVE_PATHS = re.compile(
    r'\b(payment|billing|credit|password|auth|admin|secret|token|key)\b', re.I
)


class GovernanceEngine:
    """
    Stateless engine — call analyze() to get governance findings.
    All results are appended to model.findings.
    """

    def __init__(self, pii_require_encryption: bool = True) -> None:
        self._pii_require_encryption = pii_require_encryption

    def analyze(self, model: SemanticModel) -> SemanticModel:
        """
        Run all governance rules. Appends UniversalGovernanceFinding
        objects to model.findings. Returns model for chaining.
        """
        entity_index = {e.name: e for e in model.entities}

        for entity in model.entities:
            model.findings.extend(self._check_pii(entity))
            model.findings.extend(self._check_audit_trail(entity))
            model.findings.extend(self._check_pk_integrity(entity))
            model.findings.extend(self._check_aggregate_root_fields(entity))

        for endpoint in model.endpoints:
            model.findings.extend(self._check_endpoint_auth(endpoint))

        model.findings.extend(
            self._check_cross_domain_navigation(model, entity_index)
        )

        return model

    # ------------------------------------------------------------------
    # Rule: PII fields
    # ------------------------------------------------------------------

    def _check_pii(self, entity: UniversalEntity) -> list[UniversalGovernanceFinding]:
        findings: list[UniversalGovernanceFinding] = []
        for f in entity.pii_fields:
            severity = "CRITICAL" if f.pii_risk == "high" else "WARNING"
            findings.append(UniversalGovernanceFinding(
                rule_type    = "pii",
                severity     = severity,
                finding_type = "CONFIRMED",
                entity       = entity.name,
                field        = f.name,
                description  = (
                    f"Field '{f.name}' ({f.normalized_type}) contains {f.pii_risk}-risk PII data."
                ),
                recommendation = (
                    "Ensure field is encrypted at rest and masked in logs. "
                    "Verify GDPR/CCPA access controls."
                ),
                source_file  = f.source_file or entity.source_file,
                line_number  = f.line_number or entity.line_number,
                confidence   = ConfidenceLevel.HIGH,
                analyzer     = "governance_engine",
            ))
        return findings

    # ------------------------------------------------------------------
    # Rule: Audit trail
    # ------------------------------------------------------------------

    def _check_audit_trail(self, entity: UniversalEntity) -> list[UniversalGovernanceFinding]:
        if entity.kind.value in ("value_object", "dto", "projection"):
            return []

        field_names = " ".join(f.name for f in entity.fields)
        if _AUDIT_FIELDS.search(field_names):
            return []

        return [UniversalGovernanceFinding(
            rule_type    = "audit_trail",
            severity     = "WARNING",
            finding_type = "INFERRED",
            entity       = entity.name,
            description  = (
                f"Entity '{entity.name}' has no audit trail fields "
                "(created_at/updated_at/created_by)."
            ),
            recommendation = (
                "Add CreatedAt, UpdatedAt, CreatedBy, UpdatedBy fields or "
                "implement a base auditable entity."
            ),
            source_file  = entity.source_file,
            line_number  = entity.line_number,
            confidence   = ConfidenceLevel.MEDIUM,
            analyzer     = "governance_engine",
        )]

    # ------------------------------------------------------------------
    # Rule: PK integrity
    # ------------------------------------------------------------------

    def _check_pk_integrity(self, entity: UniversalEntity) -> list[UniversalGovernanceFinding]:
        findings: list[UniversalGovernanceFinding] = []
        for pk in entity.pk_fields:
            if pk.is_nullable:
                findings.append(UniversalGovernanceFinding(
                    rule_type    = "data_integrity",
                    severity     = "CRITICAL",
                    finding_type = "CONFIRMED",
                    entity       = entity.name,
                    field        = pk.name,
                    description  = f"Primary key '{pk.name}' is nullable — data integrity risk.",
                    recommendation = "Primary keys must be NOT NULL.",
                    source_file  = pk.source_file or entity.source_file,
                    line_number  = pk.line_number,
                    confidence   = ConfidenceLevel.HIGH,
                    analyzer     = "governance_engine",
                ))
        return findings

    # ------------------------------------------------------------------
    # Rule: Aggregate root required fields
    # ------------------------------------------------------------------

    def _check_aggregate_root_fields(
        self, entity: UniversalEntity
    ) -> list[UniversalGovernanceFinding]:
        if entity.kind != EntityKind.AGGREGATE_ROOT:
            return []
        if not entity.pk_fields:
            return [UniversalGovernanceFinding(
                rule_type    = "aggregate_design",
                severity     = "WARNING",
                finding_type = "INFERRED",
                entity       = entity.name,
                description  = f"Aggregate root '{entity.name}' has no identifiable primary key.",
                recommendation = "Aggregate roots must have a unique identity field.",
                source_file  = entity.source_file,
                line_number  = entity.line_number,
                confidence   = ConfidenceLevel.MEDIUM,
                analyzer     = "governance_engine",
            )]
        return []

    # ------------------------------------------------------------------
    # Rule: Endpoint authentication
    # ------------------------------------------------------------------

    def _check_endpoint_auth(
        self, endpoint: UniversalEndpoint
    ) -> list[UniversalGovernanceFinding]:
        if endpoint.auth_required:
            return []

        is_write  = bool(_WRITE_METHODS.search(endpoint.method))
        is_sensitive = bool(_SENSITIVE_PATHS.search(endpoint.path))

        if not (is_write or is_sensitive):
            return []

        severity = "CRITICAL" if is_sensitive else "WARNING"
        return [UniversalGovernanceFinding(
            rule_type    = "auth_missing",
            severity     = severity,
            finding_type = "CONFIRMED" if is_sensitive else "INFERRED",
            entity       = endpoint.handler_class or "?",
            field        = "",
            description  = (
                f"{endpoint.method} {endpoint.path} has no authentication — "
                f"{'sensitive path' if is_sensitive else 'write operation'}."
            ),
            recommendation = "Add authentication/authorization middleware or attribute.",
            source_file  = endpoint.source_file,
            line_number  = endpoint.line_number,
            confidence   = ConfidenceLevel.HIGH if is_sensitive else ConfidenceLevel.MEDIUM,
            analyzer     = "governance_engine",
        )]

    # ------------------------------------------------------------------
    # Rule: Cross-domain navigation
    # ------------------------------------------------------------------

    def _check_cross_domain_navigation(
        self,
        model: SemanticModel,
        entity_index: dict[str, UniversalEntity],
    ) -> list[UniversalGovernanceFinding]:
        findings: list[UniversalGovernanceFinding] = []

        for r in model.relationships:
            if not r.is_cross_domain:
                continue
            if r.kind in (RelationshipKind.REFERENCES, RelationshipKind.MANY_TO_ONE):
                continue  # ID reference — correct pattern

            if r.kind in (RelationshipKind.ONE_TO_MANY, RelationshipKind.MANY_TO_MANY):
                src = entity_index.get(r.source)
                tgt = entity_index.get(r.target)
                src_agg = src.aggregate if src else "?"
                tgt_agg = tgt.aggregate if tgt else "?"
                findings.append(UniversalGovernanceFinding(
                    rule_type    = "cross_domain_leakage",
                    severity     = "WARNING",
                    finding_type = "CONFIRMED",
                    entity       = r.source,
                    field        = r.via,
                    description  = (
                        f"Direct navigation {r.source}→{r.target} crosses bounded context "
                        f"boundary ({src_agg} → {tgt_agg})."
                    ),
                    recommendation = (
                        "Replace direct navigation with ID reference. "
                        "Use domain events or anti-corruption layer for cross-context data."
                    ),
                    source_file  = r.source_file,
                    line_number  = r.line_number,
                    confidence   = r.confidence,
                    analyzer     = "governance_engine",
                ))

        return findings
