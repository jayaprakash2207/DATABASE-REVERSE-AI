"""
Universal Semantic Model (USM)

Framework-agnostic enterprise architecture representation.
All technology adapters normalize their output into these data classes.
All analysis engines and the AI reasoning layer consume ONLY these types.

Technology-specific details are preserved in the `raw` field of each object
for traceability, but analysis never depends on them.

Supported technologies (via adapters):
  .NET    : EF Core, ASP.NET Core, MediatR, Minimal API, Blazor
  Java    : Spring Boot, Spring Data JPA, Hibernate, JAX-RS
  Python  : Django ORM, SQLAlchemy, FastAPI, Flask
  Node.js : Express, Sequelize, Mongoose, TypeORM, Prisma

Universal concept mapping:
  Entity       = JPA @Entity / EF Entity / Django Model / Mongoose Schema
  Endpoint     = @RestController / ApiController / Flask route / Express router
  Repository   = JpaRepository / IRepository<T> / Manager / DAO
  Field        = Column / Property / Field / SchemaType
  Relationship = @OneToMany / HasMany / ForeignKey / populate()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EntityKind(str, Enum):
    ENTITY         = "entity"
    VALUE_OBJECT   = "value_object"
    AGGREGATE_ROOT = "aggregate_root"
    DTO            = "dto"
    ENUM           = "enum"
    PROJECTION     = "projection"   # read-model / view-model


class FieldKind(str, Enum):
    PRIMITIVE       = "primitive"     # string, int, decimal, bool, date
    FOREIGN_KEY     = "foreign_key"   # FK column
    NAVIGATION      = "navigation"    # nav property / association
    COLLECTION      = "collection"    # one-to-many nav / array
    EMBEDDED        = "embedded"      # owned / embedded value object
    COMPUTED        = "computed"      # expression-bodied / @property
    DISCRIMINATOR   = "discriminator" # TPH/STI type column


class RelationshipKind(str, Enum):
    ONE_TO_ONE      = "one_to_one"
    ONE_TO_MANY     = "one_to_many"
    MANY_TO_ONE     = "many_to_one"
    MANY_TO_MANY    = "many_to_many"
    EMBEDS          = "embeds"       # owns / embedded document
    REFERENCES      = "references"   # ID reference across bounded contexts


class EndpointStyle(str, Enum):
    REST_CONTROLLER = "rest_controller"
    MINIMAL_API     = "minimal_api"
    RAZOR_PAGE      = "razor_page"
    GRAPHQL         = "graphql"
    GRPC            = "grpc"
    SPRING_MVC      = "spring_mvc"
    DJANGO_VIEW     = "django_view"
    DJANGO_VIEWSET  = "django_viewset"
    EXPRESS_ROUTE   = "express_route"
    FASTAPI_ROUTE   = "fastapi_route"
    FLASK_ROUTE     = "flask_route"


class Technology(str, Enum):
    EF_CORE        = "ef_core"
    SPRING_JPA     = "spring_jpa"
    HIBERNATE      = "hibernate"
    DJANGO_ORM     = "django_orm"
    SQLALCHEMY     = "sqlalchemy"
    SEQUELIZE      = "sequelize"
    MONGOOSE       = "mongoose"
    TYPEORM        = "typeorm"
    PRISMA         = "prisma"
    UNKNOWN        = "unknown"


class Language(str, Enum):
    CSHARP     = "csharp"
    JAVA       = "java"
    PYTHON     = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    KOTLIN     = "kotlin"
    UNKNOWN    = "unknown"


class ConfidenceLevel(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


# ---------------------------------------------------------------------------
# Universal Field
# ---------------------------------------------------------------------------

@dataclass
class UniversalField:
    name:        str
    kind:        FieldKind
    raw_type:    str                    # Original source type (string, String, str, etc.)
    normalized_type: str                # Normalized: string | integer | decimal | boolean
                                        #             datetime | uuid | json | bytes | reference
    is_pk:       bool        = False
    is_required: bool        = False
    is_unique:   bool        = False
    is_indexed:  bool        = False
    is_nullable: bool        = True
    max_length:  Optional[int] = None
    default_val: Optional[str] = None
    pii_risk:    Optional[str] = None   # "high" | "medium" | "low" | None
    visibility:  str         = "public"
    get_access:  str         = "public"
    set_access:  str         = "public"
    source_file: str         = ""
    line_number: Optional[int] = None
    confidence:  ConfidenceLevel = ConfidenceLevel.HIGH
    raw:         dict        = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name":             self.name,
            "kind":             self.kind.value,
            "raw_type":         self.raw_type,
            "normalized_type":  self.normalized_type,
            "is_pk":            self.is_pk,
            "is_required":      self.is_required,
            "is_unique":        self.is_unique,
            "is_nullable":      self.is_nullable,
            "max_length":       self.max_length,
            "default_val":      self.default_val,
            "pii_risk":         self.pii_risk,
            "visibility":       self.visibility,
            "source_file":      self.source_file,
            "line_number":      self.line_number,
            "confidence":       self.confidence.value,
        }


# ---------------------------------------------------------------------------
# Universal Entity
# ---------------------------------------------------------------------------

@dataclass
class UniversalEntity:
    name:         str
    kind:         EntityKind
    technology:   Technology
    language:     Language
    namespace:    str                   # Package / namespace / module path
    aggregate:    str                   # Bounded context / aggregate grouping
    fields:       list[UniversalField]  = field(default_factory=list)
    source_file:  str                   = ""
    line_number:  Optional[int]         = None
    confidence:   ConfidenceLevel       = ConfidenceLevel.HIGH
    base_types:   list[str]             = field(default_factory=list)
    interfaces:   list[str]             = field(default_factory=list)
    attributes:   list[str]             = field(default_factory=list)  # annotations
    is_abstract:  bool                  = False
    raw:          dict                  = field(default_factory=dict)

    @property
    def pk_fields(self) -> list[UniversalField]:
        return [f for f in self.fields if f.is_pk]

    @property
    def fk_fields(self) -> list[UniversalField]:
        return [f for f in self.fields if f.kind == FieldKind.FOREIGN_KEY]

    @property
    def nav_fields(self) -> list[UniversalField]:
        return [f for f in self.fields
                if f.kind in (FieldKind.NAVIGATION, FieldKind.COLLECTION)]

    @property
    def pii_fields(self) -> list[UniversalField]:
        return [f for f in self.fields if f.pii_risk]

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "kind":        self.kind.value,
            "technology":  self.technology.value,
            "language":    self.language.value,
            "namespace":   self.namespace,
            "aggregate":   self.aggregate,
            "field_count": len(self.fields),
            "pk_count":    len(self.pk_fields),
            "fk_count":    len(self.fk_fields),
            "pii_count":   len(self.pii_fields),
            "fields":      [f.to_dict() for f in self.fields],
            "source_file": self.source_file,
            "line_number": self.line_number,
            "confidence":  self.confidence.value,
            "base_types":  self.base_types,
            "interfaces":  self.interfaces,
            "attributes":  self.attributes,
        }


# ---------------------------------------------------------------------------
# Universal Relationship
# ---------------------------------------------------------------------------

@dataclass
class UniversalRelationship:
    source:       str                   # Entity name
    target:       str                   # Entity name
    kind:         RelationshipKind
    via:          str                   # FK field / join column / property name
    technology:   Technology
    is_cross_domain: bool               = False
    cascade_delete:  bool               = False
    is_required:  bool                  = False
    source_file:  str                   = ""
    line_number:  Optional[int]         = None
    confidence:   ConfidenceLevel       = ConfidenceLevel.HIGH
    evidence:     str                   = ""
    raw:          dict                  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source":         self.source,
            "target":         self.target,
            "kind":           self.kind.value,
            "via":            self.via,
            "technology":     self.technology.value,
            "is_cross_domain": self.is_cross_domain,
            "cascade_delete": self.cascade_delete,
            "source_file":    self.source_file,
            "line_number":    self.line_number,
            "confidence":     self.confidence.value,
            "evidence":       self.evidence,
        }


# ---------------------------------------------------------------------------
# Universal Endpoint
# ---------------------------------------------------------------------------

@dataclass
class UniversalEndpoint:
    method:          str                # GET | POST | PUT | DELETE | PATCH
    path:            str                # /api/catalog/{id}
    style:           EndpointStyle
    technology:      Technology
    language:        Language
    handler_class:   str                = ""
    handler_method:  str                = ""
    auth_required:   bool               = False
    request_model:   Optional[str]      = None
    response_model:  Optional[str]      = None
    entities_touched: list[str]         = field(default_factory=list)
    repositories:    list[str]          = field(default_factory=list)
    services:        list[str]          = field(default_factory=list)
    source_file:     str                = ""
    line_number:     Optional[int]      = None
    confidence:      ConfidenceLevel    = ConfidenceLevel.HIGH
    raw:             dict               = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "method":          self.method,
            "path":            self.path,
            "style":           self.style.value,
            "technology":      self.technology.value,
            "language":        self.language.value,
            "handler_class":   self.handler_class,
            "handler_method":  self.handler_method,
            "auth_required":   self.auth_required,
            "request_model":   self.request_model,
            "response_model":  self.response_model,
            "entities_touched": self.entities_touched,
            "repositories":    self.repositories,
            "source_file":     self.source_file,
            "line_number":     self.line_number,
            "confidence":      self.confidence.value,
        }


# ---------------------------------------------------------------------------
# Universal Repository
# ---------------------------------------------------------------------------

@dataclass
class UniversalRepository:
    name:        str
    entity:      str                    # Entity this repository manages
    technology:  Technology
    language:    Language
    operations:  list[str]              = field(default_factory=list)  # find, save, delete, etc.
    source_file: str                    = ""
    line_number: Optional[int]          = None
    confidence:  ConfidenceLevel        = ConfidenceLevel.HIGH

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "entity":      self.entity,
            "technology":  self.technology.value,
            "language":    self.language.value,
            "operations":  self.operations,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "confidence":  self.confidence.value,
        }


# ---------------------------------------------------------------------------
# Universal Handler (CQRS / service method / use case)
# ---------------------------------------------------------------------------

@dataclass
class UniversalHandler:
    name:         str
    pattern:      str                   # "cqrs_command" | "cqrs_query" | "service_method"
                                        # | "use_case" | "application_service"
    request_type: Optional[str]         = None
    response_type: Optional[str]        = None
    repositories: list[str]             = field(default_factory=list)
    entities_touched: list[str]         = field(default_factory=list)
    source_file:  str                   = ""
    line_number:  Optional[int]         = None
    confidence:   ConfidenceLevel       = ConfidenceLevel.HIGH

    def to_dict(self) -> dict:
        return {
            "name":           self.name,
            "pattern":        self.pattern,
            "request_type":   self.request_type,
            "response_type":  self.response_type,
            "repositories":   self.repositories,
            "entities_touched": self.entities_touched,
            "source_file":    self.source_file,
            "line_number":    self.line_number,
            "confidence":     self.confidence.value,
        }


# ---------------------------------------------------------------------------
# Universal Governance Finding
# ---------------------------------------------------------------------------

@dataclass
class UniversalGovernanceFinding:
    rule_type:    str                   # "pii" | "pci" | "audit_trail" | "auth_missing"
                                        # | "encryption_gap" | "no_validation" | "sql_injection_risk"
    severity:     str                   # "CRITICAL" | "WARNING" | "NOTE"
    finding_type: str                   # "CONFIRMED" | "INFERRED" | "RECOMMENDED"
    entity:       str
    field:        str                   = ""
    description:  str                   = ""
    recommendation: str                 = ""
    source_file:  str                   = ""
    line_number:  Optional[int]         = None
    confidence:   ConfidenceLevel       = ConfidenceLevel.HIGH
    analyzer:     str                   = "governance_engine"

    def to_dict(self) -> dict:
        return {
            "rule_type":      self.rule_type,
            "severity":       self.severity,
            "finding_type":   self.finding_type,
            "entity":         self.entity,
            "field":          self.field,
            "description":    self.description,
            "recommendation": self.recommendation,
            "source_file":    self.source_file,
            "line_number":    self.line_number,
            "confidence":     self.confidence.value,
            "analyzer":       self.analyzer,
        }


# ---------------------------------------------------------------------------
# Type normalization helper
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, str] = {
    # C# primitives
    "string": "string", "String": "string",
    "int": "integer", "long": "integer", "short": "integer",
    "uint": "integer", "ulong": "integer",
    "decimal": "decimal", "double": "decimal", "float": "decimal",
    "bool": "boolean", "Boolean": "boolean",
    "DateTime": "datetime", "DateTimeOffset": "datetime",
    "DateOnly": "date", "TimeOnly": "time", "TimeSpan": "duration",
    "Guid": "uuid", "byte[]": "bytes",
    "object": "any",
    # Java
    "Integer": "integer", "Long": "integer", "Short": "integer",
    "Double": "decimal", "Float": "decimal", "BigDecimal": "decimal",
    "Boolean": "boolean", "Date": "datetime", "LocalDate": "date",
    "LocalDateTime": "datetime", "UUID": "uuid",
    # Python
    "str": "string", "int": "integer", "float": "decimal",
    "bool": "boolean", "datetime": "datetime", "date": "date",
    "UUID": "uuid", "dict": "json", "list": "array",
    # JavaScript/TypeScript
    "number": "decimal", "bigint": "integer",
    "Date": "datetime", "Buffer": "bytes", "any": "any",
    "Record": "json", "object": "json",
}


def normalize_type(raw_type: str) -> str:
    base = raw_type.rstrip("?").split("<")[0].split("[")[0].strip()
    return _TYPE_MAP.get(base, base.lower() if base else "unknown")
