"""
SQL Relationship Engine  — Priority 1

Reconstructs ALL relationships from SQL constraints with full evidence:
  - FOREIGN KEY constraints (PRIMARY source of truth)
  - UNIQUE constraints (implicit candidate keys)
  - Inferred joins from stored procedure / view body text
  - Named constraint evidence (constraint_name, source_file, line_number)

Confidence levels:
  HIGH   — explicit FOREIGN KEY / PRIMARY KEY constraint in DDL
  MEDIUM — JOIN condition in view/procedure body (inferred join)
  LOW    — name-convention match only (e.g. OrderID on Orders table)

Generates:
  memory/extracted/relationships.json      (USM-compatible, for m3_agent)
  memory/extracted/sql_relationships.json  (SQL-specific, full constraint detail)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.semantic_model import SemanticModel


# ---------------------------------------------------------------------------
# Output structures
# ---------------------------------------------------------------------------

@dataclass
class SQLRelationship:
    source_table:       str
    target_table:       str
    source_columns:     list[str]
    target_columns:     list[str]
    constraint_name:    str
    relationship_kind:  str    # many_to_one | one_to_many | many_to_many | self_ref
    on_delete:          str    = "NO ACTION"
    on_update:          str    = "NO ACTION"
    confidence:         str    = "HIGH"
    evidence:           str    = ""
    source_file:        str    = ""
    line_number:        Optional[int] = None
    is_self_ref:        bool   = False
    is_inferred:        bool   = False

    def to_dict(self) -> dict:
        return {
            "source_table":      self.source_table,
            "target_table":      self.target_table,
            "source_columns":    self.source_columns,
            "target_columns":    self.target_columns,
            "constraint_name":   self.constraint_name,
            "relationship_kind": self.relationship_kind,
            "on_delete":         self.on_delete,
            "on_update":         self.on_update,
            "confidence":        self.confidence,
            "evidence":          self.evidence,
            "source_file":       self.source_file,
            "line_number":       self.line_number,
            "is_self_ref":       self.is_self_ref,
            "is_inferred":       self.is_inferred,
        }

    def to_usm_dict(self) -> dict:
        """USM-compatible format for relationships.json."""
        via = ", ".join(self.source_columns) if self.source_columns else ""
        return {
            "source":        self.source_table,
            "target":        self.target_table,
            "relationship":  self.relationship_kind,
            "via":           via,
            "via_type":      "FK" if not self.is_inferred else "INFERRED_JOIN",
            "confidence":    self.confidence,
            "evidence":      self.evidence,
            "source_file":   self.source_file,
            "line_number":   self.line_number,
            "constraint_name": self.constraint_name,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SQLRelationshipEngine:
    """
    Extracts all SQL relationships from a SemanticModel.
    Constraints are the primary source of truth (HIGH confidence).
    JOIN patterns in procedure/view bodies provide MEDIUM confidence supplemental data.
    """

    def build(self, model: SemanticModel) -> dict[str, Any]:
        relationships: list[SQLRelationship] = []
        entity_names = {e.name.lower() for e in model.entities}

        # ── PASS 1: FK constraints from entities (HIGH confidence) ─────────
        for entity in model.entities:
            raw_fks = getattr(entity, "raw", {})
            if not isinstance(raw_fks, dict):
                raw_fks = {}
            fk_list = raw_fks.get("foreign_keys", [])

            for fk in fk_list:
                if not isinstance(fk, dict):
                    continue
                target = fk.get("references_table", "")
                if not target:
                    continue

                src_cols  = fk.get("columns", [])
                tgt_cols  = fk.get("references_columns", [])
                cname     = fk.get("constraint_name", "")
                on_del    = fk.get("on_delete", "NO ACTION")
                on_upd    = fk.get("on_update", "NO ACTION")
                src_file  = fk.get("source_file", entity.source_file or "")
                line_no   = fk.get("line_number")

                kind = "self_ref" if target.lower() == entity.name.lower() else "many_to_one"
                evidence = (
                    f"FOREIGN KEY constraint{' ' + cname if cname else ''}: "
                    f"{entity.name}.({', '.join(src_cols)}) -> {target}.({', '.join(tgt_cols)})"
                )

                relationships.append(SQLRelationship(
                    source_table    = entity.name,
                    target_table    = target,
                    source_columns  = src_cols,
                    target_columns  = tgt_cols,
                    constraint_name = cname,
                    relationship_kind = kind,
                    on_delete       = on_del,
                    on_update       = on_upd,
                    confidence      = "HIGH",
                    evidence        = evidence,
                    source_file     = src_file,
                    line_number     = line_no,
                    is_self_ref     = kind == "self_ref",
                ))

        # ── PASS 2: Relationships from SemanticModel.relationships (already extracted) ──
        seen_constraints: set[str] = {
            r.constraint_name.lower()
            for r in relationships
            if r.constraint_name
        }
        for rel in model.relationships:
            cname = getattr(rel, "constraint_name", "") or ""
            if cname and cname.lower() in seen_constraints:
                continue
            via = rel.via or ""
            src_cols = [c.strip() for c in via.split(",") if c.strip()] if via else []
            if hasattr(rel, "kind"):
                kind = rel.kind.value if hasattr(rel.kind, "value") else str(rel.kind)
            elif hasattr(rel, "relationship"):
                kind = str(rel.relationship)
            else:
                kind = "many_to_one"
            evidence = getattr(rel, "evidence", f"Relationship: {rel.source} -> {rel.target}")
            relationships.append(SQLRelationship(
                source_table    = rel.source,
                target_table    = rel.target,
                source_columns  = src_cols,
                target_columns  = [],
                constraint_name = cname,
                relationship_kind = kind,
                confidence      = rel.confidence.value if hasattr(rel.confidence, "value") else str(rel.confidence),
                evidence        = evidence,
                source_file     = rel.source_file or "",
                line_number     = getattr(rel, "line_number", None),
                is_self_ref     = rel.source.lower() == rel.target.lower(),
            ))

        # ── PASS 3: Inferred joins from view/procedure bodies (MEDIUM confidence) ──
        inferred = self._infer_from_bodies(model, entity_names, relationships)
        relationships.extend(inferred)

        # ── PASS 4: Naming-convention inference (LOW confidence, fill gaps) ──
        convention_inferred = self._infer_from_naming(model, entity_names, relationships)
        relationships.extend(convention_inferred)

        # ── Dedup ────────────────────────────────────────────────────────────
        relationships = self._dedup(relationships)

        # ── Build output ─────────────────────────────────────────────────────
        fk_count     = sum(1 for r in relationships if r.confidence == "HIGH" and not r.is_inferred)
        infer_count  = sum(1 for r in relationships if r.is_inferred)
        self_refs    = sum(1 for r in relationships if r.is_self_ref)

        return {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "relationship_count": len(relationships),
            "fk_constraint_count": fk_count,
            "inferred_count":      infer_count,
            "self_reference_count": self_refs,
            "confidence_breakdown": {
                "HIGH":   sum(1 for r in relationships if r.confidence == "HIGH"),
                "MEDIUM": sum(1 for r in relationships if r.confidence == "MEDIUM"),
                "LOW":    sum(1 for r in relationships if r.confidence == "LOW"),
            },
            "relationships": [r.to_dict() for r in relationships],
        }

    def _infer_from_bodies(
        self,
        model: SemanticModel,
        entity_names: set[str],
        existing: list[SQLRelationship],
    ) -> list[SQLRelationship]:
        """Extract MEDIUM confidence join relationships from procedure/view bodies."""
        inferred: list[SQLRelationship] = []
        existing_pairs = {
            (r.source_table.lower(), r.target_table.lower())
            for r in existing
        }

        join_re = re.compile(
            r'\b(?:INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|'
            r'FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+JOIN|JOIN)\s+'
            r'(?:\[?(\w+)\]?\.)?\[?(\w+)\]?\s+'
            r'(?:AS\s+\w+\s+)?ON\s+([\w\.\[\] =<>!]+)',
            re.IGNORECASE,
        )

        sources_checked: set[tuple] = set()

        for hdl in model.handlers:
            raw = getattr(hdl, "raw", {})
            body = raw.get("body", "") if isinstance(raw, dict) else ""
            if not body:
                continue
            src_name = hdl.name

            # Find the tables this handler reads (for the FROM clause table)
            from_re = re.compile(r'\bFROM\s+(?:\[?(\w+)\]?\.)?\[?(\w+)\]?', re.IGNORECASE)
            from_tables = []
            for fm in from_re.finditer(body):
                t = fm.group(2) or fm.group(1) or ""
                if t and t.lower() in entity_names:
                    from_tables.append(t)

            for jm in join_re.finditer(body):
                join_tbl = jm.group(2) or jm.group(1) or ""
                if not join_tbl or join_tbl.lower() not in entity_names:
                    continue

                # Try to find source table from ON condition columns
                cond = jm.group(3) or ""
                on_tbls = re.findall(r'\[?(\w+)\]?\.\[?(\w+)\]?', cond)
                for left_tbl, _ in on_tbls:
                    if left_tbl.lower() in entity_names and left_tbl.lower() != join_tbl.lower():
                        pair = (left_tbl.lower(), join_tbl.lower())
                        if pair not in existing_pairs and pair not in sources_checked:
                            sources_checked.add(pair)
                            inferred.append(SQLRelationship(
                                source_table    = left_tbl,
                                target_table    = join_tbl,
                                source_columns  = [],
                                target_columns  = [],
                                constraint_name = "",
                                relationship_kind = "many_to_one",
                                confidence      = "MEDIUM",
                                evidence        = f"JOIN pattern in PROC:{src_name} ON {cond[:100]}",
                                source_file     = hdl.source_file or "",
                                is_inferred     = True,
                            ))

        # Views
        for ep in model.endpoints:
            if ep.handler_class != "SQL View":
                continue
            src_tbls = list(ep.entities_touched)
            for i, t1 in enumerate(src_tbls):
                for t2 in src_tbls[i+1:]:
                    if t1.lower() == t2.lower():
                        continue
                    pair = (t1.lower(), t2.lower())
                    if pair not in existing_pairs and pair not in sources_checked:
                        sources_checked.add(pair)
                        inferred.append(SQLRelationship(
                            source_table    = t1,
                            target_table    = t2,
                            source_columns  = [],
                            target_columns  = [],
                            constraint_name = "",
                            relationship_kind = "many_to_one",
                            confidence      = "MEDIUM",
                            evidence        = f"Both tables appear in VIEW:{ep.handler_method}",
                            source_file     = ep.source_file or "",
                            is_inferred     = True,
                        ))

        return inferred

    def _infer_from_naming(
        self,
        model: SemanticModel,
        entity_names: set[str],
        existing: list[SQLRelationship],
    ) -> list[SQLRelationship]:
        """LOW confidence: column name ends with 'ID' and matches another table name."""
        existing_pairs = {
            (r.source_table.lower(), r.target_table.lower())
            for r in existing
        }
        inferred: list[SQLRelationship] = []
        seen: set[tuple] = set()

        for entity in model.entities:
            for col in entity.fields:
                if not col.name.lower().endswith("id"):
                    continue
                if col.is_pk:
                    continue
                # Candidate: CustomerID -> Customers or Customer table
                base = col.name[:-2].lower()  # strip "ID"
                candidates = [base, base + "s"]
                for cand in candidates:
                    if cand in entity_names and cand != entity.name.lower():
                        # Find actual table name with correct case
                        actual = next(
                            (e.name for e in model.entities if e.name.lower() == cand), cand
                        )
                        pair = (entity.name.lower(), actual.lower())
                        if pair not in existing_pairs and pair not in seen:
                            seen.add(pair)
                            inferred.append(SQLRelationship(
                                source_table    = entity.name,
                                target_table    = actual,
                                source_columns  = [col.name],
                                target_columns  = [],
                                constraint_name = "",
                                relationship_kind = "many_to_one",
                                confidence      = "LOW",
                                evidence        = (
                                    f"Column '{col.name}' name convention matches table '{actual}' "
                                    f"(no explicit FK constraint found)"
                                ),
                                source_file     = entity.source_file or "",
                                line_number     = getattr(col, "line_number", None),
                                is_inferred     = True,
                            ))
        return inferred

    def _dedup(self, rels: list[SQLRelationship]) -> list[SQLRelationship]:
        """Deduplicate: prefer HIGH over MEDIUM over LOW for same source→target pair."""
        best: dict[tuple, SQLRelationship] = {}
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        for r in rels:
            key = (r.source_table.lower(), r.target_table.lower())
            existing = best.get(key)
            if existing is None or order.get(r.confidence, 99) < order.get(existing.confidence, 99):
                best[key] = r
        return sorted(best.values(), key=lambda r: r.source_table)

    def save(self, model: SemanticModel, output_dir: str | Path) -> dict[str, Any]:
        result = self.build(model)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # sql_relationships.json — full SQL-specific format
        (out / "sql_relationships.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )

        # relationships.json — USM-compatible format (overwrites code-first version for SQL projects)
        usm_rels = [
            r.to_usm_dict()
            for r in [
                SQLRelationship(**{
                    **{k: v for k, v in rd.items()
                       if k in SQLRelationship.__dataclass_fields__}
                })
                for rd in result["relationships"]
            ]
        ]
        usm_doc = {
            "generated_at":       result["generated_at"],
            "relationship_count": result["relationship_count"],
            "relationships":      usm_rels,
        }
        (out / "relationships.json").write_text(
            json.dumps(usm_doc, indent=2, default=str), encoding="utf-8"
        )

        fk = result["fk_constraint_count"]
        inf = result["inferred_count"]
        print(
            f"[SQLRelationshipEngine] {result['relationship_count']} relationships "
            f"({fk} FK constraints, {inf} inferred) -> sql_relationships.json + relationships.json"
        )
        return result
