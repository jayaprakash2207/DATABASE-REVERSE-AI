"""
Python Adapter — Django ORM / SQLAlchemy / FastAPI / Flask

Extracts entities, repositories, and API endpoints from Python projects.

Supports:
  - Django ORM (models.Model, models.ForeignKey, etc.)
  - SQLAlchemy (declarative_base, Column, relationship)
  - FastAPI (@router.get, @app.post, etc.)
  - Flask (@app.route, Blueprint)
  - Django REST Framework (ViewSet, APIView)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from models.semantic_model import SemanticModel, TechContext
from models.universal import (
    UniversalEntity, UniversalField, UniversalRelationship,
    UniversalEndpoint, UniversalRepository,
    EntityKind, FieldKind, RelationshipKind, EndpointStyle,
    Technology, Language, ConfidenceLevel, normalize_type,
)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Django model class
_DJANGO_MODEL_RE = re.compile(
    r'^class\s+(\w+)\s*\(((?:[^)]*?)(?:models\.Model|Model)[^)]*)\)',
    re.MULTILINE,
)
# SQLAlchemy mapped class
_SA_CLASS_RE = re.compile(
    r'^class\s+(\w+)\s*\((?:Base|DeclarativeBase|db\.Model)[^)]*\)',
    re.MULTILINE,
)
# Python field (class attribute with type annotation or assignment)
_FIELD_ASSIGN_RE = re.compile(
    r'^\s{4}(\w+)\s*(?::\s*([\w\[\], |.]+))?\s*=\s*(.+)$',
    re.MULTILINE,
)
# Django FK
_DJANGO_FK_RE = re.compile(
    r'^\s{4}(\w+)\s*=\s*models\.(ForeignKey|OneToOneField|ManyToManyField)'
    r'\s*\(\s*["\']?(\w+)["\']?',
    re.MULTILINE,
)
# SQLAlchemy relationship
_SA_REL_RE = re.compile(
    r'^\s{4}(\w+)\s*=\s*relationship\s*\(\s*["\'](\w+)["\']',
    re.MULTILINE,
)
# SQLAlchemy Column
_SA_COL_RE = re.compile(
    r'^\s{4}(\w+)\s*(?::\s*[\w\[\]|. ]+)?\s*=\s*(?:mapped_column|Column)\s*\(([^)]*)\)',
    re.MULTILINE,
)
# FastAPI route
_FASTAPI_RE = re.compile(
    r'@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)
# Flask route
_FLASK_RE = re.compile(
    r'@(?:app|bp|blueprint)\s*\.route\s*\(\s*["\']([^"\']+)["\']'
    r'(?:.*?methods\s*=\s*\[([^\]]+)\])?',
    re.MULTILINE,
)
# Django ViewSet / APIView
_DRF_CLASS_RE = re.compile(
    r'^class\s+(\w+)\s*\((?:[^)]*?)(?:ViewSet|APIView|ModelViewSet|ReadOnlyModelViewSet)[^)]*\)',
    re.MULTILINE,
)
# Django URL pattern
_URL_RE = re.compile(
    r"(?:path|re_path)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_MODULE_RE = re.compile(r'^from\s+([\w.]+)\s+import|^import\s+([\w.]+)', re.MULTILINE)

_SKIP_DIRS = {
    "migrations", "__pycache__", ".git", "venv", ".venv", "env",
    "node_modules", "site-packages", "dist", "build",
}

_DJANGO_FIELD_TYPES = {
    "CharField": "string", "TextField": "string", "SlugField": "string",
    "URLField": "string", "EmailField": "string", "FileField": "string",
    "ImageField": "string", "UUIDField": "uuid",
    "IntegerField": "integer", "BigIntegerField": "integer",
    "SmallIntegerField": "integer", "PositiveIntegerField": "integer",
    "AutoField": "integer", "BigAutoField": "integer",
    "FloatField": "decimal", "DecimalField": "decimal",
    "BooleanField": "boolean", "NullBooleanField": "boolean",
    "DateField": "date", "DateTimeField": "datetime", "TimeField": "time",
    "DurationField": "duration", "BinaryField": "bytes",
    "JSONField": "json", "ArrayField": "array",
    "ForeignKey": "foreign_key", "OneToOneField": "one_to_one",
    "ManyToManyField": "many_to_many",
}


class DjangoAdapter(BaseAdapter):

    @property
    def name(self) -> str:
        return "Django ORM / SQLAlchemy / FastAPI"

    @property
    def supported_technologies(self) -> list[str]:
        return ["django_orm", "sqlalchemy"]

    def can_handle(self, tech_context: TechContext) -> bool:
        return (
            Language.PYTHON in tech_context.languages
            or Technology.DJANGO_ORM in tech_context.orms
            or Technology.SQLALCHEMY in tech_context.orms
        )

    def extract(self, tech_context: TechContext) -> SemanticModel:
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )

        root = Path(tech_context.project_root)
        py_files = [
            f for f in root.rglob("*.py")
            if not any(p in _SKIP_DIRS for p in f.parts)
        ]

        for py_file in py_files:
            text = self._read(str(py_file))
            if not text:
                continue

            # Django models
            for m in _DJANGO_MODEL_RE.finditer(text):
                entity = self._extract_django_entity(py_file, text, m)
                if entity:
                    model.entities.append(entity)
                    # FK relationships
                    rels = self._extract_django_rels(py_file, text, entity.name)
                    model.relationships.extend(rels)

            # SQLAlchemy models
            for m in _SA_CLASS_RE.finditer(text):
                entity = self._extract_sa_entity(py_file, text, m)
                if entity:
                    model.entities.append(entity)
                    rels = self._extract_sa_rels(py_file, text, entity.name)
                    model.relationships.extend(rels)

            # FastAPI endpoints
            for m in _FASTAPI_RE.finditer(text):
                ep = self._make_endpoint(
                    http_method  = m.group(1).upper(),
                    path         = m.group(2),
                    style        = EndpointStyle.FASTAPI_ROUTE,
                    source_file  = str(py_file),
                    line_number  = text[:m.start()].count("\n") + 1,
                )
                model.endpoints.append(ep)

            # Flask routes
            for m in _FLASK_RE.finditer(text):
                methods_raw = m.group(2) or "GET"
                methods = [x.strip().strip("'\"") for x in methods_raw.split(",")]
                for meth in methods:
                    ep = self._make_endpoint(
                        http_method  = meth.upper(),
                        path         = m.group(1),
                        style        = EndpointStyle.FLASK_ROUTE,
                        source_file  = str(py_file),
                        line_number  = text[:m.start()].count("\n") + 1,
                    )
                    model.endpoints.append(ep)

            # Django REST Framework ViewSets
            for m in _DRF_CLASS_RE.finditer(text):
                ep = self._make_endpoint(
                    http_method  = "GET",
                    path         = f"/{m.group(1).lower()}/",
                    style        = EndpointStyle.DJANGO_VIEWSET,
                    source_file  = str(py_file),
                    handler_class = m.group(1),
                    line_number  = text[:m.start()].count("\n") + 1,
                )
                model.endpoints.append(ep)

        return model

    # ------------------------------------------------------------------
    # Django entity
    # ------------------------------------------------------------------

    def _extract_django_entity(
        self, path: Path, text: str, class_match: re.Match
    ) -> Optional[UniversalEntity]:
        class_name = class_match.group(1)
        if class_name in ("Meta", "Admin", "Form", "Serializer"):
            return None

        # Extract class body (until next class or EOF)
        start = class_match.end()
        next_class = re.search(r'\nclass\s+\w+', text[start:])
        body = text[start: start + next_class.start()] if next_class else text[start:]

        fields  = self._extract_django_fields(body, str(path))
        agg     = self._aggregate_from_path(str(path), class_name)
        line_no = text[:class_match.start()].count("\n") + 1

        return UniversalEntity(
            name        = class_name,
            kind        = EntityKind.ENTITY,
            technology  = Technology.DJANGO_ORM,
            language    = Language.PYTHON,
            namespace   = self._module_from_path(str(path)),
            aggregate   = agg,
            fields      = fields,
            source_file = str(path),
            line_number = line_no,
            confidence  = ConfidenceLevel.HIGH,
            raw         = {"class_body_len": len(body)},
        )

    def _extract_django_fields(self, body: str, source_file: str) -> list[UniversalField]:
        fields: list[UniversalField] = []
        for m in _FIELD_ASSIGN_RE.finditer(body):
            fname    = m.group(1)
            rhs      = m.group(3).strip()
            if fname.startswith("_") or fname in ("Meta", "objects", "class"):
                continue

            # Determine field type from RHS
            field_type_match = re.match(r'models\.(\w+)', rhs)
            if not field_type_match:
                continue

            django_type = field_type_match.group(1)
            norm_type   = _DJANGO_FIELD_TYPES.get(django_type, "string")
            is_fk       = django_type in ("ForeignKey",)
            is_m2m      = django_type == "ManyToManyField"
            is_o2o      = django_type == "OneToOneField"
            is_pk       = fname == "id" or "primary_key=True" in rhs

            # Max length
            max_len_m = re.search(r'max_length\s*=\s*(\d+)', rhs)
            max_len = int(max_len_m.group(1)) if max_len_m else None

            nullable    = "null=True" in rhs or "blank=True" in rhs
            pii         = _detect_pii(fname, norm_type)

            if is_m2m:
                kind = FieldKind.COLLECTION
            elif is_fk or is_o2o:
                kind = FieldKind.FOREIGN_KEY
            else:
                kind = FieldKind.PRIMITIVE

            fields.append(UniversalField(
                name            = fname,
                kind            = kind,
                raw_type        = f"models.{django_type}",
                normalized_type = norm_type,
                is_pk           = is_pk,
                is_required     = not nullable,
                is_nullable     = nullable,
                max_length      = max_len,
                pii_risk        = pii,
                source_file     = source_file,
                confidence      = ConfidenceLevel.HIGH,
                raw             = {"rhs": rhs[:100]},
            ))

        return fields

    def _extract_django_rels(
        self, path: Path, text: str, source_entity: str
    ) -> list[UniversalRelationship]:
        rels: list[UniversalRelationship] = []
        for m in _DJANGO_FK_RE.finditer(text):
            fname     = m.group(1)
            rel_type  = m.group(2)
            target    = m.group(3)
            if target in ("self", "'self'", '"self"'):
                target = source_entity
            kind_map = {
                "ForeignKey":     RelationshipKind.MANY_TO_ONE,
                "OneToOneField":  RelationshipKind.ONE_TO_ONE,
                "ManyToManyField": RelationshipKind.MANY_TO_MANY,
            }
            rels.append(UniversalRelationship(
                source      = source_entity,
                target      = target,
                kind        = kind_map.get(rel_type, RelationshipKind.MANY_TO_ONE),
                via         = fname,
                technology  = Technology.DJANGO_ORM,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                evidence    = f"models.{rel_type}",
                raw         = {"match": m.group(0)},
            ))
        return rels

    # ------------------------------------------------------------------
    # SQLAlchemy entity
    # ------------------------------------------------------------------

    def _extract_sa_entity(
        self, path: Path, text: str, class_match: re.Match
    ) -> Optional[UniversalEntity]:
        class_name = class_match.group(1)
        start = class_match.end()
        next_class = re.search(r'\nclass\s+\w+', text[start:])
        body = text[start: start + next_class.start()] if next_class else text[start:]

        fields  = self._extract_sa_fields(body, str(path))
        agg     = self._aggregate_from_path(str(path), class_name)
        line_no = text[:class_match.start()].count("\n") + 1

        return UniversalEntity(
            name        = class_name,
            kind        = EntityKind.ENTITY,
            technology  = Technology.SQLALCHEMY,
            language    = Language.PYTHON,
            namespace   = self._module_from_path(str(path)),
            aggregate   = agg,
            fields      = fields,
            source_file = str(path),
            line_number = line_no,
            confidence  = ConfidenceLevel.HIGH,
            raw         = {"orm": "sqlalchemy"},
        )

    def _extract_sa_fields(self, body: str, source_file: str) -> list[UniversalField]:
        fields: list[UniversalField] = []
        for m in _SA_COL_RE.finditer(body):
            fname    = m.group(1)
            col_args = m.group(2)
            if fname.startswith("_"):
                continue

            # Determine type from Column args
            type_match = re.search(r'(String|Integer|Float|Boolean|DateTime|Date|UUID|Text|Numeric|LargeBinary)', col_args)
            raw_type   = type_match.group(1) if type_match else "String"
            norm_type  = normalize_type(raw_type)

            is_pk = "primary_key=True" in col_args
            nullable = "nullable=False" not in col_args

            max_len_m = re.search(r'String\s*\((\d+)\)', col_args)
            max_len   = int(max_len_m.group(1)) if max_len_m else None

            pii = _detect_pii(fname, norm_type)

            fields.append(UniversalField(
                name            = fname,
                kind            = FieldKind.PRIMITIVE,
                raw_type        = raw_type,
                normalized_type = norm_type,
                is_pk           = is_pk,
                is_required     = not nullable,
                is_nullable     = nullable,
                max_length      = max_len,
                pii_risk        = pii,
                source_file     = source_file,
                confidence      = ConfidenceLevel.HIGH,
                raw             = {"col_args": col_args[:100]},
            ))
        return fields

    def _extract_sa_rels(
        self, path: Path, text: str, source_entity: str
    ) -> list[UniversalRelationship]:
        rels: list[UniversalRelationship] = []
        for m in _SA_REL_RE.finditer(text):
            rels.append(UniversalRelationship(
                source      = source_entity,
                target      = m.group(2),
                kind        = RelationshipKind.ONE_TO_MANY,
                via         = m.group(1),
                technology  = Technology.SQLALCHEMY,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                evidence    = "relationship()",
            ))
        return rels

    # ------------------------------------------------------------------
    # Endpoint factory
    # ------------------------------------------------------------------

    def _make_endpoint(
        self, http_method: str, path: str, style: EndpointStyle,
        source_file: str, line_number: Optional[int] = None,
        handler_class: str = "",
    ) -> UniversalEndpoint:
        orm = Technology.DJANGO_ORM if style == EndpointStyle.DJANGO_VIEWSET else Technology.SQLALCHEMY
        return UniversalEndpoint(
            method         = http_method,
            path           = path,
            style          = style,
            technology     = orm,
            language       = Language.PYTHON,
            handler_class  = handler_class,
            source_file    = source_file,
            line_number    = line_number,
            confidence     = ConfidenceLevel.HIGH,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _module_from_path(self, path: str) -> str:
        """Convert file path to Python module notation."""
        p = Path(path)
        parts = list(p.parts)
        # Strip up to 'app', 'src', or project root
        for marker in ("app", "src", "apps"):
            if marker in parts:
                idx = parts.index(marker)
                parts = parts[idx:]
                break
        return ".".join(parts).replace(".py", "").replace("\\", ".").replace("/", ".")

    def _aggregate_from_path(self, source_file: str, entity: str) -> str:
        _GENERIC = {"models", "entities", "domain", "core", "app", "apps",
                    "src", "base", "abstract", "common"}
        parts = Path(source_file).parts
        for part in reversed(parts[:-1]):  # skip filename
            clean = part.lower().replace(".py", "")
            if clean not in _GENERIC and len(clean) > 2:
                return clean.capitalize() + "Aggregate"
        return entity + "Aggregate"


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

_PII_HIGH_RE   = re.compile(r'\b(password|ssn|credit_card|cvv|pin|secret)\b', re.I)
_PII_MEDIUM_RE = re.compile(r'\b(email|phone|address|birth|salary|national_id)\b', re.I)
_PII_LOW_RE    = re.compile(r'\b(name|first_name|last_name|username)\b', re.I)


def _detect_pii(field_name: str, field_type: str) -> Optional[str]:
    if _PII_HIGH_RE.search(field_name):
        return "high"
    if _PII_MEDIUM_RE.search(field_name):
        return "medium"
    if _PII_LOW_RE.search(field_name):
        return "low"
    return None
