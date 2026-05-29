"""
Redundancy Analyzer — semantic similarity + field overlap detection.

Detects:
  - Duplicate entities (same field set > 70% overlap)
  - Duplicate DTOs (structural clone of entity)
  - Repeated field patterns (same field in N entities)
  - Duplicate business logic (same guard clause in N entities)
  - Repeated validation patterns
  - Structural duplicates (identical base type + same field count)

All findings include confidence scoring and field overlap %.

Generates:
  memory/m3/redundancy_analysis.json
  memory/m3/redundancy-analysis.md
"""

from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence


class RedundancyAnalyzer:
    def __init__(self, output_dir: str = "memory/m3"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, entities_data: dict[str, Any],
                governance_data: dict[str, Any],
                ast_data: dict[str, Any]) -> dict[str, Any]:

        findings: list[dict] = []

        all_entities = entities_data.get("entities", [])
        all_vos      = entities_data.get("value_objects", [])
        all_items    = all_entities + all_vos

        findings.extend(self._detect_entity_clones(all_items))
        findings.extend(self._detect_repeated_fields(all_items))
        findings.extend(self._detect_cross_domain_fields(all_items))
        findings.extend(self._detect_structural_duplicates(all_items))
        findings.extend(self._detect_repeated_governance(governance_data))

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "finding_count": len(findings),
            "actionable":    [f for f in findings if f.get("actionable")],
            "informational": [f for f in findings if not f.get("actionable")],
            "findings":      findings,
        }

        (self.output_dir / "redundancy_analysis.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        (self.output_dir / "redundancy-analysis.md").write_text(
            self._render_report(findings), encoding="utf-8")

        print(f"[RedundancyAnalyzer] {len(findings)} findings ({len(result['actionable'])} actionable)")
        return result

    # ------------------------------------------------------------------
    # 1. Entity structural clones (field overlap > threshold)
    # ------------------------------------------------------------------

    def _detect_entity_clones(self, items: list[dict]) -> list[dict]:
        findings = []
        checked: set[tuple] = set()

        for i, a in enumerate(items):
            fields_a = {f["name"] for f in a.get("fields", []) if not f.get("is_navigation")}
            if len(fields_a) < 2:
                continue
            for b in items[i + 1:]:
                pair = tuple(sorted([a["entity"], b["entity"]]))
                if pair in checked:
                    continue
                checked.add(pair)
                fields_b = {f["name"] for f in b.get("fields", []) if not f.get("is_navigation")}
                if len(fields_b) < 2:
                    continue
                overlap = fields_a & fields_b
                union   = fields_a | fields_b
                pct     = len(overlap) / len(union) if union else 0

                if pct >= 0.70:
                    conf = Confidence.HIGH if pct >= 0.90 else Confidence.MEDIUM
                    findings.append({
                        "type":         "entity_clone",
                        "severity":     "WARNING" if pct >= 0.80 else "NOTE",
                        "entities":     [a["entity"], b["entity"]],
                        "overlap_pct":  round(pct * 100, 1),
                        "shared_fields": sorted(overlap),
                        "unique_a":     sorted(fields_a - fields_b),
                        "unique_b":     sorted(fields_b - fields_a),
                        "source_a":     a.get("source_file", ""),
                        "source_b":     b.get("source_file", ""),
                        "confidence":   conf.value,
                        "actionable":   pct >= 0.80,
                        "recommendation": (
                            "Consider extracting a shared base class or interface."
                            if pct >= 0.80 else
                            "Structural similarity; verify if these represent the same concept."
                        ),
                    })
        return findings

    # ------------------------------------------------------------------
    # 2. Repeated fields across multiple entities
    # ------------------------------------------------------------------

    def _detect_repeated_fields(self, items: list[dict]) -> list[dict]:
        field_owners: dict[str, list[str]] = defaultdict(list)
        field_types:  dict[str, set] = defaultdict(set)

        for item in items:
            for fld in item.get("fields", []):
                if fld.get("is_navigation") or fld.get("is_fk"):
                    continue
                field_owners[fld["name"]].append(item["entity"])
                field_types[fld["name"]].add(fld.get("type", "?"))

        findings = []
        for field_name, owners in field_owners.items():
            if len(owners) < 3:
                continue
            # Ignore Id and common timestamp fields (expected repetition)
            if field_name in ("Id", "CreatedAt", "UpdatedAt", "CreatedBy", "IsDeleted"):
                continue
            same_type = len(field_types[field_name]) == 1
            findings.append({
                "type":         "repeated_field",
                "severity":     "NOTE",
                "field_name":   field_name,
                "field_types":  list(field_types[field_name]),
                "appears_in":   owners,
                "occurrence_count": len(owners),
                "same_type":    same_type,
                "confidence":   Confidence.HIGH.value,
                "actionable":   len(owners) >= 4,
                "recommendation": (
                    f"Field '{field_name}' appears in {len(owners)} entities. "
                    "Consider an interface or shared base class."
                    if len(owners) >= 4 else
                    f"Field '{field_name}' appears in {len(owners)} entities."
                ),
            })
        return findings

    # ------------------------------------------------------------------
    # 3. Cross-domain repeated fields (same semantic concept)
    # ------------------------------------------------------------------

    def _detect_cross_domain_fields(self, items: list[dict]) -> list[dict]:
        """Detect semantically identical cross-domain references (e.g. BuyerId in Basket+Order)."""
        findings = []

        # Map (field_name, field_type) → entities that contain it
        cross: dict[tuple, list[str]] = defaultdict(list)
        for item in items:
            for fld in item.get("fields", []):
                if fld.get("is_fk"):
                    cross[(fld["name"], fld.get("type",""))].append(item["entity"])

        for (fname, ftype), owners in cross.items():
            if len(owners) < 2:
                continue
            # Check they're in different aggregates
            aggs = {item.get("aggregate") for item in items if item["entity"] in owners}
            if len(aggs) < 2:
                continue
            findings.append({
                "type":         "cross_domain_field",
                "severity":     "WARNING",
                "field_name":   fname,
                "field_type":   ftype,
                "appears_in":   owners,
                "aggregates":   [a for a in aggs if a],
                "confidence":   Confidence.HIGH.value,
                "actionable":   True,
                "recommendation": (
                    f"FK '{fname}' ({ftype}) appears in {len(owners)} entities across "
                    f"{len(aggs)} aggregates. Enforce a shared interface contract "
                    f"(e.g., IBuyerIdentified) to prevent silent divergence."
                ),
            })
        return findings

    # ------------------------------------------------------------------
    # 4. Structural duplicates (same base class + same field count)
    # ------------------------------------------------------------------

    def _detect_structural_duplicates(self, items: list[dict]) -> list[dict]:
        """Find entities with identical structure (same bases + same field names)."""
        findings = []
        profile: dict[str, list[str]] = defaultdict(list)

        for item in items:
            bases  = tuple(sorted(item.get("base_types", [])))
            fields = tuple(sorted(f["name"] for f in item.get("fields", [])
                                  if not f.get("is_navigation")))
            key = f"{bases}|{fields}"
            profile[key].append(item["entity"])

        for key, ents in profile.items():
            if len(ents) < 2:
                continue
            findings.append({
                "type":       "structural_duplicate",
                "severity":   "NOTE",
                "entities":   ents,
                "overlap_pct": 100.0,
                "confidence": Confidence.HIGH.value,
                "actionable": True,
                "recommendation": (
                    f"{len(ents)} entities have identical field structure: "
                    f"{', '.join(ents)}. Verify they are semantically distinct."
                ),
            })
        return findings

    # ------------------------------------------------------------------
    # 5. Repeated governance rules (same guard on many entities)
    # ------------------------------------------------------------------

    def _detect_repeated_governance(self, governance_data: dict) -> list[dict]:
        """Detect governance patterns that recur across many entities (good candidates for base class)."""
        rule_owners: dict[str, set] = defaultdict(set)
        for finding in governance_data.get("findings", []):
            if finding.get("detection_method") == "guard_clause":
                rule_owners[finding["rule_type"]].add(finding["entity"])

        findings = []
        for rule, entities in rule_owners.items():
            if len(entities) < 3:
                continue
            findings.append({
                "type":       "repeated_validation",
                "severity":   "NOTE",
                "rule_type":  rule,
                "appears_in": sorted(entities),
                "count":      len(entities),
                "confidence": Confidence.HIGH.value,
                "actionable": len(entities) >= 4,
                "recommendation": (
                    f"Guard rule '{rule}' is repeated in {len(entities)} entities. "
                    "Consider a shared base class or specification."
                ),
            })
        return findings

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _render_report(self, findings: list[dict]) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        actionable  = [f for f in findings if f.get("actionable")]
        informational = [f for f in findings if not f.get("actionable")]

        lines = [
            "# Redundancy Analysis\n\n",
            f"_Generated: {ts} by M3 Data Architecture Agent_\n\n",
            f"| Category | Count |\n|----------|-------|\n",
            f"| Actionable findings | {len(actionable)} |\n",
            f"| Informational | {len(informational)} |\n",
            f"| Total | {len(findings)} |\n\n",
        ]

        if actionable:
            lines.append("---\n\n## Actionable Findings\n\n")
            for f in actionable:
                lines.append(f"### {f['type'].replace('_',' ').title()} — "
                              f"`{'` / `'.join(f.get('entities', [f.get('field_name','?')]))}`\n\n")
                lines.append(f"**Severity:** {f['severity']}  "
                              f"**Confidence:** {f['confidence']}  ")
                if "overlap_pct" in f:
                    lines.append(f"**Field overlap:** {f['overlap_pct']}%")
                lines.append("\n\n")
                if f.get("shared_fields"):
                    lines.append(f"Shared fields: `{', '.join(f['shared_fields'])}`\n\n")
                lines.append(f"**Recommendation:** {f.get('recommendation','')}\n\n")

        if informational:
            lines.append("---\n\n## Informational Findings\n\n")
            lines.append("| Type | Subject | Detail | Confidence |\n")
            lines.append("|------|---------|--------|------------|\n")
            for f in informational:
                subject = ", ".join(f.get("entities") or [f.get("field_name","?")])
                detail  = f.get("recommendation","")[:80]
                lines.append(f"| {f['type']} | {subject} | {detail} | {f['confidence']} |\n")

        return "".join(lines)
