"""
Relationship Detector — AST + EF Core configuration scanning.

Detection passes (ordered by confidence):
  Pass 1 (HIGH)   — IEntityTypeConfiguration (.HasMany/.HasOne/.OwnsOne/.OwnsMany)
  Pass 2 (HIGH)   — DbContext OnModelCreating direct mappings
  Pass 3 (HIGH)   — Collection navigation properties (AST-extracted)
  Pass 4 (HIGH)   — FK + scalar navigation pairs (AST-extracted)
  Pass 5 (MEDIUM) — Scalar navigation without matching FK (inferred)
  Pass 6 (LOW)    — Many-to-many junction table heuristic

Generates:
  memory/extracted/relationships.json
  memory/extracted/erd_map.json
  memory/extracted/dependency_graph.json
"""

from __future__ import annotations

import json
import re
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence

warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

# ---------------------------------------------------------------------------
# EF Core config patterns
# ---------------------------------------------------------------------------

_HAS_MANY   = re.compile(r'\.HasMany\s*(?:<[^>]+>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.)?(\w+)\s*\)')
_HAS_ONE    = re.compile(r'\.HasOne\s*(?:<[^>]+>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.)?(\w+)\s*\)')
_OWNS_ONE   = re.compile(r'\.OwnsOne\s*(?:<[^>]+>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.)?(\w+)\s*[,)]')
_OWNS_MANY  = re.compile(r'\.OwnsMany\s*(?:<[^>]+>)?\s*\(\s*(?:\w+\s*=>\s*\w+\.)?(\w+)\s*[,)]')
_ENTITY_CFG = re.compile(
    r'IEntityTypeConfiguration\s*<\s*(\w+)\s*>|'
    r'EntityTypeBuilder\s*<\s*(\w+)\s*>|'
    r'builder\s*=\s*modelBuilder\.Entity\s*<\s*(\w+)\s*>'
)
_HAS_FK        = re.compile(r'\.HasForeignKey\s*(?:<[^>]+>)?\s*\(')
_WITH_MANY     = re.compile(r'\.WithMany\s*\(')
_WITH_ONE      = re.compile(r'\.WithOne\s*\(')

# Extended EF semantics patterns
_HAS_INDEX     = re.compile(r'\.HasIndex\s*\(([^)]+)\)')
_IS_UNIQUE     = re.compile(r'\.IsUnique\s*\(\s*\)')
_HAS_MAX_LEN   = re.compile(r'\.HasMaxLength\s*\(\s*(\d+)\s*\)')
_IS_REQUIRED   = re.compile(r'\.IsRequired\s*\(\s*(true|false)?\s*\)')
_ON_DELETE     = re.compile(r'\.OnDelete\s*\(\s*DeleteBehavior\.(\w+)\s*\)')
_HAS_COL_TYPE  = re.compile(r'\.HasColumnType\s*\(\s*"([^"]+)"\s*\)')
_HAS_DEFAULT   = re.compile(r'\.HasDefaultValue\s*\(([^)]+)\)')
_CONCURRENCY   = re.compile(r'\.IsConcurrencyToken\s*\(\s*\)|\.IsRowVersion\s*\(\s*\)')
_HAS_ALT_KEY   = re.compile(r'\.HasAlternateKey\s*\(')
_PROP_CALL     = re.compile(r'\.Property\s*\(\s*\w+\s*=>\s*\w+\.(\w+)\s*\)'
                             r'|\.Property\s*<[^>]+>\s*\(\s*"(\w+)"\s*\)')

# ---------------------------------------------------------------------------
# Known external types (not in entity catalog)
# ---------------------------------------------------------------------------

_KNOWN_EXTERNAL = {"Card", "AspNetUser", "IdentityUser", "ApplicationUser", "Unknown"}

# ---------------------------------------------------------------------------
# Cross-domain detection
# ---------------------------------------------------------------------------

_AGGREGATE_DOMAINS = {
    "OrderAggregate":  {"Order", "OrderItem", "Address", "CatalogItemOrdered"},
    "BasketAggregate": {"Basket", "BasketItem"},
    "BuyerAggregate":  {"Buyer", "PaymentMethod"},
    "CatalogAggregate":{"CatalogItem", "CatalogBrand", "CatalogType"},
}

def _domain_of(entity: str) -> Optional[str]:
    for domain, members in _AGGREGATE_DOMAINS.items():
        if entity in members:
            return domain
    return None

def _is_cross_domain(source: str, target: str) -> bool:
    d1, d2 = _domain_of(source), _domain_of(target)
    return bool(d1 and d2 and d1 != d2)


# ---------------------------------------------------------------------------
# Relationship Detector
# ---------------------------------------------------------------------------

class RelationshipDetector:
    def __init__(
        self,
        output_dir: str = "memory/extracted",
        infra_dir: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.infra_dir  = Path(infra_dir) if infra_dir else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        entities_data: dict[str, Any],
        type_resolver: Any = None,
    ) -> dict[str, Any]:

        all_entity_names = {e["entity"] for e in entities_data.get("entities", [])}
        all_vo_names     = {v["entity"] for v in entities_data.get("value_objects", [])}
        all_classes      = all_entity_names | all_vo_names

        relationships: list[dict] = []

        # Pass 1 + 2: EF Core configuration files
        ef_rels = self._scan_ef_config(all_classes)
        relationships.extend(ef_rels)
        print(f"  [Pass1-EFConfig] {len(ef_rels)} relationships from EF config")

        # Pass 3: Collection navigation properties (one_to_many / many_to_many)
        coll_rels = self._from_collection_nav(entities_data, all_classes)
        relationships.extend(coll_rels)
        print(f"  [Pass2-CollNav]  {len(coll_rels)} from collection navigation")

        # Pass 4: FK + scalar nav pairs (many_to_one)
        fk_rels = self._from_fk_pairs(entities_data, all_classes)
        relationships.extend(fk_rels)
        print(f"  [Pass3-FKPairs]  {len(fk_rels)} from FK pairs")

        # Pass 5: Owned value objects (embeds_value_object)
        vo_rels = self._from_owned_vos(entities_data, all_vo_names)
        relationships.extend(vo_rels)
        print(f"  [Pass4-OwnedVO]  {len(vo_rels)} owned value objects")

        # Pass 6: Scalar nav without FK (inferred references)
        inferred = self._from_scalar_nav(entities_data, all_classes, relationships)
        relationships.extend(inferred)
        print(f"  [Pass5-Inferred] {len(inferred)} inferred from scalar nav")

        # Deduplicate keeping highest confidence
        relationships = self._deduplicate(relationships)

        # Annotate cross-domain
        for r in relationships:
            r["cross_domain"] = _is_cross_domain(r.get("source",""), r.get("target",""))

        # Write outputs
        self._write_relationships(relationships)
        self._write_erd_map(relationships, all_entity_names)
        self._write_dependency_graph(relationships, all_entity_names, all_vo_names)
        self._write_ef_semantics(all_entity_names | all_vo_names)

        result = {
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "relationship_count": len(relationships),
            "relationships":      relationships,
        }
        print(f"[RelationshipDetector] {len(relationships)} total relationships written")
        return result

    # ------------------------------------------------------------------
    # Pass 1+2: EF Core IEntityTypeConfiguration scanning
    # ------------------------------------------------------------------

    def _scan_ef_config(self, all_classes: set[str]) -> list[dict]:
        if not self.infra_dir or not self.infra_dir.exists():
            return []

        config_files = list(self.infra_dir.rglob("*Configuration*.cs")) + \
                       list(self.infra_dir.rglob("*Context*.cs"))

        relationships: list[dict] = []

        for cfg_file in config_files:
            if "Migration" in cfg_file.name or "Snapshot" in cfg_file.name:
                continue
            try:
                text = cfg_file.read_text(encoding="utf-8", errors="replace")
                src  = str(cfg_file)
                relationships.extend(
                    self._extract_ef_rels(text, src, all_classes))
            except Exception as exc:
                print(f"  [WARN] EF config scan {cfg_file.name}: {exc}")

        return relationships

    def _extract_ef_rels(self, text: str, src: str, all_classes: set[str]) -> list[dict]:
        rels: list[dict] = []
        lines = text.splitlines()

        # Determine which entity this config file is for
        source_entity = None
        for m in _ENTITY_CFG.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3)
            if name and name in all_classes:
                source_entity = name
                break

        if not source_entity:
            return []

        for i, line in enumerate(lines, 1):
            # OwnsOne → embeds_value_object (HIGH)
            for m in _OWNS_ONE.finditer(line):
                prop = m.group(1)
                target = self._resolve_nav_prop_type(source_entity, prop)
                rels.append(self._make_rel(
                    source_entity, target or prop, "embeds_value_object",
                    prop, Confidence.HIGH.value, src, i,
                    "OwnsOne EF config"))

            # OwnsMany → owns_many (HIGH)
            for m in _OWNS_MANY.finditer(line):
                prop = m.group(1)
                target = self._resolve_nav_prop_type(source_entity, prop)
                rels.append(self._make_rel(
                    source_entity, target or prop, "owns_many",
                    prop, Confidence.HIGH.value, src, i,
                    "OwnsMany EF config"))

            # HasMany → one_to_many (HIGH)
            for m in _HAS_MANY.finditer(line):
                prop = m.group(1)
                target = self._resolve_nav_prop_type(source_entity, prop)
                # Check for WithMany → many_to_many
                rel_type = "one_to_many"
                if i + 5 < len(lines):
                    nearby = " ".join(lines[i:i+5])
                    if _WITH_MANY.search(nearby):
                        rel_type = "many_to_many"
                rels.append(self._make_rel(
                    source_entity, target or prop, rel_type,
                    prop, Confidence.HIGH.value, src, i,
                    f"HasMany EF config"))

            # HasOne → many_to_one or one_to_one (HIGH)
            for m in _HAS_ONE.finditer(line):
                prop = m.group(1)
                target = self._resolve_nav_prop_type(source_entity, prop)
                rel_type = "many_to_one"
                if i + 5 < len(lines):
                    nearby = " ".join(lines[i:i+5])
                    if _WITH_ONE.search(nearby):
                        rel_type = "one_to_one"
                rels.append(self._make_rel(
                    source_entity, target or prop, rel_type,
                    prop, Confidence.HIGH.value, src, i,
                    "HasOne EF config"))

        return rels

    # ------------------------------------------------------------------
    # Pass 3: Collection navigation properties
    # ------------------------------------------------------------------

    def _from_collection_nav(self, entities_data: dict,
                              all_classes: set[str]) -> list[dict]:
        rels = []
        for ent in entities_data.get("entities", []):
            src  = ent["entity"]
            file = ent.get("source_file", "")
            for nav in ent.get("navigation_collection", []):
                target = nav.get("target_entity", "")
                if not target or target not in all_classes:
                    continue
                rels.append(self._make_rel(
                    src, target, "one_to_many",
                    nav["name"], Confidence.HIGH.value, file,
                    nav.get("line_number"), "Collection navigation property (AST)"))
        return rels

    # ------------------------------------------------------------------
    # Pass 4: FK + scalar nav pairs
    # ------------------------------------------------------------------

    def _from_fk_pairs(self, entities_data: dict,
                       all_classes: set[str]) -> list[dict]:
        rels = []
        for ent in entities_data.get("entities", []):
            src  = ent["entity"]
            file = ent.get("source_file", "")
            # Map nav target → nav property name
            nav_by_target = {
                n["target_entity"]: n for n in ent.get("navigation_scalar", [])
            }
            for fk in ent.get("foreign_keys", []):
                ref = fk.get("references", "")
                if not ref:
                    continue
                nav = nav_by_target.get(ref)
                if ref in all_classes:
                    rels.append(self._make_rel(
                        src, ref, "many_to_one",
                        nav["name"] if nav else fk["name"],
                        Confidence.HIGH.value, file, fk.get("line_number"),
                        f"FK field {fk['name']} + nav property (AST)"))
                elif ref not in _KNOWN_EXTERNAL:
                    # FK references unknown type — flag for validation
                    rels.append(self._make_rel(
                        src, ref, "references",
                        fk["name"], Confidence.MEDIUM.value, file,
                        fk.get("line_number"),
                        f"FK {fk['name']} references unresolved type {ref}"))
        return rels

    # ------------------------------------------------------------------
    # Pass 5: Owned value objects
    # ------------------------------------------------------------------

    def _from_owned_vos(self, entities_data: dict,
                        vo_names: set[str]) -> list[dict]:
        rels = []
        for ent in entities_data.get("entities", []):
            src  = ent["entity"]
            file = ent.get("source_file", "")
            for nav in ent.get("navigation_scalar", []):
                target = nav.get("target_entity", "")
                if target in vo_names:
                    rels.append(self._make_rel(
                        src, target, "embeds_value_object",
                        nav["name"], Confidence.HIGH.value, file,
                        nav.get("line_number"),
                        f"Owns value object {target} via {nav['name']} (AST)"))
        return rels

    # ------------------------------------------------------------------
    # Pass 6: Inferred scalar nav (no FK)
    # ------------------------------------------------------------------

    def _from_scalar_nav(self, entities_data: dict, all_classes: set[str],
                         existing: list[dict]) -> list[dict]:
        # Build set of already-covered (source, target) pairs
        covered = {(r["source"], r["target"]) for r in existing}
        rels = []
        for ent in entities_data.get("entities", []):
            src  = ent["entity"]
            file = ent.get("source_file", "")
            for nav in ent.get("navigation_scalar", []):
                target = nav.get("target_entity", "")
                if not target or target not in all_classes:
                    continue
                if (src, target) in covered:
                    continue
                rels.append(self._make_rel(
                    src, target, "references",
                    nav["name"], Confidence.MEDIUM.value, file,
                    nav.get("line_number"),
                    f"Scalar nav {nav['name']} without FK (inferred)"))
                covered.add((src, target))
        return rels

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Entity nav property map: populated lazily from entities_data
    _nav_type_cache: dict[tuple[str, str], str] = {}
    _entities_data_ref: Optional[dict] = None

    def _resolve_nav_prop_type(self, entity: str, prop_name: str) -> Optional[str]:
        """Look up the declared CLR type of a nav property by name."""
        # Try nav_type_cache first
        key = (entity, prop_name)
        if key in self._nav_type_cache:
            return self._nav_type_cache[key]
        return None

    def _build_nav_cache(self, entities_data: dict) -> None:
        """Populate nav type cache from extracted entities."""
        for ent in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            name = ent["entity"]
            for nav in ent.get("navigation_scalar", []) + ent.get("navigation_collection", []):
                self._nav_type_cache[(name, nav["name"])] = nav["target_entity"]

    def detect(self, entities_data: dict[str, Any], type_resolver: Any = None) -> dict[str, Any]:
        self._build_nav_cache(entities_data)
        return self.__class__._detect_impl(self, entities_data, type_resolver)

    @staticmethod
    def _make_rel(source: str, target: str, rel_type: str, via: str,
                  confidence: str, source_file: str,
                  line_number: Any, evidence: str) -> dict:
        return {
            "source":      source,
            "target":      target,
            "relationship": rel_type,
            "via":         via or "",
            "confidence":  confidence,
            "source_file": source_file or "",
            "line_number": line_number,
            "evidence":    evidence,
            "cross_domain": False,
        }

    def _deduplicate(self, rels: list[dict]) -> list[dict]:
        """Keep highest-confidence relationship for each (source, target, type) tuple."""
        conf_order = {Confidence.HIGH.value: 3, Confidence.MEDIUM.value: 2, Confidence.LOW.value: 1}
        best: dict[tuple, dict] = {}
        for r in rels:
            key = (r["source"], r["target"], r["relationship"])
            existing = best.get(key)
            if not existing or (conf_order.get(r["confidence"], 0) >
                                conf_order.get(existing["confidence"], 0)):
                best[key] = r
        return list(best.values())

    # ------------------------------------------------------------------
    # Output writers
    # ------------------------------------------------------------------

    def _write_relationships(self, rels: list[dict]) -> None:
        result = {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "relationship_count": len(rels),
            "relationships":      rels,
        }
        (self.output_dir / "relationships.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

    def _write_erd_map(self, rels: list[dict], entity_names: set[str]) -> None:
        mermaid_lines = ["erDiagram"]
        for r in rels:
            src, tgt = r["source"], r["target"]
            sym = {
                "one_to_many":         "||--o{",
                "many_to_one":         "}o--||",
                "many_to_many":        "}o--o{",
                "embeds_value_object": "||--||",
                "owns_many":           "||--o{",
                "one_to_one":          "||--||",
                "references":          "..>",
            }.get(r["relationship"], "--")
            via  = r.get("via", "")[:30]
            mermaid_lines.append(f"  {src} {sym} {tgt} : \"{via}\"")

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_count": len(entity_names),
            "mermaid":      "\n".join(mermaid_lines),
            "relationships": rels,
        }
        (self.output_dir / "erd_map.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

    def _write_dependency_graph(self, rels: list[dict],
                                entity_names: set[str],
                                vo_names: set[str]) -> None:
        adjacency: dict[str, list[dict]] = defaultdict(list)
        for r in rels:
            adjacency[r["source"]].append({
                "target":      r["target"],
                "relationship": r["relationship"],
                "confidence":  r["confidence"],
                "cross_domain": r["cross_domain"],
            })

        # Domain clusters
        clusters: dict[str, list[str]] = defaultdict(list)
        for name in entity_names | vo_names:
            d = _domain_of(name) or "Unknown"
            clusters[d].append(name)

        cross_domain_edges = [r for r in rels if r.get("cross_domain")]

        result = {
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "node_count":        len(entity_names | vo_names),
            "edge_count":        len(rels),
            "cross_domain_count": len(cross_domain_edges),
            "adjacency":         dict(adjacency),
            "domain_clusters":   dict(clusters),
            "cross_domain_edges": cross_domain_edges,
        }
        (self.output_dir / "dependency_graph.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")

    def _write_ef_semantics(self, all_classes: set[str]) -> None:
        """
        Parse full EF Core property/table semantics from configuration files.
        Extracts: max_lengths, required constraints, cascade deletes, indexes,
        column types, default values, concurrency tokens, alternate keys.
        """
        if not self.infra_dir or not self.infra_dir.exists():
            return

        config_files = (list(self.infra_dir.rglob("*Configuration*.cs")) +
                        list(self.infra_dir.rglob("*Context*.cs")))

        entities_semantics: dict[str, dict] = {}

        for cfg_file in config_files:
            if "Migration" in cfg_file.name or "Snapshot" in cfg_file.name:
                continue
            try:
                text  = cfg_file.read_text(encoding="utf-8", errors="replace")
                src   = str(cfg_file)
                lines = text.splitlines()

                # Determine entity for this config
                entity = None
                for m in _ENTITY_CFG.finditer(text):
                    name = m.group(1) or m.group(2) or m.group(3)
                    if name and name in all_classes:
                        entity = name
                        break
                if not entity:
                    continue

                sem = entities_semantics.setdefault(entity, {
                    "entity":        entity,
                    "config_file":   src,
                    "properties":    {},
                    "indexes":       [],
                    "cascade_deletes": [],
                    "concurrency_tokens": [],
                    "alternate_keys": [],
                })

                # Walk line by line for property chains
                current_prop: Optional[str] = None
                for i, line in enumerate(lines, 1):
                    # Detect active property
                    pm = _PROP_CALL.search(line)
                    if pm:
                        current_prop = pm.group(1) or pm.group(2)
                        sem["properties"].setdefault(current_prop, {"source_file": src, "line": i})

                    p = sem["properties"].get(current_prop, {}) if current_prop else {}

                    # MaxLength
                    mm = _HAS_MAX_LEN.search(line)
                    if mm and current_prop:
                        p["max_length"] = int(mm.group(1))
                        p["constraint_line"] = i

                    # IsRequired
                    rm = _IS_REQUIRED.search(line)
                    if rm and current_prop:
                        val = rm.group(1)
                        p["required"] = (val != "false")
                        p["constraint_line"] = i

                    # ColumnType
                    ctm = _HAS_COL_TYPE.search(line)
                    if ctm and current_prop:
                        p["column_type"] = ctm.group(1)

                    # DefaultValue
                    dvm = _HAS_DEFAULT.search(line)
                    if dvm and current_prop:
                        p["default_value"] = dvm.group(1).strip()

                    # Concurrency token
                    if _CONCURRENCY.search(line) and current_prop:
                        sem["concurrency_tokens"].append({
                            "property": current_prop, "source_file": src, "line": i})

                    # Index
                    im = _HAS_INDEX.search(line)
                    if im:
                        idx: dict = {"properties": im.group(1).strip(),
                                     "source_file": src, "line": i}
                        if _IS_UNIQUE.search(line) or (i < len(lines) and _IS_UNIQUE.search(lines[i])):
                            idx["unique"] = True
                        sem["indexes"].append(idx)

                    # Cascade delete
                    odm = _ON_DELETE.search(line)
                    if odm:
                        sem["cascade_deletes"].append({
                            "behavior": odm.group(1), "source_file": src, "line": i})

                    # Alternate key
                    if _HAS_ALT_KEY.search(line):
                        sem["alternate_keys"].append({"source_file": src, "line": i})

            except Exception as exc:
                print(f"  [WARN] EF semantics scan {cfg_file.name}: {exc}")

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_count": len(entities_semantics),
            "entities":     list(entities_semantics.values()),
        }
        out = self.output_dir / "ef_semantics.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[RelationshipDetector] EF semantics -> {out}")


# ---------------------------------------------------------------------------
# Monkeypatch: replace detect with _detect_impl for clean dispatch
# ---------------------------------------------------------------------------

_orig_detect = RelationshipDetector.detect

def _patched_detect(self, entities_data, type_resolver=None):
    self._build_nav_cache(entities_data)
    all_entity_names = {e["entity"] for e in entities_data.get("entities", [])}
    all_vo_names     = {v["entity"] for v in entities_data.get("value_objects", [])}
    all_classes      = all_entity_names | all_vo_names

    relationships: list[dict] = []

    ef_rels = self._scan_ef_config(all_classes)
    relationships.extend(ef_rels)
    print(f"  [Pass1-EFConfig] {len(ef_rels)} from EF IEntityTypeConfiguration")

    coll_rels = self._from_collection_nav(entities_data, all_classes)
    relationships.extend(coll_rels)
    print(f"  [Pass2-CollNav]  {len(coll_rels)} from collection navigation")

    fk_rels = self._from_fk_pairs(entities_data, all_classes)
    relationships.extend(fk_rels)
    print(f"  [Pass3-FKPairs]  {len(fk_rels)} from FK pairs")

    vo_rels = self._from_owned_vos(entities_data, all_vo_names)
    relationships.extend(vo_rels)
    print(f"  [Pass4-OwnedVO]  {len(vo_rels)} owned value objects")

    inferred = self._from_scalar_nav(entities_data, all_classes, relationships)
    relationships.extend(inferred)
    print(f"  [Pass5-Inferred] {len(inferred)} inferred scalar nav")

    relationships = self._deduplicate(relationships)

    for r in relationships:
        r["cross_domain"] = _is_cross_domain(r.get("source",""), r.get("target",""))

    self._write_relationships(relationships)
    self._write_erd_map(relationships, all_entity_names)
    self._write_dependency_graph(relationships, all_entity_names, all_vo_names)

    total = len(relationships)
    print(f"[RelationshipDetector] {total} total relationships written")

    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "relationship_count": total,
        "relationships":      relationships,
    }

RelationshipDetector.detect = _patched_detect
