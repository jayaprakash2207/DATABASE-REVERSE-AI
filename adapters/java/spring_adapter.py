"""
Java Spring Boot / JPA / Hibernate Adapter

Extracts entities, repositories, and REST endpoints from Java Spring projects.
Supports:
  - Spring Data JPA (@Entity, @Repository, JpaRepository)
  - Hibernate (@Table, @Column, @OneToMany, @ManyToOne, etc.)
  - Spring MVC (@RestController, @GetMapping, @PostMapping, etc.)
  - Spring Boot service layer (@Service)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from adapters.base import BaseAdapter
from models.semantic_model import SemanticModel, TechContext
from models.universal import (
    UniversalEntity, UniversalField, UniversalRelationship,
    UniversalEndpoint, UniversalRepository, UniversalHandler,
    EntityKind, FieldKind, RelationshipKind, EndpointStyle,
    Technology, Language, ConfidenceLevel, normalize_type,
)

# ---------------------------------------------------------------------------
# Regex patterns for Java parsing
# ---------------------------------------------------------------------------

_CLASS_RE   = re.compile(
    r'(?:@(?:Entity|Table|MappedSuperclass|Embeddable|Data|Getter|Setter)\s*(?:\([^)]*\)\s*)*)'
    r'(?:public\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)'
    r'(?:\s+extends\s+([\w<>, ]+))?(?:\s+implements\s+([\w<>, ]+))?',
    re.MULTILINE,
)
_FIELD_RE   = re.compile(
    r'(?:@(?:Id|Column|JoinColumn|OneToMany|ManyToOne|ManyToMany|OneToOne|Enumerated|Embedded|Transient)'
    r'(?:\([^)]*\))?\s*)*'
    r'(?:private|protected|public)\s+([\w<>\[\], .]+)\s+(\w+)\s*[;=]',
    re.MULTILINE,
)
_ID_ANN     = re.compile(r'@Id\b')
_ENTITY_ANN = re.compile(r'@Entity\b')
_REPO_RE    = re.compile(
    r'(?:public\s+)?interface\s+(\w+)\s+extends\s+(JpaRepository|CrudRepository|PagingAndSortingRepository|Repository)<(\w+)',
    re.MULTILINE,
)
_SERVICE_RE = re.compile(
    r'@Service\b.*?(?:public\s+)?class\s+(\w+)',
    re.DOTALL,
)
_CONTROLLER_RE = re.compile(
    r'@(?:RestController|Controller)\s*(?:\([^)]*\))?\s*(?:@RequestMapping\s*\(\s*["\']([^"\']*)["\'])?.*?'
    r'(?:public\s+)?class\s+(\w+)',
    re.DOTALL,
)
_MAPPING_RE = re.compile(
    r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
    r'\s*\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\']',
    re.MULTILINE,
)
_PACKAGE_RE = re.compile(r'^package\s+([\w.]+)\s*;', re.MULTILINE)
_IMPORT_RE  = re.compile(r'^import\s+([\w.]+)\s*;', re.MULTILINE)

# JPA relationship annotations
_REL_ANN = re.compile(
    r'@(OneToMany|ManyToOne|ManyToMany|OneToOne|Embedded|ElementCollection)'
    r'(?:\([^)]*\))?\s*(?:@JoinColumn\s*\(\s*name\s*=\s*["\']([^"\']*)["\'])?',
    re.MULTILINE,
)

_SKIP_DIRS = {"target", "build", ".git", "node_modules", ".gradle", "out"}

_JAVA_TYPE_MAP = {
    "String": "string",
    "Integer": "integer", "int": "integer", "Long": "integer", "long": "integer",
    "Double": "decimal", "double": "decimal", "Float": "decimal", "float": "decimal",
    "BigDecimal": "decimal",
    "Boolean": "boolean", "boolean": "boolean",
    "LocalDate": "date", "LocalDateTime": "datetime",
    "Date": "datetime", "Timestamp": "datetime",
    "UUID": "uuid",
    "byte[]": "bytes",
    "Object": "any",
}


class SpringAdapter(BaseAdapter):

    @property
    def name(self) -> str:
        return "Spring Boot / JPA"

    @property
    def supported_technologies(self) -> list[str]:
        return ["spring_jpa", "hibernate"]

    def can_handle(self, tech_context: TechContext) -> bool:
        return (
            Language.JAVA in tech_context.languages
            or Language.KOTLIN in tech_context.languages
            or Technology.SPRING_JPA in tech_context.orms
            or Technology.HIBERNATE in tech_context.orms
        )

    def extract(self, tech_context: TechContext) -> SemanticModel:
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )

        root = Path(tech_context.project_root)
        java_files = [
            f for f in root.rglob("*.java")
            if not any(p in _SKIP_DIRS for p in f.parts)
        ]

        for java_file in java_files:
            text = self._read(str(java_file))
            if not text:
                continue
            pkg = self._extract_package(text)

            # Entities
            if _ENTITY_ANN.search(text):
                entity = self._extract_entity(java_file, text, pkg)
                if entity:
                    model.entities.append(entity)
                    # Extract relationships from the same file
                    rels = self._extract_relationships(java_file, text, entity.name)
                    model.relationships.extend(rels)

            # Repositories
            for m in _REPO_RE.finditer(text):
                repo = UniversalRepository(
                    name        = m.group(1),
                    entity      = m.group(3),
                    technology  = Technology.SPRING_JPA,
                    language    = Language.JAVA,
                    operations  = ["findById", "findAll", "save", "delete"],
                    source_file = str(java_file),
                    confidence  = ConfidenceLevel.HIGH,
                )
                model.repositories.append(repo)

            # REST Controllers
            if re.search(r'@(?:RestController|Controller)\b', text):
                endpoints = self._extract_endpoints(java_file, text)
                model.endpoints.extend(endpoints)

        return model

    # ------------------------------------------------------------------
    # Entity extraction
    # ------------------------------------------------------------------

    def _extract_entity(self, path: Path, text: str, pkg: str) -> Optional[UniversalEntity]:
        # Find class name
        class_match = re.search(
            r'(?:public\s+)?(?:abstract\s+)?class\s+(\w+)'
            r'(?:\s+extends\s+([\w<>]+))?(?:\s+implements\s+([\w<>, ]+))?',
            text,
        )
        if not class_match:
            return None

        class_name = class_match.group(1)
        base_types = [class_match.group(2)] if class_match.group(2) else []
        interfaces = [i.strip() for i in (class_match.group(3) or "").split(",") if i.strip()]

        fields     = self._extract_fields(text, str(path))
        aggregate  = self._aggregate_from_package(pkg, class_name)

        kind = EntityKind.ENTITY
        if re.search(r'@MappedSuperclass', text):
            kind = EntityKind.VALUE_OBJECT
        if re.search(r'@Embeddable', text):
            kind = EntityKind.VALUE_OBJECT
        if re.search(r'abstract\s+class', text):
            kind = EntityKind.ENTITY

        # Find line number of class declaration
        lines = text.split("\n")
        line_number = next(
            (i + 1 for i, ln in enumerate(lines) if class_name in ln and "class" in ln),
            None,
        )

        return UniversalEntity(
            name        = class_name,
            kind        = kind,
            technology  = Technology.SPRING_JPA,
            language    = Language.JAVA,
            namespace   = pkg,
            aggregate   = aggregate,
            fields      = fields,
            source_file = str(path),
            line_number = line_number,
            confidence  = ConfidenceLevel.HIGH,
            base_types  = base_types,
            interfaces  = interfaces,
            raw         = {"package": pkg, "file": str(path)},
        )

    def _extract_fields(self, text: str, source_file: str) -> list[UniversalField]:
        fields: list[UniversalField] = []
        lines  = text.split("\n")

        for m in _FIELD_RE.finditer(text):
            raw_type  = m.group(1).strip()
            field_name = m.group(2).strip()

            # Skip common non-field identifiers
            if field_name in ("class", "interface", "enum", "return", "new"):
                continue

            # Check what annotations precede this field
            start = max(0, m.start() - 200)
            context = text[start:m.start()]

            is_pk  = bool(_ID_ANN.search(context))
            is_nav = bool(re.search(r'@(?:OneToMany|ManyToOne|ManyToMany|OneToOne)\b', context))
            is_col = bool(re.search(r'@(?:OneToMany|ElementCollection)\b', context))
            is_emb = bool(re.search(r'@(?:Embedded|Embeddable)\b', context))

            # Column annotation details
            max_len = None
            col_match = re.search(r'@Column\s*\([^)]*length\s*=\s*(\d+)', context)
            if col_match:
                max_len = int(col_match.group(1))

            nullable = True
            if re.search(r'nullable\s*=\s*false', context):
                nullable = False
            if re.search(r'@NotNull\b|@NonNull\b', context):
                nullable = False

            base_type = raw_type.split("<")[0].strip()
            norm_type = _JAVA_TYPE_MAP.get(base_type, normalize_type(base_type))

            if is_pk:
                kind = FieldKind.PRIMITIVE
            elif is_col:
                kind = FieldKind.COLLECTION
            elif is_nav:
                kind = FieldKind.NAVIGATION
            elif is_emb:
                kind = FieldKind.EMBEDDED
            else:
                kind = FieldKind.PRIMITIVE

            # Find line number
            field_offset = text[:m.start()].count("\n") + 1

            pii = _detect_pii(field_name, norm_type)

            fields.append(UniversalField(
                name            = field_name,
                kind            = kind,
                raw_type        = raw_type,
                normalized_type = norm_type,
                is_pk           = is_pk,
                is_required     = not nullable,
                is_nullable     = nullable,
                max_length      = max_len,
                pii_risk        = pii,
                source_file     = source_file,
                line_number     = field_offset,
                confidence      = ConfidenceLevel.HIGH,
                raw             = {"context": context[-100:]},
            ))

        return fields

    # ------------------------------------------------------------------
    # Relationship extraction
    # ------------------------------------------------------------------

    def _extract_relationships(
        self, path: Path, text: str, source_entity: str
    ) -> list[UniversalRelationship]:
        rels: list[UniversalRelationship] = []

        for m in _REL_ANN.finditer(text):
            ann_type = m.group(1)
            join_col = m.group(2) or ""

            # Find the field type immediately after the annotation
            rest = text[m.end():]
            field_match = re.search(
                r'(?:private|protected|public)\s+([\w<>]+)\s+(\w+)\s*[;=]',
                rest[:300],
            )
            if not field_match:
                continue

            target_raw = field_match.group(1)
            # Strip generics: List<Order> → Order
            target = re.sub(r'(?:List|Set|Collection|Optional)<(\w+)>', r'\1', target_raw)
            target = target.split("<")[0]

            if target == source_entity or len(target) < 2:
                continue

            if ann_type == "OneToMany":
                kind = RelationshipKind.ONE_TO_MANY
            elif ann_type == "ManyToOne":
                kind = RelationshipKind.MANY_TO_ONE
            elif ann_type == "ManyToMany":
                kind = RelationshipKind.MANY_TO_MANY
            elif ann_type == "OneToOne":
                kind = RelationshipKind.ONE_TO_ONE
            elif ann_type in ("Embedded", "ElementCollection"):
                kind = RelationshipKind.EMBEDS
            else:
                kind = RelationshipKind.REFERENCES

            rels.append(UniversalRelationship(
                source      = source_entity,
                target      = target,
                kind        = kind,
                via         = join_col or field_match.group(2),
                technology  = Technology.SPRING_JPA,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                evidence    = f"@{ann_type}",
                raw         = {"annotation": m.group(0)},
            ))

        return rels

    # ------------------------------------------------------------------
    # Endpoint extraction
    # ------------------------------------------------------------------

    def _extract_endpoints(self, path: Path, text: str) -> list[UniversalEndpoint]:
        endpoints: list[UniversalEndpoint] = []

        # Extract class-level base path
        ctrl_match = re.search(
            r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\']', text
        )
        base_path = ctrl_match.group(1) if ctrl_match else ""
        base_path = base_path.rstrip("/")

        # Extract class name
        cls_match = re.search(r'(?:public\s+)?class\s+(\w+)', text)
        class_name = cls_match.group(1) if cls_match else ""

        # auth check
        auth_required = bool(re.search(
            r'@(?:PreAuthorize|Secured|RolesAllowed|Authorize)\b', text
        ))

        for m in _MAPPING_RE.finditer(text):
            ann   = m.group(1)
            route = m.group(2)

            method_map = {
                "GetMapping": "GET", "PostMapping": "POST",
                "PutMapping": "PUT", "DeleteMapping": "DELETE",
                "PatchMapping": "PATCH", "RequestMapping": "GET",
            }
            http_method = method_map.get(ann, "GET")

            full_path = (base_path + "/" + route.lstrip("/")).rstrip("/") or "/"
            if not full_path.startswith("/"):
                full_path = "/" + full_path

            # Find the method name that follows this annotation
            rest = text[m.end():]
            method_match = re.search(r'public\s+[\w<>]+\s+(\w+)\s*\(', rest[:400])
            handler_method = method_match.group(1) if method_match else ""

            endpoints.append(UniversalEndpoint(
                method         = http_method,
                path           = full_path,
                style          = EndpointStyle.SPRING_MVC,
                technology     = Technology.SPRING_JPA,
                language       = Language.JAVA,
                handler_class  = class_name,
                handler_method = handler_method,
                auth_required  = auth_required,
                source_file    = str(path),
                confidence     = ConfidenceLevel.HIGH,
                raw            = {"annotation": m.group(0)},
            ))

        return endpoints

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_package(self, text: str) -> str:
        m = _PACKAGE_RE.search(text)
        return m.group(1) if m else ""

    def _aggregate_from_package(self, pkg: str, entity: str) -> str:
        """
        com.example.order.entity.Order → OrderAggregate
        com.example.catalog.entity.Product → CatalogAggregate
        """
        _GENERIC = {"entity", "entities", "model", "models", "domain",
                    "persistence", "repository", "service", "dto"}
        parts = pkg.split(".")
        for part in reversed(parts):
            if part.lower() not in _GENERIC and len(part) > 2:
                return part.capitalize() + "Aggregate"
        return entity + "Aggregate"


# ---------------------------------------------------------------------------
# PII detection helper
# ---------------------------------------------------------------------------

_PII_HIGH   = re.compile(
    r'\b(ssn|social_security|password|passwd|credit_card|card_number|cvv|pin)\b', re.I
)
_PII_MEDIUM = re.compile(
    r'\b(email|phone|mobile|address|birth|dob|salary|tax_id|national_id)\b', re.I
)
_PII_LOW = re.compile(r'\b(name|first_name|last_name|full_name|username|user_name)\b', re.I)


def _detect_pii(field_name: str, field_type: str) -> Optional[str]:
    if _PII_HIGH.search(field_name):
        return "high"
    if _PII_MEDIUM.search(field_name):
        return "medium"
    if _PII_LOW.search(field_name):
        return "low"
    return None
