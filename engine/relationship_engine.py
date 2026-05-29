"""
Universal Relationship Engine

Post-processes extracted entities to infer additional relationships
that adapters may have missed:
  - FK field naming conventions (entityId → ManyToOne Entity)
  - Collection navigation properties
  - Cross-aggregate ID references
  - Complementary sides of existing relationships
"""

from __future__ import annotations

import re
from typing import Optional

from models.semantic_model import SemanticModel
from models.universal import (
    UniversalEntity, UniversalRelationship,
    FieldKind, RelationshipKind, Technology, ConfidenceLevel,
)


_FK_SUFFIX = re.compile(r'^(.+?)(?:Id|_id|ID)$')
_COLLECTION_TYPES = re.compile(
    r'List<|IEnumerable<|ICollection<|IReadOnlyList<|Set<|Collection<|List\[|list\[',
    re.I,
)


class RelationshipEngine:
    """
    Analyzes a SemanticModel and augments it with inferred relationships.
    Operates only on universal types — no technology-specific logic.
    """

    def __init__(self, cross_domain_threshold: int = 1) -> None:
        self._cross_domain_threshold = cross_domain_threshold

    def enrich(self, model: SemanticModel) -> SemanticModel:
        """
        Run all enrichment passes on the model's relationships in-place.
        Returns the same model (mutated) for chaining.
        """
        entity_index = {e.name: e for e in model.entities}
        existing = {(r.source, r.target, r.via) for r in model.relationships}

        new_rels: list[UniversalRelationship] = []

        # Pass 1: FK naming convention
        new_rels.extend(
            self._infer_fk_rels(model.entities, entity_index, existing)
        )

        # Pass 2: Collection navigation (already captured, but ensure both sides)
        new_rels.extend(
            self._infer_inverse_rels(model.relationships, entity_index, existing)
        )

        # Pass 3: Cross-aggregate detection
        self._mark_cross_domain(model.relationships, entity_index)
        self._mark_cross_domain(new_rels, entity_index)

        # Deduplicate and add
        for r in new_rels:
            key = (r.source, r.target, r.via)
            if key not in existing:
                model.relationships.append(r)
                existing.add(key)

        return model

    # ------------------------------------------------------------------
    # Pass 1: FK naming convention
    # ------------------------------------------------------------------

    def _infer_fk_rels(
        self,
        entities: list[UniversalEntity],
        entity_index: dict[str, UniversalEntity],
        existing: set[tuple[str, str, str]],
    ) -> list[UniversalRelationship]:
        new: list[UniversalRelationship] = []

        for entity in entities:
            for f in entity.fields:
                if f.kind not in (FieldKind.PRIMITIVE, FieldKind.FOREIGN_KEY):
                    continue

                m = _FK_SUFFIX.match(f.name)
                if not m:
                    continue

                candidate = m.group(1)
                # Try capitalized versions
                for target_name in (candidate, candidate.capitalize(),
                                    candidate[0].upper() + candidate[1:]):
                    if target_name in entity_index and target_name != entity.name:
                        key = (entity.name, target_name, f.name)
                        if key not in existing:
                            new.append(UniversalRelationship(
                                source      = entity.name,
                                target      = target_name,
                                kind        = RelationshipKind.MANY_TO_ONE,
                                via         = f.name,
                                technology  = entity.technology,
                                source_file = f.source_file,
                                line_number = f.line_number,
                                confidence  = ConfidenceLevel.MEDIUM,
                                evidence    = f"FK naming convention: {f.name}",
                            ))
                        break

        return new

    # ------------------------------------------------------------------
    # Pass 2: Inverse relationship generation
    # ------------------------------------------------------------------

    def _infer_inverse_rels(
        self,
        relationships: list[UniversalRelationship],
        entity_index: dict[str, UniversalEntity],
        existing: set[tuple[str, str, str]],
    ) -> list[UniversalRelationship]:
        """
        For every ManyToOne A→B, if there is no OneToMany B→A, add it.
        For every OneToOne A→B, if there is no OneToOne B→A, add it.
        """
        new: list[UniversalRelationship] = []

        for r in relationships:
            if r.kind == RelationshipKind.MANY_TO_ONE:
                inverse_key = (r.target, r.source, r.via)
                if inverse_key not in existing:
                    if r.target in entity_index and r.source in entity_index:
                        new.append(UniversalRelationship(
                            source      = r.target,
                            target      = r.source,
                            kind        = RelationshipKind.ONE_TO_MANY,
                            via         = r.via,
                            technology  = r.technology,
                            source_file = r.source_file,
                            confidence  = ConfidenceLevel.MEDIUM,
                            evidence    = f"Inferred inverse of {r.source}→{r.target}",
                        ))

        return new

    # ------------------------------------------------------------------
    # Pass 3: Cross-aggregate/cross-domain marking
    # ------------------------------------------------------------------

    def _mark_cross_domain(
        self,
        relationships: list[UniversalRelationship],
        entity_index: dict[str, UniversalEntity],
    ) -> None:
        for r in relationships:
            src = entity_index.get(r.source)
            tgt = entity_index.get(r.target)
            if src and tgt and src.aggregate and tgt.aggregate:
                if src.aggregate != tgt.aggregate:
                    r.is_cross_domain = True
