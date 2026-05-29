"""
Universal Semantic Model — Container

Aggregates all extracted USM objects for a single analysis run.
Provides serialization, merging, and summary utilities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .universal import (
    UniversalEntity, UniversalRelationship, UniversalEndpoint,
    UniversalRepository, UniversalHandler, UniversalGovernanceFinding,
    Technology, Language,
)


@dataclass
class TechContext:
    """Detected technology context for a project."""
    project_root:  str
    languages:     list[Language]          = field(default_factory=list)
    frameworks:    list[str]               = field(default_factory=list)   # ["spring_boot","hibernate"]
    orms:          list[Technology]        = field(default_factory=list)
    api_styles:    list[str]               = field(default_factory=list)   # ["rest","graphql","grpc"]
    architecture:  str                     = "unknown"                     # "monolith"|"modular_monolith"|"microservices"|"layered"
    db_types:      list[str]               = field(default_factory=list)   # ["postgresql","mongodb","sqlite"]
    package_files: list[str]               = field(default_factory=list)   # files that drove detection
    confidence:    float                   = 0.0

    def primary_language(self) -> Optional[Language]:
        return self.languages[0] if self.languages else None

    def primary_orm(self) -> Optional[Technology]:
        return self.orms[0] if self.orms else None

    def to_dict(self) -> dict:
        return {
            "project_root": self.project_root,
            "languages":    [l.value for l in self.languages],
            "frameworks":   self.frameworks,
            "orms":         [o.value for o in self.orms],
            "api_styles":   self.api_styles,
            "architecture": self.architecture,
            "db_types":     self.db_types,
            "confidence":   self.confidence,
        }


@dataclass
class SemanticModel:
    """
    Container for all Universal Semantic Model objects extracted from a project.
    """
    project_root:   str
    tech_context:   Optional[TechContext]             = None
    entities:       list[UniversalEntity]             = field(default_factory=list)
    relationships:  list[UniversalRelationship]       = field(default_factory=list)
    endpoints:      list[UniversalEndpoint]           = field(default_factory=list)
    repositories:   list[UniversalRepository]         = field(default_factory=list)
    handlers:       list[UniversalHandler]            = field(default_factory=list)
    findings:       list[UniversalGovernanceFinding]  = field(default_factory=list)
    generated_at:   str                               = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    adapter_used:   str                               = "unknown"
    extraction_warnings: list[str]                    = field(default_factory=list)

    # ------------------------------------------------------------------
    # Merge from another SemanticModel (multi-adapter runs)
    # ------------------------------------------------------------------

    def merge(self, other: "SemanticModel") -> None:
        existing_entities = {e.name for e in self.entities}
        for e in other.entities:
            if e.name not in existing_entities:
                self.entities.append(e)

        existing_rels = {(r.source, r.target, r.via) for r in self.relationships}
        for r in other.relationships:
            if (r.source, r.target, r.via) not in existing_rels:
                self.relationships.append(r)

        existing_eps = {(ep.method, ep.path) for ep in self.endpoints}
        for ep in other.endpoints:
            if (ep.method, ep.path) not in existing_eps:
                self.endpoints.append(ep)

        existing_repos = {r.name for r in self.repositories}
        for r in other.repositories:
            if r.name not in existing_repos:
                self.repositories.append(r)

        existing_hdl = {h.name for h in self.handlers}
        for h in other.handlers:
            if h.name not in existing_hdl:
                self.handlers.append(h)

        self.findings.extend(other.findings)
        self.extraction_warnings.extend(other.extraction_warnings)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def entity_by_name(self, name: str) -> Optional[UniversalEntity]:
        return next((e for e in self.entities if e.name == name), None)

    def entities_by_aggregate(self, aggregate: str) -> list[UniversalEntity]:
        return [e for e in self.entities if e.aggregate == aggregate]

    def relationships_from(self, entity_name: str) -> list[UniversalRelationship]:
        return [r for r in self.relationships if r.source == entity_name]

    def relationships_to(self, entity_name: str) -> list[UniversalRelationship]:
        return [r for r in self.relationships if r.target == entity_name]

    def critical_findings(self) -> list[UniversalGovernanceFinding]:
        return [f for f in self.findings if f.severity == "CRITICAL"]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        aggregates = {e.aggregate for e in self.entities if e.aggregate}
        languages  = {e.language.value for e in self.entities}
        techs      = {e.technology.value for e in self.entities}
        return {
            "project_root":       self.project_root,
            "generated_at":       self.generated_at,
            "adapter_used":       self.adapter_used,
            "entities":           len(self.entities),
            "relationships":      len(self.relationships),
            "endpoints":          len(self.endpoints),
            "repositories":       len(self.repositories),
            "handlers":           len(self.handlers),
            "governance_findings": len(self.findings),
            "critical_findings":  len(self.critical_findings()),
            "aggregates":         sorted(aggregates),
            "languages_detected": sorted(languages),
            "technologies":       sorted(techs),
            "tech_context":       self.tech_context.to_dict() if self.tech_context else {},
            "warnings":           len(self.extraction_warnings),
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "project_root":  self.project_root,
            "generated_at":  self.generated_at,
            "adapter_used":  self.adapter_used,
            "tech_context":  self.tech_context.to_dict() if self.tech_context else {},
            "entities":      [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "endpoints":     [ep.to_dict() for ep in self.endpoints],
            "repositories":  [r.to_dict() for r in self.repositories],
            "handlers":      [h.to_dict() for h in self.handlers],
            "findings":      [f.to_dict() for f in self.findings],
            "warnings":      self.extraction_warnings,
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "SemanticModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            project_root=data.get("project_root", ""),
            generated_at=data.get("generated_at", ""),
            adapter_used=data.get("adapter_used", "unknown"),
            extraction_warnings=data.get("warnings", []),
        )

    # ------------------------------------------------------------------
    # Legacy bridge: export as flat dicts matching existing pipeline format
    # ------------------------------------------------------------------

    def to_legacy_entities_dict(self) -> dict:
        """Convert to the format expected by existing scripts/knowledge_graph.py etc."""
        entities_out  = []
        value_objects = []

        for e in self.entities:
            entry = {
                "entity":           e.name,
                "kind":             e.kind.value,
                "namespace":        e.namespace,
                "aggregate":        e.aggregate,
                "technology":       e.technology.value,
                "language":         e.language.value,
                "is_aggregate_root": e.kind.value == "aggregate_root",
                "is_value_object":  e.kind.value == "value_object",
                "source_file":      e.source_file,
                "line_number":      e.line_number,
                "confidence":       {"level": e.confidence.value, "score": 1.0},
                "fields": [
                    {
                        "name":       f.name,
                        "type":       f.raw_type,
                        "is_pk":      f.is_pk,
                        "is_fk":      f.kind.value == "foreign_key",
                        "is_navigation": f.kind.value in ("navigation","collection"),
                        "set_visibility": f.set_access,
                        "get_visibility": f.get_access,
                        "pii_risk":   f.pii_risk,
                    }
                    for f in e.fields
                ],
            }
            if e.kind.value == "value_object":
                value_objects.append(entry)
            else:
                entities_out.append(entry)

        return {"entities": entities_out, "value_objects": value_objects}

    def to_legacy_relationships_dict(self) -> dict:
        rels = [
            {
                "source":       r.source,
                "target":       r.target,
                "relationship": r.kind.value,
                "via":          r.via,
                "cross_domain": r.is_cross_domain,
                "cascade_delete": r.cascade_delete,
                "confidence":   r.confidence.value,
                "source_file":  r.source_file,
                "line_number":  r.line_number,
                "evidence":     r.evidence,
            }
            for r in self.relationships
        ]
        return {"relationships": rels}

    def to_legacy_apis_dict(self) -> dict:
        endpoints = [ep.to_dict() for ep in self.endpoints]
        handlers  = [h.to_dict()  for h  in self.handlers]
        repos     = [r.to_dict()  for r  in self.repositories]
        return {
            "endpoints":        endpoints,
            "mediatr_handlers": handlers,
            "repositories":     repos,
            "mapper_profiles":  [],
        }
