"""
Centralized Confidence Engine — single source of truth for all confidence scoring.

Evidence weights (deterministic):
  AST_DIRECT         1.00  — tree-sitter property/class node
  EF_FLUENT_API      1.00  — IEntityTypeConfiguration OwnsOne/HasMany/etc.
  DBCONTEXT_MAPPING  1.00  — DbSet<T> or OnModelCreating explicit mapping
  GUARD_CLAUSE       0.90  — constructor null guard / throw
  DATA_ANNOTATION    0.85  — [Required], [MaxLength], [ForeignKey] attribute
  COLLECTION_NAV     0.80  — ICollection<T> / IReadOnlyCollection<T> property
  FK_NAMING_CONV     0.70  — XxxId property matching entity class name
  REPO_INFERENCE     0.65  — IRepository<T> type argument
  CTOR_INJECTION     0.60  — constructor parameter injection
  NAMING_HEURISTIC   0.40  — class/property name pattern match only
  SEMANTIC_GUESS     0.25  — AI/heuristic inference without source anchor

Thresholds:
  >= 0.80 → HIGH
  >= 0.55 → MEDIUM
  < 0.55  → LOW

Usage:
    from core.confidence_engine import ConfidenceEngine, EvidenceType
    engine = ConfidenceEngine()
    score  = engine.score([
        engine.evidence(EvidenceType.AST_DIRECT, "CatalogItem.cs", 45),
        engine.evidence(EvidenceType.EF_FLUENT_API, "OrderConfiguration.cs", 12),
    ])
    print(score.level, score.numeric)   # HIGH  1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class EvidenceType(str, Enum):
    AST_DIRECT         = "ast_direct"
    EF_FLUENT_API      = "ef_fluent_api"
    DBCONTEXT_MAPPING  = "dbcontext_mapping"
    GUARD_CLAUSE       = "guard_clause"
    DATA_ANNOTATION    = "data_annotation"
    COLLECTION_NAV     = "collection_nav"
    FK_NAMING_CONV     = "fk_naming_conv"
    REPO_INFERENCE     = "repo_inference"
    CTOR_INJECTION     = "ctor_injection"
    NAMING_HEURISTIC   = "naming_heuristic"
    SEMANTIC_GUESS     = "semantic_guess"


_WEIGHTS: dict[EvidenceType, float] = {
    EvidenceType.AST_DIRECT:        1.00,
    EvidenceType.EF_FLUENT_API:     1.00,
    EvidenceType.DBCONTEXT_MAPPING: 1.00,
    EvidenceType.GUARD_CLAUSE:      0.90,
    EvidenceType.DATA_ANNOTATION:   0.85,
    EvidenceType.COLLECTION_NAV:    0.80,
    EvidenceType.FK_NAMING_CONV:    0.70,
    EvidenceType.REPO_INFERENCE:    0.65,
    EvidenceType.CTOR_INJECTION:    0.60,
    EvidenceType.NAMING_HEURISTIC:  0.40,
    EvidenceType.SEMANTIC_GUESS:    0.25,
}

_HIGH_THRESHOLD   = 0.80
_MEDIUM_THRESHOLD = 0.55


@dataclass
class EvidenceItem:
    evidence_type: EvidenceType
    source_file:   str
    line_number:   Optional[int]
    description:   str
    weight:        float

    def to_dict(self) -> dict:
        return {
            "type":        self.evidence_type.value,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "description": self.description,
            "weight":      round(self.weight, 3),
        }


@dataclass
class ScoredConfidence:
    level:    str           # HIGH | MEDIUM | LOW
    numeric:  float         # 0.0–1.0
    evidence: list[EvidenceItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level":    self.level,
            "numeric":  round(self.numeric, 3),
            "evidence": [e.to_dict() for e in self.evidence],
        }

    # Legacy compat
    @property
    def score(self) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(self.level, 1)


class ConfidenceEngine:
    """Single source of truth for confidence scoring across all analyzers."""

    def evidence(
        self,
        etype:       EvidenceType,
        source_file: str,
        line_number: Optional[int] = None,
        description: str = "",
    ) -> EvidenceItem:
        w = _WEIGHTS[etype]
        desc = description or etype.value.replace("_", " ").title()
        return EvidenceItem(etype, source_file, line_number, desc, w)

    def score(self, evidence_list: list[EvidenceItem]) -> ScoredConfidence:
        """
        Aggregate evidence into a single confidence score.

        Algorithm: take the MAX weight from the list (best-evidence wins).
        Multiple high-weight pieces cap at 1.0.
        """
        if not evidence_list:
            return ScoredConfidence("LOW", 0.0, [])

        numeric = max(e.weight for e in evidence_list)
        # Boost slightly if multiple independent HIGH signals agree
        if sum(1 for e in evidence_list if e.weight >= 0.80) >= 2:
            numeric = min(numeric + 0.05, 1.0)

        level = ("HIGH"   if numeric >= _HIGH_THRESHOLD   else
                 "MEDIUM" if numeric >= _MEDIUM_THRESHOLD else
                 "LOW")
        return ScoredConfidence(level, numeric, evidence_list)

    def from_string(self, level: str) -> ScoredConfidence:
        """Convert a legacy HIGH/MEDIUM/LOW string to a ScoredConfidence."""
        numeric = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.30}.get(level.upper(), 0.30)
        return ScoredConfidence(level.upper(), numeric)

    # -----------------------------------------------------------------------
    # Shortcut factories
    # -----------------------------------------------------------------------

    def ast_high(self, source_file: str, line: Optional[int] = None,
                 desc: str = "AST-extracted") -> ScoredConfidence:
        ev = self.evidence(EvidenceType.AST_DIRECT, source_file, line, desc)
        return self.score([ev])

    def ef_high(self, source_file: str, line: Optional[int] = None,
                desc: str = "EF Fluent API") -> ScoredConfidence:
        ev = self.evidence(EvidenceType.EF_FLUENT_API, source_file, line, desc)
        return self.score([ev])

    def inferred_medium(self, source_file: str,
                        desc: str = "Inferred from navigation") -> ScoredConfidence:
        ev = self.evidence(EvidenceType.FK_NAMING_CONV, source_file, None, desc)
        return self.score([ev])

    def heuristic_low(self, desc: str = "Naming heuristic") -> ScoredConfidence:
        ev = self.evidence(EvidenceType.NAMING_HEURISTIC, "", None, desc)
        return self.score([ev])


# Module-level singleton
_engine = ConfidenceEngine()


def get_engine() -> ConfidenceEngine:
    return _engine
