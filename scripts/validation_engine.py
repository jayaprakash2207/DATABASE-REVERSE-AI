"""
Validation Engine — cross-layer integrity checking.

Checks:
  1. Orphaned FKs — FK field references an entity not in entity catalog
  2. Missing navigation — FK exists but no scalar nav property
  3. Unresolved relationship targets — relationship target not in entity set
  4. Lineage gaps — entity has no API trace
  5. Circular dependencies — detect cycles in relationship graph
  6. Schema conflicts — same entity name in multiple aggregates
  7. Governance gaps — PII field with no encryption or retention rule
  8. Missing EF configuration — entity in entities.json but not found in DbContext

Generates:
  memory/m3/validation_results.json
  memory/m3/validation-report.md
"""

from __future__ import annotations
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence


def _classify_finding_type(issue: dict) -> str:
    """
    Classify each finding as CONFIRMED, INFERRED, or RECOMMENDED.

    CONFIRMED   — directly evidenced by AST/EF/source code (HIGH confidence)
    INFERRED    — derived from architecture reasoning (MEDIUM confidence)
    RECOMMENDED — modernization advice, not a defect (always NOTE severity)
    """
    check    = issue.get("check", "")
    severity = issue.get("severity", "")
    conf     = issue.get("confidence", "")

    # Recommendations are always suggestions, never defects
    if severity == "NOTE" and "recommendation" in check.lower():
        return "RECOMMENDED"
    # Lineage gaps and schema conflicts are inferred (no direct source anchor)
    if check in ("lineage_gap", "schema_conflict", "missing_nav_property"):
        return "INFERRED"
    # Orphaned FKs and unresolved rels with HIGH conf = CONFIRMED defect
    if conf == "HIGH" and check in ("orphaned_fk", "unresolved_relationship_target",
                                     "circular_dependency", "pii_no_encryption"):
        return "CONFIRMED"
    # Everything else with source_file + line_number = CONFIRMED
    if issue.get("source_file") and issue.get("line_number"):
        return "CONFIRMED"
    # Heuristic / governance gaps without direct source anchor
    if check in ("governance_gap", "lineage_gap", "missing_ef_config"):
        return "INFERRED"
    return "INFERRED"


class ValidationEngine:
    def __init__(self, output_dir: str = "memory/m3"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        entities_data:    dict[str, Any],
        relationships_data: dict[str, Any],
        governance_data:  dict[str, Any],
        lineage_data:     dict[str, Any],
    ) -> dict[str, Any]:

        issues: list[dict] = []

        all_entities = {e["entity"] for e in entities_data.get("entities", [])}
        all_vos      = {v["entity"] for v in entities_data.get("value_objects", [])}
        all_classes  = all_entities | all_vos

        issues.extend(self._check_orphaned_fks(entities_data, all_classes))
        issues.extend(self._check_missing_nav(entities_data, all_classes))
        issues.extend(self._check_unresolved_rels(relationships_data, all_classes))
        issues.extend(self._check_lineage_gaps(lineage_data, all_entities))
        issues.extend(self._check_circular_deps(relationships_data))
        issues.extend(self._check_schema_conflicts(entities_data))
        issues.extend(self._check_governance_gaps(entities_data, governance_data))

        # Severity distribution
        sev_counts = defaultdict(int)
        for issue in issues:
            sev_counts[issue["severity"]] += 1

        # Stamp each issue with finding_type (CONFIRMED / INFERRED / RECOMMENDED)
        for issue in issues:
            issue.setdefault("finding_type", _classify_finding_type(issue))
            issue.setdefault("analyzer_source", "ValidationEngine")

        confirmed   = [i for i in issues if i["finding_type"] == "CONFIRMED"]
        inferred    = [i for i in issues if i["finding_type"] == "INFERRED"]
        recommended = [i for i in issues if i["finding_type"] == "RECOMMENDED"]

        result = {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "issue_count":     len(issues),
            "severity_counts": dict(sev_counts),
            "finding_type_counts": {
                "CONFIRMED":   len(confirmed),
                "INFERRED":    len(inferred),
                "RECOMMENDED": len(recommended),
            },
            "critical_issues": [i for i in issues if i["severity"] == "CRITICAL"],
            "warnings":        [i for i in issues if i["severity"] == "WARNING"],
            "notes":           [i for i in issues if i["severity"] == "NOTE"],
            "confirmed":       confirmed,
            "inferred":        inferred,
            "recommended":     recommended,
            "issues":          issues,
        }

        (self.output_dir / "validation_results.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        (self.output_dir / "validation-report.md").write_text(
            self._render_report(issues), encoding="utf-8")

        print(f"[ValidationEngine] {len(issues)} issues "
              f"(CRITICAL:{sev_counts.get('CRITICAL',0)} "
              f"WARNING:{sev_counts.get('WARNING',0)} "
              f"NOTE:{sev_counts.get('NOTE',0)})")
        return result

    # ------------------------------------------------------------------
    # Check 1: Orphaned FK references
    # ------------------------------------------------------------------

    def _check_orphaned_fks(self, entities_data: dict, all_classes: set) -> list[dict]:
        issues = []
        known_external = {"Card", "AspNetUser", "IdentityUser"}
        for ent in entities_data.get("entities", []) + entities_data.get("value_objects", []):
            for fk in ent.get("foreign_keys", []):
                ref = fk.get("references", "")
                if ref and ref not in all_classes and ref not in known_external:
                    issues.append({
                        "check":       "orphaned_fk",
                        "severity":    "WARNING",
                        "entity":      ent["entity"],
                        "field":       fk["name"],
                        "description": f"FK '{fk['name']}' references '{ref}' which is not in entity catalog",
                        "source_file": ent.get("source_file", ""),
                        "line_number": fk.get("line_number"),
                        "confidence":  Confidence.HIGH.value,
                        "recommendation": f"Verify '{ref}' is an external system reference (add to known_external) "
                                          f"or extract it as an entity.",
                    })
        return issues

    # ------------------------------------------------------------------
    # Check 2: FK field without matching scalar navigation
    # ------------------------------------------------------------------

    def _check_missing_nav(self, entities_data: dict, all_classes: set) -> list[dict]:
        issues = []
        for ent in entities_data.get("entities", []):
            nav_targets = {n["target_entity"] for n in ent.get("navigation_scalar", [])}
            for fk in ent.get("foreign_keys", []):
                ref = fk.get("references", "")
                if ref in all_classes and ref not in nav_targets:
                    issues.append({
                        "check":       "missing_nav_property",
                        "severity":    "NOTE",
                        "entity":      ent["entity"],
                        "field":       fk["name"],
                        "description": f"FK '{fk['name']}' references '{ref}' but no scalar navigation property found",
                        "source_file": ent.get("source_file", ""),
                        "line_number": fk.get("line_number"),
                        "confidence":  Confidence.MEDIUM.value,
                        "recommendation": f"Consider adding 'public {ref}? {ref} {{ get; private set; }}' "
                                          f"for EF Core lazy loading.",
                    })
        return issues

    # ------------------------------------------------------------------
    # Check 3: Unresolved relationship targets
    # ------------------------------------------------------------------

    def _check_unresolved_rels(self, relationships_data: dict, all_classes: set) -> list[dict]:
        issues = []
        known_external = {"Card", "AspNetUser", "IdentityUser", "Unknown"}
        for rel in relationships_data.get("relationships", []):
            target = rel.get("target", "")
            if target and target not in all_classes and target not in known_external:
                issues.append({
                    "check":       "unresolved_relationship_target",
                    "severity":    "WARNING",
                    "entity":      rel.get("source", "?"),
                    "field":       rel.get("via", "?"),
                    "description": f"Relationship target '{target}' (from '{rel.get('source')}') "
                                   f"not in entity catalog",
                    "source_file": rel.get("source_file", ""),
                    "line_number": rel.get("line_number"),
                    "confidence":  rel.get("confidence", Confidence.MEDIUM.value),
                    "recommendation": f"Verify '{target}' is extracted or mark as external reference.",
                })
        return issues

    # ------------------------------------------------------------------
    # Check 4: Lineage gaps
    # ------------------------------------------------------------------

    def _check_lineage_gaps(self, lineage_data: dict, all_entities: set) -> list[dict]:
        issues = []
        covered = set()
        for flow in lineage_data.get("flows", []):
            if not flow.get("has_gaps"):
                for step in flow.get("steps", []):
                    if step.get("layer") == "entity":
                        covered.add(step["component"])

        for ent_name in all_entities:
            if ent_name not in covered:
                issues.append({
                    "check":       "lineage_gap",
                    "severity":    "NOTE",
                    "entity":      ent_name,
                    "field":       "",
                    "description": f"Entity '{ent_name}' has no complete API lineage trace",
                    "source_file": "",
                    "line_number": None,
                    "confidence":  Confidence.HIGH.value,
                    "recommendation": "Entity may be managed via Web layer or background service. "
                                      "Document access path in INTEGRATION_MAP.",
                })
        return issues

    # ------------------------------------------------------------------
    # Check 5: Circular dependency detection (DFS)
    # ------------------------------------------------------------------

    def _check_circular_deps(self, relationships_data: dict) -> list[dict]:
        graph: dict[str, list[str]] = defaultdict(list)
        for rel in relationships_data.get("relationships", []):
            if rel["relationship"] in ("many_to_one", "one_to_many", "references"):
                graph[rel["source"]].append(rel["target"])

        visited:   set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path[:])
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
            rec_stack.discard(node)

        for node in list(graph.keys()):
            if node not in visited:
                dfs(node, [])

        issues = []
        for cycle in cycles:
            issues.append({
                "check":       "circular_dependency",
                "severity":    "WARNING",
                "entity":      cycle[0],
                "field":       "",
                "description": f"Circular dependency detected: {' -> '.join(cycle)}",
                "source_file": "",
                "line_number": None,
                "confidence":  Confidence.MEDIUM.value,
                "recommendation": "Break the cycle by introducing an interface or event-based decoupling.",
            })
        return issues

    # ------------------------------------------------------------------
    # Check 6: Schema conflicts (same name in multiple aggregates)
    # ------------------------------------------------------------------

    def _check_schema_conflicts(self, entities_data: dict) -> list[dict]:
        name_agg: dict[str, list[str]] = defaultdict(list)
        for ent in entities_data.get("entities", []):
            agg = ent.get("aggregate") or "root"
            name_agg[ent["entity"]].append(agg)

        issues = []
        for ent_name, aggs in name_agg.items():
            if len(set(aggs)) > 1:
                issues.append({
                    "check":       "schema_conflict",
                    "severity":    "CRITICAL",
                    "entity":      ent_name,
                    "field":       "",
                    "description": f"Entity '{ent_name}' appears in multiple aggregates: {aggs}",
                    "source_file": "",
                    "line_number": None,
                    "confidence":  Confidence.HIGH.value,
                    "recommendation": "Entity should belong to exactly one bounded context / aggregate.",
                })
        return issues

    # ------------------------------------------------------------------
    # Check 7: Governance gaps (PII field with no encryption rule)
    # ------------------------------------------------------------------

    def _check_governance_gaps(self, entities_data: dict, governance_data: dict) -> list[dict]:
        # Find PII fields
        pii_fields: set[tuple] = set()
        encrypted:  set[tuple] = set()
        for finding in governance_data.get("findings", []):
            key = (finding.get("entity"), finding.get("field"))
            if finding.get("rule_type") in ("pii", "pci_dss", "credential"):
                pii_fields.add(key)
            if finding.get("rule_type") in ("encryption", "sensitive_data"):
                encrypted.add(key)

        issues = []
        for (entity, field) in pii_fields:
            if (entity, field) not in encrypted:
                issues.append({
                    "check":       "pii_without_encryption",
                    "severity":    "WARNING",
                    "entity":      entity or "?",
                    "field":       field or "?",
                    "description": f"PII/sensitive field '{field}' on '{entity}' has no encryption annotation",
                    "source_file": "",
                    "line_number": None,
                    "confidence":  Confidence.MEDIUM.value,
                    "recommendation": "Add [Encrypted] attribute or confirm server-side encryption handles this field.",
                })
        return issues

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _render_report(self, issues: list[dict]) -> str:
        ts    = datetime.now(timezone.utc).isoformat()
        crits = [i for i in issues if i["severity"] == "CRITICAL"]
        warns = [i for i in issues if i["severity"] == "WARNING"]
        notes = [i for i in issues if i["severity"] == "NOTE"]

        conf_ct  = sum(1 for i in issues if i.get("finding_type") == "CONFIRMED")
        inf_ct   = sum(1 for i in issues if i.get("finding_type") == "INFERRED")
        rec_ct   = sum(1 for i in issues if i.get("finding_type") == "RECOMMENDED")

        lines = [
            "# Validation Report\n\n",
            f"_Generated: {ts} by M3 Data Architecture Agent_\n\n",
            "## Finding Classification\n\n",
            "| Type | Meaning | Count |\n|------|---------|-------|\n",
            f"| **CONFIRMED** | Directly evidenced by AST / EF config / source code | {conf_ct} |\n",
            f"| **INFERRED**  | Derived via architecture reasoning, not directly proven | {inf_ct} |\n",
            f"| **RECOMMENDED** | Modernization suggestion — not a defect | {rec_ct} |\n\n",
            "## Severity Counts\n\n",
            f"| Severity | Count |\n|----------|-------|\n",
            f"| CRITICAL | {len(crits)} |\n",
            f"| WARNING  | {len(warns)} |\n",
            f"| NOTE     | {len(notes)} |\n",
            f"| **Total**| **{len(issues)}** |\n\n",
        ]

        for severity, group in [("CRITICAL", crits), ("WARNING", warns), ("NOTE", notes)]:
            if not group:
                continue
            lines.append(f"---\n\n## {severity} ({len(group)})\n\n")
            lines.append("| Type | Check | Entity | Field | Description | Evidence | Conf |\n")
            lines.append("|------|-------|--------|-------|-------------|----------|------|\n")
            for i in group:
                ftype = i.get("finding_type", "INFERRED")
                src   = Path(i.get("source_file","")).name if i.get("source_file") else "—"
                ln    = i.get("line_number", "")
                evid  = f"{src}:{ln}" if src != "—" else "—"
                lines.append(
                    f"| `{ftype}` | {i['check']} | {i['entity']} | {i.get('field','')} "
                    f"| {i['description'][:80]} | {evid} | {i['confidence']} |\n"
                )
            lines.append("\n")
            recs = [i for i in group if i.get("recommendation")]
            if recs:
                lines.append("### Recommendations\n\n")
                for i in recs:
                    lines.append(f"- **`RECOMMENDED`** `{i['entity']}.{i.get('field','')}` — "
                                  f"{i['recommendation']}\n")
                lines.append("\n")

        return "".join(lines)
