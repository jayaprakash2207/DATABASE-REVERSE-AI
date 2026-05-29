"""
EF Core / ASP.NET Core Adapter

Wraps the existing .NET-specific parsers and normalizes their output
into the Universal Semantic Model.

Supported:
  - EF Core (DbContext / Fluent API / Data Annotations)
  - ASP.NET Core (Controllers, Minimal APIs, Razor Pages)
  - MediatR (CQRS command/query handlers)
  - AutoMapper profiles
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from adapters.base import BaseAdapter
from models.semantic_model import SemanticModel, TechContext
from models.universal import (
    UniversalEntity, UniversalField, UniversalRelationship,
    UniversalEndpoint, UniversalRepository, UniversalHandler,
    EntityKind, FieldKind, RelationshipKind, EndpointStyle,
    Technology, Language, ConfidenceLevel, normalize_type,
)


_ROOT = Path(__file__).parent.parent.parent


def _ensure_path() -> None:
    root_str = str(_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


class EFCoreAdapter(BaseAdapter):
    """
    Technology adapter for .NET + EF Core projects.
    Delegates heavy lifting to the existing parser suite and normalizes
    all results to USM.
    """

    @property
    def name(self) -> str:
        return "EF Core / ASP.NET Core"

    @property
    def supported_technologies(self) -> list[str]:
        return ["ef_core"]

    def can_handle(self, tech_context: TechContext) -> bool:
        return (
            Language.CSHARP in tech_context.languages
            or Technology.EF_CORE in tech_context.orms
        )

    # ------------------------------------------------------------------
    # Main extraction
    # ------------------------------------------------------------------

    def extract(self, tech_context: TechContext) -> SemanticModel:
        _ensure_path()
        project_root = tech_context.project_root
        model = SemanticModel(
            project_root = project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )

        # -------- Detect project layout --------
        try:
            from project_layout import detect_layout
            layout = detect_layout(project_root)
            entity_dirs = layout.domain_dirs or [project_root]
            api_dirs    = (layout.api_dirs or []) + (layout.web_dirs or [])
        except Exception as e:
            model.extraction_warnings.append(f"Layout detection failed: {e}")
            entity_dirs = [project_root]
            api_dirs    = [project_root]

        # -------- Entity extraction --------
        try:
            from parsers.entity_extractor import EntityExtractor
            extractor = EntityExtractor()
            seen_entities: set[str] = set()
            for d in entity_dirs:
                raw_data = extractor.extract(str(d))
                for raw_e in raw_data.get("entities", []):
                    ue = self._map_entity(raw_e, is_vo=False)
                    if ue.name not in seen_entities:
                        model.entities.append(ue)
                        seen_entities.add(ue.name)
                for raw_vo in raw_data.get("value_objects", []):
                    ue = self._map_entity(raw_vo, is_vo=True)
                    if ue.name not in seen_entities:
                        model.entities.append(ue)
                        seen_entities.add(ue.name)
        except Exception as e:
            model.extraction_warnings.append(f"EntityExtractor failed: {e}")

        # -------- Relationship extraction --------
        try:
            from parsers.relationship_detector import RelationshipDetector
            rel_detector = RelationshipDetector()
            for d in entity_dirs:
                raw_rels = rel_detector.detect(str(d))
                for raw_r in raw_rels.get("relationships", []):
                    ur = self._map_relationship(raw_r)
                    model.relationships.append(ur)
        except Exception as e:
            model.extraction_warnings.append(f"RelationshipDetector failed: {e}")

        # -------- API / endpoint extraction --------
        try:
            from parsers.api_extractor import APIExtractor
            api_extractor = APIExtractor()
            for d in api_dirs if api_dirs else [project_root]:
                raw_api = api_extractor.extract(str(d))
                for raw_ep in raw_api.get("endpoints", []):
                    ue = self._map_endpoint(raw_ep)
                    model.endpoints.append(ue)
                for raw_hdl in raw_api.get("mediatr_handlers", []):
                    uh = self._map_handler(raw_hdl)
                    model.handlers.append(uh)
                for raw_repo in raw_api.get("repositories", []):
                    ur = self._map_repository(raw_repo)
                    model.repositories.append(ur)
        except Exception as e:
            model.extraction_warnings.append(f"APIExtractor failed: {e}")

        return model

    # ------------------------------------------------------------------
    # Entity mapper
    # ------------------------------------------------------------------

    def _map_entity(self, raw: dict, is_vo: bool = False) -> UniversalEntity:
        kind = EntityKind.VALUE_OBJECT if is_vo else (
            EntityKind.AGGREGATE_ROOT if raw.get("is_aggregate_root") else EntityKind.ENTITY
        )
        conf_raw = raw.get("confidence", {})
        if isinstance(conf_raw, dict):
            conf_str = conf_raw.get("level", "HIGH")
        else:
            conf_str = str(conf_raw)
        confidence = _parse_confidence(conf_str)

        fields = [self._map_field(f) for f in raw.get("fields", [])]

        return UniversalEntity(
            name       = raw.get("entity", raw.get("name", "Unknown")),
            kind       = kind,
            technology = Technology.EF_CORE,
            language   = Language.CSHARP,
            namespace  = raw.get("namespace", ""),
            aggregate  = raw.get("aggregate", ""),
            fields     = fields,
            source_file = raw.get("source_file", ""),
            line_number = raw.get("line_number"),
            confidence  = confidence,
            base_types  = raw.get("base_types", []),
            interfaces  = raw.get("interfaces", []),
            attributes  = raw.get("attributes", []),
            is_abstract = raw.get("is_abstract", False),
            raw        = raw,
        )

    def _map_field(self, raw: dict) -> UniversalField:
        raw_type = raw.get("type", raw.get("raw_type", "object"))
        norm     = normalize_type(raw_type)

        is_fk  = raw.get("is_fk", False)
        is_nav = raw.get("is_navigation", False)
        is_col = raw.get("is_collection", False)

        if is_fk:
            kind = FieldKind.FOREIGN_KEY
        elif is_col:
            kind = FieldKind.COLLECTION
        elif is_nav:
            kind = FieldKind.NAVIGATION
        else:
            kind = FieldKind.PRIMITIVE

        return UniversalField(
            name            = raw.get("name", "unknown"),
            kind            = kind,
            raw_type        = raw_type,
            normalized_type = norm,
            is_pk           = raw.get("is_pk", False),
            is_required     = not raw.get("is_nullable", True),
            is_unique       = raw.get("is_unique", False),
            is_indexed      = raw.get("is_indexed", False),
            is_nullable     = raw.get("is_nullable", True),
            max_length      = raw.get("max_length"),
            default_val     = raw.get("default_val"),
            pii_risk        = raw.get("pii_risk"),
            visibility      = raw.get("visibility", "public"),
            get_access      = raw.get("get_visibility", "public"),
            set_access      = raw.get("set_visibility", "public"),
            source_file     = raw.get("source_file", ""),
            line_number     = raw.get("line_number"),
            confidence      = ConfidenceLevel.HIGH,
            raw             = raw,
        )

    # ------------------------------------------------------------------
    # Relationship mapper
    # ------------------------------------------------------------------

    def _map_relationship(self, raw: dict) -> UniversalRelationship:
        rel_str = raw.get("relationship", raw.get("kind", "one_to_many"))
        kind    = _parse_relationship_kind(rel_str)
        return UniversalRelationship(
            source        = raw.get("source", ""),
            target        = raw.get("target", ""),
            kind          = kind,
            via           = raw.get("via", ""),
            technology    = Technology.EF_CORE,
            is_cross_domain = raw.get("cross_domain", False),
            cascade_delete  = raw.get("cascade_delete", False),
            is_required   = raw.get("is_required", False),
            source_file   = raw.get("source_file", ""),
            line_number   = raw.get("line_number"),
            confidence    = _parse_confidence(raw.get("confidence", "HIGH")),
            evidence      = raw.get("evidence", ""),
            raw           = raw,
        )

    # ------------------------------------------------------------------
    # Endpoint mapper
    # ------------------------------------------------------------------

    def _map_endpoint(self, raw: dict) -> UniversalEndpoint:
        style_str = raw.get("style", "rest_controller")
        style     = _parse_endpoint_style(style_str)

        return UniversalEndpoint(
            method          = raw.get("method", "GET"),
            path            = raw.get("path", "/"),
            style           = style,
            technology      = Technology.EF_CORE,
            language        = Language.CSHARP,
            handler_class   = raw.get("handler_class", raw.get("class", "")),
            handler_method  = raw.get("handler_method", raw.get("action", "")),
            auth_required   = raw.get("auth_required", raw.get("requires_auth", False)),
            request_model   = raw.get("request_model"),
            response_model  = raw.get("response_model"),
            entities_touched = raw.get("entities_touched", []),
            repositories    = raw.get("repositories", []),
            services        = raw.get("services", []),
            source_file     = raw.get("source_file", ""),
            line_number     = raw.get("line_number"),
            confidence      = _parse_confidence(raw.get("confidence", "HIGH")),
            raw             = raw,
        )

    # ------------------------------------------------------------------
    # Repository mapper
    # ------------------------------------------------------------------

    def _map_repository(self, raw: dict) -> UniversalRepository:
        return UniversalRepository(
            name        = raw.get("name", raw.get("class_name", "Unknown")),
            entity      = raw.get("entity", raw.get("entity_type", "")),
            technology  = Technology.EF_CORE,
            language    = Language.CSHARP,
            operations  = raw.get("operations", raw.get("methods", [])),
            source_file = raw.get("source_file", ""),
            line_number = raw.get("line_number"),
            confidence  = _parse_confidence(raw.get("confidence", "HIGH")),
        )

    # ------------------------------------------------------------------
    # Handler mapper (MediatR)
    # ------------------------------------------------------------------

    def _map_handler(self, raw: dict) -> UniversalHandler:
        req_type = raw.get("request_type", raw.get("command_type", raw.get("query_type", "")))
        pattern  = "cqrs_command"
        if req_type and ("Query" in req_type):
            pattern = "cqrs_query"
        elif req_type and ("Event" in req_type):
            pattern = "event_handler"

        return UniversalHandler(
            name             = raw.get("class_name", raw.get("name", "Unknown")),
            pattern          = pattern,
            request_type     = req_type,
            response_type    = raw.get("response_type", raw.get("return_type")),
            repositories     = raw.get("repositories", []),
            entities_touched = raw.get("entities_touched", []),
            source_file      = raw.get("source_file", ""),
            line_number      = raw.get("line_number"),
            confidence       = _parse_confidence(raw.get("confidence", "HIGH")),
        )


# ---------------------------------------------------------------------------
# Helper parsers
# ---------------------------------------------------------------------------

def _parse_confidence(val: Any) -> ConfidenceLevel:
    s = str(val).upper()
    if s in ("HIGH", "1.0", "0.9", "0.85"):
        return ConfidenceLevel.HIGH
    if s in ("MEDIUM", "0.7", "0.65", "0.6"):
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _parse_relationship_kind(val: str) -> RelationshipKind:
    v = val.lower()
    if "many_to_many" in v or "m2m" in v:
        return RelationshipKind.MANY_TO_MANY
    if "one_to_many" in v or "1_to_n" in v:
        return RelationshipKind.ONE_TO_MANY
    if "many_to_one" in v or "n_to_1" in v:
        return RelationshipKind.MANY_TO_ONE
    if "one_to_one" in v or "1_to_1" in v:
        return RelationshipKind.ONE_TO_ONE
    if "embeds" in v or "owned" in v:
        return RelationshipKind.EMBEDS
    if "references" in v or "cross" in v:
        return RelationshipKind.REFERENCES
    return RelationshipKind.ONE_TO_MANY


def _parse_endpoint_style(val: str) -> EndpointStyle:
    v = val.lower()
    if "minimal" in v:
        return EndpointStyle.MINIMAL_API
    if "razor" in v:
        return EndpointStyle.RAZOR_PAGE
    if "graphql" in v:
        return EndpointStyle.GRAPHQL
    if "grpc" in v:
        return EndpointStyle.GRPC
    return EndpointStyle.REST_CONTROLLER
