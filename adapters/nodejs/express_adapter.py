"""
Node.js Adapter — Express / Sequelize / Mongoose / TypeORM / Prisma

Extracts entities, repositories, and API endpoints from Node.js / TypeScript projects.

Supports:
  - Sequelize (DataTypes, Model.init, @Table)
  - Mongoose (new Schema, mongoose.model)
  - TypeORM (@Entity, @Column, @OneToMany)
  - Prisma (schema.prisma model blocks)
  - Express (router.get/post/put/delete/patch)
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
# TypeORM / TypeScript patterns
# ---------------------------------------------------------------------------

_TYPEORM_ENTITY_RE  = re.compile(
    r'@Entity\s*\([^)]*\)\s*(?:@\w+\s*(?:\([^)]*\))?\s*)*'
    r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)',
    re.MULTILINE,
)
_TYPEORM_COL_RE = re.compile(
    r'@(?:Column|PrimaryColumn|PrimaryGeneratedColumn|CreateDateColumn|UpdateDateColumn)'
    r'\s*(?:\([^)]*\))?\s*(?:\w+\s*!?\??\s*:\s*([\w<>\[\]| ]+)\s*;)',
    re.MULTILINE,
)
_TYPEORM_FIELD_RE = re.compile(
    r'@(?:Column|PrimaryColumn|PrimaryGeneratedColumn)\s*(?:\([^)]*\))?\n\s*'
    r'(\w+)\s*[!?]?\s*:\s*([\w<>\[\] |]+)',
    re.MULTILINE,
)
_TYPEORM_REL_RE = re.compile(
    r'@(OneToMany|ManyToOne|ManyToMany|OneToOne)\s*\(\s*(?:type\s*=>\s*)?\s*'
    r'(?:\([^)]*\)\s*=>\s*)?(\w+)',
    re.MULTILINE,
)

# Mongoose
_MONGOOSE_SCHEMA_RE  = re.compile(
    r'(?:const|let|var)\s+(\w+)Schema\s*=\s*new\s+(?:mongoose\.)?Schema\s*\(',
    re.MULTILINE,
)
_MONGOOSE_MODEL_RE   = re.compile(
    r'(?:mongoose\.model|model)\s*<[^>]*>\s*\(\s*["\'](\w+)["\']',
    re.MULTILINE,
)
_MONGOOSE_FIELD_RE = re.compile(
    r'^\s{2,4}(\w+)\s*:\s*\{[^}]*type\s*:\s*(\w+)',
    re.MULTILINE,
)

# Sequelize
_SEQUELIZE_DEFINE_RE = re.compile(
    r'(?:sequelize|db)\.define\s*\(\s*["\'](\w+)["\']',
    re.MULTILINE,
)
_SEQUELIZE_MODEL_RE  = re.compile(
    r'(?:export\s+)?class\s+(\w+)\s+extends\s+Model\s*<',
    re.MULTILINE,
)
_SEQUELIZE_FIELD_RE  = re.compile(
    r'(\w+)\s*:\s*\{[^}]*type\s*:\s*DataTypes\.(\w+)',
    re.MULTILINE,
)

# Prisma schema
_PRISMA_MODEL_RE = re.compile(r'^model\s+(\w+)\s*\{', re.MULTILINE)
_PRISMA_FIELD_RE = re.compile(
    r'^\s{2}(\w+)\s+([\w\[\]?]+)\s*(?:@[^\n]*)?$',
    re.MULTILINE,
)
_PRISMA_REL_RE = re.compile(
    r'^\s{2}(\w+)\s+(\w+)(\[\])?\s+(?:@relation[^\n]*)?\n',
    re.MULTILINE,
)

# Express routes
_EXPRESS_RE = re.compile(
    r'(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)

_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "coverage",
    ".next", "__pycache__", "vendor",
}

_TS_TYPE_MAP = {
    "string": "string", "String": "string",
    "number": "decimal", "Number": "decimal",
    "bigint": "integer", "BigInt": "integer",
    "boolean": "boolean", "Boolean": "boolean",
    "Date": "datetime",
    "Buffer": "bytes",
    "any": "any", "object": "json",
    "Record": "json", "JSON": "json",
    "UUID": "uuid",
    "TEXT": "string", "VARCHAR": "string",
    "INTEGER": "integer", "BIGINT": "integer",
    "FLOAT": "decimal", "DOUBLE": "decimal", "DECIMAL": "decimal",
    "BOOLEAN": "boolean", "DATE": "datetime", "DATEONLY": "date",
    "UUID_TYPE": "uuid", "JSONB": "json", "JSON_TYPE": "json",
    "BLOB": "bytes",
}


class NodeJSAdapter(BaseAdapter):

    @property
    def name(self) -> str:
        return "Node.js / Express / Sequelize / Mongoose / TypeORM / Prisma"

    @property
    def supported_technologies(self) -> list[str]:
        return ["sequelize", "mongoose", "typeorm", "prisma"]

    def can_handle(self, tech_context: TechContext) -> bool:
        return (
            Language.JAVASCRIPT in tech_context.languages
            or Language.TYPESCRIPT in tech_context.languages
            or any(t in tech_context.orms for t in [
                Technology.SEQUELIZE, Technology.MONGOOSE,
                Technology.TYPEORM, Technology.PRISMA,
            ])
        )

    def extract(self, tech_context: TechContext) -> SemanticModel:
        model = SemanticModel(
            project_root = tech_context.project_root,
            tech_context = tech_context,
            adapter_used = self.name,
        )

        root = Path(tech_context.project_root)

        # Prisma schema (separate file type)
        for prisma_file in root.rglob("schema.prisma"):
            if any(p in _SKIP_DIRS for p in prisma_file.parts):
                continue
            text = self._read(str(prisma_file))
            entities, rels = self._extract_prisma(prisma_file, text)
            model.entities.extend(entities)
            model.relationships.extend(rels)

        # TS/JS source files
        for src_file in root.rglob("*.ts"):
            if any(p in _SKIP_DIRS for p in src_file.parts):
                continue
            text = self._read(str(src_file))
            if not text:
                continue

            # TypeORM entities
            if _TYPEORM_ENTITY_RE.search(text):
                entities, rels = self._extract_typeorm(src_file, text)
                model.entities.extend(entities)
                model.relationships.extend(rels)

            # Mongoose schemas
            if _MONGOOSE_SCHEMA_RE.search(text):
                entities = self._extract_mongoose(src_file, text)
                model.entities.extend(entities)

            # Express routes
            for m in _EXPRESS_RE.finditer(text):
                model.endpoints.append(self._make_endpoint(
                    method      = m.group(1).upper(),
                    path        = m.group(2),
                    source_file = str(src_file),
                    line_number = text[:m.start()].count("\n") + 1,
                ))

        for src_file in list(root.rglob("*.js")):
            if any(p in _SKIP_DIRS for p in src_file.parts):
                continue
            text = self._read(str(src_file))
            if not text:
                continue

            # Sequelize (JS or TS)
            if _SEQUELIZE_DEFINE_RE.search(text) or _SEQUELIZE_MODEL_RE.search(text):
                entities = self._extract_sequelize(src_file, text)
                model.entities.extend(entities)

            # Mongoose
            if _MONGOOSE_SCHEMA_RE.search(text):
                entities = self._extract_mongoose(src_file, text)
                model.entities.extend(entities)

            # Express routes
            for m in _EXPRESS_RE.finditer(text):
                model.endpoints.append(self._make_endpoint(
                    method      = m.group(1).upper(),
                    path        = m.group(2),
                    source_file = str(src_file),
                    line_number = text[:m.start()].count("\n") + 1,
                ))

        return model

    # ------------------------------------------------------------------
    # Prisma
    # ------------------------------------------------------------------

    def _extract_prisma(
        self, path: Path, text: str
    ) -> tuple[list[UniversalEntity], list[UniversalRelationship]]:
        entities: list[UniversalEntity] = []
        rels:     list[UniversalRelationship] = []

        for m in _PRISMA_MODEL_RE.finditer(text):
            model_name = m.group(1)
            # Extract body until closing brace
            start = m.end()
            end   = text.find("\n}", start)
            if end < 0:
                end = len(text)
            body = text[start:end]

            fields: list[UniversalField] = []
            for fm in _PRISMA_FIELD_RE.finditer(body):
                fname    = fm.group(1)
                ftype_raw = fm.group(2)
                if fname in ("@@", "//"):
                    continue

                is_optional = ftype_raw.endswith("?")
                is_array    = ftype_raw.endswith("[]")
                base_type   = ftype_raw.rstrip("?[]")
                is_pk       = bool(re.search(r'@id\b', fm.group(0), re.I))
                norm_type   = _TS_TYPE_MAP.get(base_type, normalize_type(base_type))

                # Relationship field — target is another model (starts uppercase)
                if base_type[0].isupper() and base_type not in _TS_TYPE_MAP:
                    kind = FieldKind.COLLECTION if is_array else FieldKind.NAVIGATION
                    rels.append(UniversalRelationship(
                        source      = model_name,
                        target      = base_type,
                        kind        = RelationshipKind.ONE_TO_MANY if is_array else RelationshipKind.MANY_TO_ONE,
                        via         = fname,
                        technology  = Technology.PRISMA,
                        source_file = str(path),
                        confidence  = ConfidenceLevel.HIGH,
                        evidence    = "prisma schema field",
                    ))
                else:
                    kind = FieldKind.PRIMITIVE

                fields.append(UniversalField(
                    name            = fname,
                    kind            = kind,
                    raw_type        = ftype_raw,
                    normalized_type = norm_type,
                    is_pk           = is_pk,
                    is_nullable     = is_optional,
                    is_required     = not is_optional,
                    pii_risk        = _detect_pii(fname, norm_type),
                    source_file     = str(path),
                    confidence      = ConfidenceLevel.HIGH,
                ))

            entities.append(UniversalEntity(
                name        = model_name,
                kind        = EntityKind.ENTITY,
                technology  = Technology.PRISMA,
                language    = Language.TYPESCRIPT,
                namespace   = "",
                aggregate   = self._aggregate_from_name(model_name),
                fields      = fields,
                source_file = str(path),
                line_number = text[:m.start()].count("\n") + 1,
                confidence  = ConfidenceLevel.HIGH,
                raw         = {"orm": "prisma"},
            ))

        return entities, rels

    # ------------------------------------------------------------------
    # TypeORM
    # ------------------------------------------------------------------

    def _extract_typeorm(
        self, path: Path, text: str
    ) -> tuple[list[UniversalEntity], list[UniversalRelationship]]:
        entities: list[UniversalEntity] = []
        rels:     list[UniversalRelationship] = []

        for m in _TYPEORM_ENTITY_RE.finditer(text):
            class_name = m.group(1)
            start = m.end()
            # Find matching closing brace
            body_end = self._find_class_end(text, start)
            body = text[start:body_end]

            fields = self._extract_typeorm_fields(body, str(path))
            entity_rels = self._extract_typeorm_rels(path, body, class_name)
            rels.extend(entity_rels)

            entities.append(UniversalEntity(
                name        = class_name,
                kind        = EntityKind.ENTITY,
                technology  = Technology.TYPEORM,
                language    = Language.TYPESCRIPT,
                namespace   = "",
                aggregate   = self._aggregate_from_name(class_name),
                fields      = fields,
                source_file = str(path),
                line_number = text[:m.start()].count("\n") + 1,
                confidence  = ConfidenceLevel.HIGH,
                raw         = {"orm": "typeorm"},
            ))

        return entities, rels

    def _extract_typeorm_fields(self, body: str, source_file: str) -> list[UniversalField]:
        fields: list[UniversalField] = []
        for m in _TYPEORM_FIELD_RE.finditer(body):
            fname    = m.group(1)
            ftype    = m.group(2).strip()
            is_pk    = bool(re.search(r'@PrimaryGeneratedColumn|@PrimaryColumn', body[max(0, m.start()-100):m.start()]))
            norm     = _TS_TYPE_MAP.get(ftype.rstrip("[]?"), normalize_type(ftype))
            nullable = "?" in ftype or "| null" in ftype

            fields.append(UniversalField(
                name            = fname,
                kind            = FieldKind.PRIMITIVE,
                raw_type        = ftype,
                normalized_type = norm,
                is_pk           = is_pk,
                is_nullable     = nullable,
                is_required     = not nullable,
                pii_risk        = _detect_pii(fname, norm),
                source_file     = source_file,
                confidence      = ConfidenceLevel.HIGH,
            ))
        return fields

    def _extract_typeorm_rels(
        self, path: Path, body: str, source_entity: str
    ) -> list[UniversalRelationship]:
        rels: list[UniversalRelationship] = []
        for m in _TYPEORM_REL_RE.finditer(body):
            ann_type = m.group(1)
            target   = m.group(2)
            kind_map = {
                "OneToMany":  RelationshipKind.ONE_TO_MANY,
                "ManyToOne":  RelationshipKind.MANY_TO_ONE,
                "ManyToMany": RelationshipKind.MANY_TO_MANY,
                "OneToOne":   RelationshipKind.ONE_TO_ONE,
            }
            rels.append(UniversalRelationship(
                source      = source_entity,
                target      = target,
                kind        = kind_map.get(ann_type, RelationshipKind.ONE_TO_MANY),
                via         = "",
                technology  = Technology.TYPEORM,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                evidence    = f"@{ann_type}",
            ))
        return rels

    # ------------------------------------------------------------------
    # Mongoose
    # ------------------------------------------------------------------

    def _extract_mongoose(self, path: Path, text: str) -> list[UniversalEntity]:
        entities: list[UniversalEntity] = []

        for m in _MONGOOSE_SCHEMA_RE.finditer(text):
            schema_name = m.group(1)  # e.g. "User" from "UserSchema"
            # Try to find the model() call to get the canonical name
            model_m = re.search(
                rf'model\s*<[^>]*>\s*\(\s*["\']({schema_name})["\']', text
            )
            class_name = model_m.group(1) if model_m else schema_name

            # Extract schema body
            start = m.end()
            schema_end = self._find_paren_end(text, start)
            body = text[start:schema_end]

            fields: list[UniversalField] = []
            for fm in _MONGOOSE_FIELD_RE.finditer(body):
                fname    = fm.group(1)
                ftype_raw = fm.group(2)
                norm_type = _TS_TYPE_MAP.get(ftype_raw, normalize_type(ftype_raw))
                required  = bool(re.search(r'required\s*:\s*true', body[fm.start():fm.start()+200]))

                fields.append(UniversalField(
                    name            = fname,
                    kind            = FieldKind.PRIMITIVE,
                    raw_type        = ftype_raw,
                    normalized_type = norm_type,
                    is_required     = required,
                    is_nullable     = not required,
                    pii_risk        = _detect_pii(fname, norm_type),
                    source_file     = str(path),
                    confidence      = ConfidenceLevel.HIGH,
                ))

            entities.append(UniversalEntity(
                name        = class_name,
                kind        = EntityKind.ENTITY,
                technology  = Technology.MONGOOSE,
                language    = Language.JAVASCRIPT,
                namespace   = "",
                aggregate   = self._aggregate_from_name(class_name),
                fields      = fields,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                raw         = {"orm": "mongoose"},
            ))

        return entities

    # ------------------------------------------------------------------
    # Sequelize
    # ------------------------------------------------------------------

    def _extract_sequelize(self, path: Path, text: str) -> list[UniversalEntity]:
        entities: list[UniversalEntity] = []

        # Class-style model
        for m in _SEQUELIZE_MODEL_RE.finditer(text):
            class_name = m.group(1)
            start = m.end()
            body_end = self._find_class_end(text, start)
            body = text[start:body_end]

            fields: list[UniversalField] = []
            for fm in _SEQUELIZE_FIELD_RE.finditer(body):
                fname     = fm.group(1)
                seq_type  = fm.group(2)
                norm_type = _TS_TYPE_MAP.get(seq_type, normalize_type(seq_type))
                required  = bool(re.search(r'allowNull\s*:\s*false', body[fm.start():fm.start()+200]))

                fields.append(UniversalField(
                    name            = fname,
                    kind            = FieldKind.PRIMITIVE,
                    raw_type        = f"DataTypes.{seq_type}",
                    normalized_type = norm_type,
                    is_required     = required,
                    is_nullable     = not required,
                    pii_risk        = _detect_pii(fname, norm_type),
                    source_file     = str(path),
                    confidence      = ConfidenceLevel.HIGH,
                ))

            entities.append(UniversalEntity(
                name        = class_name,
                kind        = EntityKind.ENTITY,
                technology  = Technology.SEQUELIZE,
                language    = Language.JAVASCRIPT,
                namespace   = "",
                aggregate   = self._aggregate_from_name(class_name),
                fields      = fields,
                source_file = str(path),
                confidence  = ConfidenceLevel.HIGH,
                raw         = {"orm": "sequelize"},
            ))

        return entities

    # ------------------------------------------------------------------
    # Endpoint factory
    # ------------------------------------------------------------------

    def _make_endpoint(
        self, method: str, path: str,
        source_file: str, line_number: Optional[int] = None,
    ) -> UniversalEndpoint:
        tech = Technology.SEQUELIZE  # generic for Node
        if not path.startswith("/"):
            path = "/" + path
        return UniversalEndpoint(
            method      = method,
            path        = path,
            style       = EndpointStyle.EXPRESS_ROUTE,
            technology  = tech,
            language    = Language.JAVASCRIPT,
            source_file = source_file,
            line_number = line_number,
            confidence  = ConfidenceLevel.HIGH,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _aggregate_from_name(self, name: str) -> str:
        return name + "Aggregate"

    def _find_class_end(self, text: str, start: int) -> int:
        """Find closing brace of a class body."""
        depth = 0
        for i, ch in enumerate(text[start:]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start + i + 1
        return len(text)

    def _find_paren_end(self, text: str, start: int) -> int:
        """Find closing paren/bracket for schema body."""
        depth = 0
        for i, ch in enumerate(text[start:]):
            if ch in ("(", "{", "["):
                depth += 1
            elif ch in (")", "}", "]"):
                depth -= 1
                if depth < 0:
                    return start + i
        return len(text)


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

_PII_HIGH_RE   = re.compile(r'\b(password|passwd|ssn|credit_card|cvv|pin|secret)\b', re.I)
_PII_MEDIUM_RE = re.compile(r'\b(email|phone|address|birthday|dob|salary)\b', re.I)
_PII_LOW_RE    = re.compile(r'\b(name|firstName|lastName|username)\b', re.I)


def _detect_pii(field_name: str, field_type: str) -> Optional[str]:
    if _PII_HIGH_RE.search(field_name):
        return "high"
    if _PII_MEDIUM_RE.search(field_name):
        return "medium"
    if _PII_LOW_RE.search(field_name):
        return "low"
    return None
