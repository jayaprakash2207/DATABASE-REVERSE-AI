"""
Semantic Type Resolver — maps property names/types to canonical CLR types.

Resolves:
  • Property name aliases  (ShipToAddress → Address)
  • Owned value object types
  • Navigation target disambiguation
  • Generic wrapper stripping (ICollection<T> → T)
  • Nullable stripping (Address? → Address)
  • Cross-aggregate identity references (BuyerId string → Buyer)

Generates:
  memory/extracted/type_resolution.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class TypeResolver:
    """
    Build a resolution map from the extracted entities data, then use it
    to resolve property types and navigation targets across the codebase.
    """

    def __init__(self, output_dir: str = "memory/extracted"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Maps canonical CLR name → entity dict
        self._entity_map:  dict[str, dict] = {}
        self._vo_map:      dict[str, dict] = {}

        # Maps (property_name, declaring_entity) → resolved_type
        self._name_alias:  dict[tuple[str, str], str] = {}

        # Known external / identity types (never flag as unresolved)
        self._externals: set[str] = {
            "Card", "AspNetUser", "IdentityUser", "ApplicationUser",
        }

        # Owned type map: property_type → owning_entity
        self._owned_types: dict[str, str] = {}

        # Generic wrapper pattern
        self._generic_re = re.compile(r'^(?:ICollection|IReadOnlyCollection|IList|'
                                      r'List|IEnumerable|IQueryable|HashSet|'
                                      r'ISet|IReadOnlyList|Collection)<(\w+)\??>$')

    # ------------------------------------------------------------------
    # Build from extracted data
    # ------------------------------------------------------------------

    def build(self, entities_data: dict[str, Any]) -> "TypeResolver":
        for ent in entities_data.get("entities", []):
            self._entity_map[ent["entity"]] = ent

        for vo in entities_data.get("value_objects", []):
            self._vo_map[vo["entity"]] = vo

        # Build name→type alias map from navigation scalar properties
        for ent in entities_data.get("entities", []):
            for nav in ent.get("navigation_scalar", []):
                prop_name   = nav["name"]
                target_type = nav["target_entity"]
                # e.g. ShipToAddress → Address
                self._name_alias[(prop_name, ent["entity"])] = target_type

        # Owned types: VOs owned by entities
        for vo in entities_data.get("value_objects", []):
            owner = vo.get("owned_by")
            if owner:
                self._owned_types[vo["entity"]] = owner if isinstance(owner, str) else owner[0]

        return self

    # ------------------------------------------------------------------
    # Resolution API
    # ------------------------------------------------------------------

    def resolve_type(self, raw_type: str, declaring_entity: str = "",
                     property_name: str = "") -> dict[str, Any]:
        """
        Resolve a raw C# type string to a canonical type descriptor.

        Returns:
          {
            "resolved":       canonical type name,
            "is_collection":  bool,
            "element_type":   T if collection,
            "is_entity":      bool,
            "is_value_object":bool,
            "is_external":    bool,
            "is_primitive":   bool,
            "owned_by":       str | None,
            "confidence":     "HIGH" | "MEDIUM" | "LOW",
            "evidence":       str,
          }
        """
        # Strip nullable suffix
        stripped = raw_type.rstrip("?")

        # Check collection wrapper
        m = self._generic_re.match(stripped)
        is_coll  = bool(m)
        elem     = m.group(1) if m else None
        base     = elem if is_coll else stripped

        # Remove generic part for lookup
        base_clean = base.split("<")[0].rstrip("?")

        # Alias resolution: property name → type
        if property_name and declaring_entity:
            alias_key = (property_name, declaring_entity)
            if alias_key in self._name_alias:
                base_clean = self._name_alias[alias_key]

        is_entity  = base_clean in self._entity_map
        is_vo      = base_clean in self._vo_map
        is_external= base_clean in self._externals
        is_primitive = base_clean in _PRIMITIVES

        confidence = "HIGH" if (is_entity or is_vo) else (
                     "MEDIUM" if is_external else
                     "LOW")
        evidence = (
            f"Entity registry match: {base_clean}"  if is_entity  else
            f"Value object match: {base_clean}"     if is_vo      else
            f"Known external: {base_clean}"         if is_external else
            f"Primitive type: {base_clean}"         if is_primitive else
            f"Unresolved type: {base_clean}"
        )

        return {
            "resolved":        base_clean,
            "raw":             raw_type,
            "is_collection":   is_coll,
            "element_type":    elem,
            "is_entity":       is_entity,
            "is_value_object": is_vo,
            "is_external":     is_external,
            "is_primitive":    is_primitive,
            "owned_by":        self._owned_types.get(base_clean),
            "confidence":      confidence,
            "evidence":        evidence,
        }

    def all_entity_names(self) -> set[str]:
        return set(self._entity_map.keys())

    def all_known_types(self) -> set[str]:
        return set(self._entity_map.keys()) | set(self._vo_map.keys()) | self._externals

    def is_known(self, type_name: str) -> bool:
        clean = type_name.rstrip("?").split("<")[0]
        return clean in self.all_known_types() or clean in _PRIMITIVES

    # ------------------------------------------------------------------
    # Generate type_resolution.json
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        aliases = [
            {"property": pn, "declaring_entity": de, "resolves_to": t}
            for (pn, de), t in sorted(self._name_alias.items())
        ]
        unresolved: list[dict] = []
        for ent in self._entity_map.values():
            for nav in ent.get("navigation_scalar", []):
                target = nav.get("target_entity", "")
                if target and not self.is_known(target):
                    unresolved.append({
                        "property":  nav["name"],
                        "in_entity": ent["entity"],
                        "raw_type":  nav["type"],
                        "target":    target,
                    })

        result = {
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "entity_count":   len(self._entity_map),
            "vo_count":       len(self._vo_map),
            "alias_count":    len(aliases),
            "unresolved_count": len(unresolved),
            "aliases":        aliases,
            "owned_types":    self._owned_types,
            "unresolved_navigations": unresolved,
        }
        out = self.output_dir / "type_resolution.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[TypeResolver] {len(aliases)} aliases, {len(unresolved)} unresolved "
              f"-> {out}")
        return result


# ---------------------------------------------------------------------------
# Primitive types set
# ---------------------------------------------------------------------------

_PRIMITIVES = {
    "int","long","short","byte","sbyte","uint","ulong","ushort",
    "float","double","decimal","bool","char","string","object","void",
    "DateTime","DateTimeOffset","DateOnly","TimeOnly","TimeSpan",
    "Guid","Uri","dynamic",
}
