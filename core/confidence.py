"""
Confidence scoring engine for all extraction findings.

Levels:
  HIGH   — Direct AST/DbContext/EF Core evidence (line-number cited)
  MEDIUM — Navigation property inference, naming-convention match
  LOW    — Heuristic assumption, no source-code anchor
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Confidence(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"

    @property
    def score(self) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}[self.value]

    @classmethod
    def aggregate(cls, levels: List["Confidence"]) -> "Confidence":
        """Return the highest confidence level in the list."""
        if not levels:
            return cls.LOW
        scores = {cls.HIGH: 3, cls.MEDIUM: 2, cls.LOW: 1}
        best = max(levels, key=lambda c: scores[c])
        return best

    @classmethod
    def from_source(cls, has_ast: bool, has_dbcontext: bool, has_fk: bool) -> "Confidence":
        if has_dbcontext or (has_ast and has_fk):
            return cls.HIGH
        if has_ast or has_fk:
            return cls.MEDIUM
        return cls.LOW


@dataclass
class Evidence:
    """A single piece of evidence supporting a finding."""
    description:  str
    source_file:  str
    line_number:  Optional[int] = None
    confidence:   Confidence = Confidence.MEDIUM

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "source_file":  self.source_file,
            "line_number":  self.line_number,
            "confidence":   self.confidence.value,
        }


@dataclass
class ConfidenceScore:
    """Aggregated confidence with supporting evidence list."""
    level:    Confidence
    evidence: List[Evidence] = field(default_factory=list)

    @classmethod
    def from_evidence(cls, evidence_list: List[Evidence]) -> "ConfidenceScore":
        if not evidence_list:
            return cls(Confidence.LOW)
        level = Confidence.aggregate([e.confidence for e in evidence_list])
        return cls(level, evidence_list)

    @classmethod
    def high(cls, description: str, source_file: str, line: Optional[int] = None) -> "ConfidenceScore":
        ev = Evidence(description, source_file, line, Confidence.HIGH)
        return cls(Confidence.HIGH, [ev])

    @classmethod
    def medium(cls, description: str, source_file: str, line: Optional[int] = None) -> "ConfidenceScore":
        ev = Evidence(description, source_file, line, Confidence.MEDIUM)
        return cls(Confidence.MEDIUM, [ev])

    @classmethod
    def low(cls, description: str, source_file: str = "") -> "ConfidenceScore":
        ev = Evidence(description, source_file, None, Confidence.LOW)
        return cls(Confidence.LOW, [ev])

    def to_dict(self) -> dict:
        return {
            "level":    self.level.value,
            "score":    self.level.score,
            "evidence": [e.to_dict() for e in self.evidence],
        }
