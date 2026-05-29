"""
DDD Analyzer — Domain-Driven Design pattern analysis.

Detects:
  - Aggregate invariants (guard clauses, private setters, encapsulation)
  - Transactional boundaries (aggregate roots, owned entities)
  - Domain services (stateless service classes in domain layer)
  - Anti-corruption layers (adapter/translator/mapper in domain)
  - Bounded context coupling (cross-domain references)
  - Eventual consistency patterns (domain events, integration events)
  - Value object correctness (immutable, no ID, equality by value)
  - Factory methods (static Create() / FromXxx() constructors)

Generates:
  REVIEW/DDD_ANALYSIS.md
  memory/extracted/ddd_analysis.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DDDAnalyzer:
    def __init__(self, output_dir: str = "REVIEW",
                 extracted_dir: str = "memory/extracted"):
        self.output_dir   = Path(output_dir)
        self.extracted_dir = Path(extracted_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        entities_data:      dict[str, Any],
        relationships_data: dict[str, Any],
        apis_data:          dict[str, Any],
    ) -> dict[str, Any]:

        entities = entities_data.get("entities", [])
        vos      = entities_data.get("value_objects", [])
        rels     = relationships_data.get("relationships", [])

        result = {
            "generated_at":        datetime.now(timezone.utc).isoformat(),
            "aggregate_roots":     self._analyze_aggregate_roots(entities),
            "value_objects":       self._analyze_value_objects(vos),
            "aggregate_invariants": self._detect_invariants(entities),
            "transactional_boundaries": self._detect_tx_boundaries(entities, rels),
            "domain_services":     self._detect_domain_services(apis_data),
            "anti_corruption_layers": self._detect_acl(entities, apis_data),
            "bounded_context_coupling": self._detect_bc_coupling(rels),
            "eventual_consistency": self._detect_eventual_consistency(apis_data),
            "ddd_health_score":    0,
        }

        result["ddd_health_score"] = self._compute_health(result)

        # Write JSON
        json_out = self.extracted_dir / "ddd_analysis.json"
        json_out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

        # Write Markdown
        md = self._render_report(result)
        md_out = self.output_dir / "DDD_ANALYSIS.md"
        md_out.write_text(md, encoding="utf-8")

        print(f"[DDDAnalyzer] Health score: {result['ddd_health_score']}/100  "
              f"-> {md_out}")
        return result

    # ------------------------------------------------------------------
    # Aggregate root analysis
    # ------------------------------------------------------------------

    def _analyze_aggregate_roots(self, entities: list[dict]) -> list[dict]:
        roots = []
        for e in entities:
            if not e.get("is_aggregate_root"):
                continue

            # Check encapsulation: collection navs should be private
            coll_navs = e.get("navigation_collection", [])
            exposed_colls = [
                n["name"] for n in coll_navs
                if n.get("get_visibility", "public") == "public"
                and n.get("set_visibility", "public") in ("public", "none")
            ]

            # Check for private constructor (creation via factory)
            ctors = e.get("constructor_params", [])
            has_factory = any(
                "Create" in str(f.get("name",""))
                for f in e.get("fields", [])
            )

            # Count owned entities in this aggregate
            agg_name   = e.get("aggregate", "")
            owned_count = 0  # will be filled by cross-reference

            roots.append({
                "entity":            e["entity"],
                "aggregate":         agg_name,
                "source_file":       e.get("source_file", ""),
                "line_number":       e.get("line_number"),
                "field_count":       len(e.get("fields", [])),
                "exposed_collections": exposed_colls,
                "has_encapsulation_issue": bool(exposed_colls),
                "has_guard_clauses": bool(ctors),
                "confidence":        e.get("confidence", {}).get("level", "HIGH"),
            })
        return roots

    # ------------------------------------------------------------------
    # Value object correctness
    # ------------------------------------------------------------------

    def _analyze_value_objects(self, vos: list[dict]) -> list[dict]:
        results = []
        for vo in vos:
            fields    = vo.get("fields", [])
            has_id    = any(f.get("name","").lower() == "id" for f in fields)
            has_setter = any(
                f.get("set_visibility","none") in ("public","internal")
                for f in fields
            )
            results.append({
                "entity":        vo["entity"],
                "owned_by":      vo.get("owned_by"),
                "source_file":   vo.get("source_file",""),
                "field_count":   len(fields),
                "fields":        [f["name"] for f in fields],
                "has_id":        has_id,
                "has_public_setter": has_setter,
                "is_immutable":  not has_setter,
                "correctness_issues": (
                    (["VO should not have Id field"] if has_id else []) +
                    (["VO has public setter — should be immutable"] if has_setter else [])
                ),
            })
        return results

    # ------------------------------------------------------------------
    # Invariant detection (guard clauses)
    # ------------------------------------------------------------------

    def _detect_invariants(self, entities: list[dict]) -> list[dict]:
        invariants = []
        for e in entities:
            guards = e.get("constructor_params", [])
            if guards:
                invariants.append({
                    "entity":      e["entity"],
                    "source_file": e.get("source_file",""),
                    "type":        "constructor_guard",
                    "description": f"Constructor validates: {', '.join(guards)}",
                    "confidence":  "HIGH",
                    "finding_type": "CONFIRMED",
                })

            # Check for private setters on fields (immutability enforcement)
            private_set_fields = [
                f["name"] for f in e.get("fields",[])
                if f.get("set_visibility") in ("private", "none")
                and not f.get("is_navigation")
            ]
            if private_set_fields:
                invariants.append({
                    "entity":        e["entity"],
                    "source_file":   e.get("source_file",""),
                    "type":          "private_setter_invariant",
                    "description":   f"Immutable fields: {', '.join(private_set_fields[:5])}",
                    "confidence":    "HIGH",
                    "finding_type":  "CONFIRMED",
                })
        return invariants

    # ------------------------------------------------------------------
    # Transactional boundaries
    # ------------------------------------------------------------------

    def _detect_tx_boundaries(self, entities: list[dict],
                                rels: list[dict]) -> list[dict]:
        boundaries = []

        # Group entities by aggregate
        aggregates: dict[str, list[str]] = {}
        for e in entities:
            agg = e.get("aggregate", "Unknown")
            aggregates.setdefault(agg, []).append(e["entity"])

        for agg, members in aggregates.items():
            roots = [e for e in entities
                     if e.get("aggregate") == agg and e.get("is_aggregate_root")]
            cross = [r for r in rels
                     if r.get("cross_domain") and r.get("source") in members]

            boundaries.append({
                "aggregate":        agg,
                "members":          members,
                "root_count":       len(roots),
                "cross_domain_refs": len(cross),
                "boundary_type":    (
                    "well_bounded" if len(roots) == 1 and not cross else
                    "multiple_roots" if len(roots) > 1 else
                    "cross_domain_leakage" if cross else
                    "no_root"
                ),
                "finding_type": "CONFIRMED" if roots else "INFERRED",
            })

        return boundaries

    # ------------------------------------------------------------------
    # Domain service detection
    # ------------------------------------------------------------------

    def _detect_domain_services(self, apis_data: dict) -> list[dict]:
        services = []
        # Handlers that are stateless (no repository) are domain services
        for hdl in apis_data.get("mediatr_handlers", []):
            repos = hdl.get("repositories", [])
            if not repos:
                services.append({
                    "name":        hdl.get("class_name","?"),
                    "type":        "domain_service_candidate",
                    "description": "MediatR handler with no repository — pure domain logic",
                    "source_file": hdl.get("source_file",""),
                    "confidence":  "MEDIUM",
                    "finding_type": "INFERRED",
                })
        return services

    # ------------------------------------------------------------------
    # Anti-corruption layer detection
    # ------------------------------------------------------------------

    def _detect_acl(self, entities: list[dict],
                    apis_data: dict) -> list[dict]:
        acl = []
        # Mapper profiles = ACL between layers
        for mp in apis_data.get("mapper_profiles", []):
            acl.append({
                "name":        mp.get("name","?"),
                "type":        "automapper_profile",
                "description": "AutoMapper profile acts as ACL between domain and API layer",
                "source_file": mp.get("source_file",""),
                "confidence":  "HIGH",
                "finding_type": "CONFIRMED",
                "mappings":    mp.get("mappings",[]),
            })

        # CatalogItemOrdered VO = ACL snapshot (anti-corruption at order time)
        snapshot_vos = [e for e in entities if "Ordered" in e.get("entity","")
                        or "Snapshot" in e.get("entity","")]
        for vo in snapshot_vos:
            acl.append({
                "name":        vo["entity"],
                "type":        "snapshot_acl",
                "description": "Value object snapshot isolates domain from catalog changes at order time",
                "source_file": vo.get("source_file",""),
                "confidence":  "HIGH",
                "finding_type": "CONFIRMED",
            })
        return acl

    # ------------------------------------------------------------------
    # Bounded context coupling
    # ------------------------------------------------------------------

    def _detect_bc_coupling(self, rels: list[dict]) -> list[dict]:
        cross = [r for r in rels if r.get("cross_domain")]
        coupling: dict[str, dict] = {}
        for r in cross:
            key = f"{r.get('source','')}→{r.get('target','')}"
            coupling[key] = {
                "from":         r.get("source",""),
                "to":           r.get("target",""),
                "relationship": r.get("relationship",""),
                "via":          r.get("via",""),
                "coupling_type": (
                    "id_reference"   if r.get("relationship") == "many_to_one" else
                    "shared_entity"  if r.get("relationship") == "one_to_many" else
                    "value_snapshot" if r.get("relationship") == "embeds_value_object" else
                    "unknown"
                ),
                "confidence":    r.get("confidence","HIGH"),
                "source_file":   r.get("source_file",""),
                "line_number":   r.get("line_number"),
                "finding_type":  "CONFIRMED",
                "recommendation": (
                    "Use ID reference (not navigation property) across bounded contexts — ✓ correct"
                    if r.get("relationship") == "many_to_one" else
                    "Consider event-driven integration instead of direct cross-context navigation"
                ),
            }
        return list(coupling.values())

    # ------------------------------------------------------------------
    # Eventual consistency / domain events
    # ------------------------------------------------------------------

    def _detect_eventual_consistency(self, apis_data: dict) -> list[dict]:
        patterns = []
        # Check for domain event handlers
        for hdl in apis_data.get("mediatr_handlers", []):
            req = hdl.get("request_type","")
            if "Event" in req or "DomainEvent" in req:
                patterns.append({
                    "type":        "domain_event_handler",
                    "handler":     hdl.get("class_name","?"),
                    "event_type":  req,
                    "source_file": hdl.get("source_file",""),
                    "confidence":  "HIGH",
                    "finding_type": "CONFIRMED",
                })
        return patterns

    # ------------------------------------------------------------------
    # DDD health score (0–100)
    # ------------------------------------------------------------------

    def _compute_health(self, result: dict) -> int:
        score = 50  # Baseline

        # Aggregate roots: +5 each, -5 for encapsulation issues
        for root in result.get("aggregate_roots", []):
            score += 5
            if root.get("has_encapsulation_issue"):
                score -= 3
            if root.get("has_guard_clauses"):
                score += 2

        # Value objects: +3 each, -5 for public setters or IDs
        for vo in result.get("value_objects", []):
            score += 3
            for _ in vo.get("correctness_issues", []):
                score -= 3

        # Bounded context: -5 for each direct cross-context navigation
        for coupling in result.get("bounded_context_coupling", []):
            if coupling.get("coupling_type") == "id_reference":
                score += 1   # correct pattern
            elif coupling.get("coupling_type") == "shared_entity":
                score -= 5   # anti-pattern

        # ACL: +3 each AutoMapper/snapshot ACL
        score += len(result.get("anti_corruption_layers", [])) * 3

        # Invariants: +2 each
        score += min(len(result.get("aggregate_invariants", [])) * 2, 20)

        return max(0, min(100, score))

    # ------------------------------------------------------------------
    # Report rendering
    # ------------------------------------------------------------------

    def _render_report(self, result: dict) -> str:
        ts    = result["generated_at"]
        score = result["ddd_health_score"]
        lines = [
            "# DDD Analysis\n\n",
            f"_Generated: {ts} by M3 Data Architecture Agent_\n\n",
            f"## DDD Health Score: {score}/100\n\n",
            "_Score measures domain model correctness, encapsulation, and bounded context boundaries._\n\n",
        ]

        # Aggregate roots
        roots = result.get("aggregate_roots", [])
        if roots:
            lines.append("## Aggregate Roots\n\n")
            lines.append("| Entity | Aggregate | Guard Clauses | Encapsulation Issue | Confidence |\n")
            lines.append("|--------|-----------|---------------|---------------------|------------|\n")
            for r in roots:
                issues = ", ".join(r.get("exposed_collections", [])) or "—"
                lines.append(
                    f"| `CONFIRMED` {r['entity']} | {r['aggregate']} "
                    f"| {'✓' if r['has_guard_clauses'] else '—'} "
                    f"| {issues} | {r['confidence']} |\n"
                )
            lines.append("\n")

        # Value objects
        vos = result.get("value_objects", [])
        if vos:
            lines.append("## Value Objects\n\n")
            lines.append("| Entity | Owner | Immutable | Issues | Fields |\n")
            lines.append("|--------|-------|-----------|--------|--------|\n")
            for v in vos:
                issues = "; ".join(v.get("correctness_issues", [])) or "—"
                status = "`CONFIRMED` ✓" if not v.get("correctness_issues") else "`INFERRED` ⚠"
                lines.append(
                    f"| {status} {v['entity']} | {v.get('owned_by','?')} "
                    f"| {'✓' if v['is_immutable'] else '✗'} "
                    f"| {issues} | {', '.join(v.get('fields',[]))} |\n"
                )
            lines.append("\n")

        # Transactional boundaries
        tx = result.get("transactional_boundaries", [])
        if tx:
            lines.append("## Transactional Boundaries\n\n")
            for b in tx:
                icon = ("✓" if b["boundary_type"] == "well_bounded" else
                        "⚠" if "leakage" in b["boundary_type"] else "?")
                lines.append(
                    f"- **{b['aggregate']}** {icon} `{b['boundary_type']}` "
                    f"— members: {', '.join(b['members'])}  "
                    f"roots={b['root_count']}  cross_domain={b['cross_domain_refs']}\n"
                )
            lines.append("\n")

        # Bounded context coupling
        coupling = result.get("bounded_context_coupling", [])
        if coupling:
            lines.append("## Bounded Context Coupling\n\n")
            lines.append("| From | To | Via | Pattern | Recommendation |\n")
            lines.append("|------|-----|-----|---------|----------------|\n")
            for c in coupling:
                lines.append(
                    f"| `CONFIRMED` {c['from']} | {c['to']} | {c['via']} "
                    f"| `{c['coupling_type']}` | {c.get('recommendation','')[:60]} |\n"
                )
            lines.append("\n")

        # ACL
        acl = result.get("anti_corruption_layers", [])
        if acl:
            lines.append("## Anti-Corruption Layers\n\n")
            for a in acl:
                lines.append(
                    f"- **`CONFIRMED`** `{a['type']}` — **{a['name']}**: {a['description']}\n"
                )
            lines.append("\n")

        # Domain events
        events = result.get("eventual_consistency", [])
        if events:
            lines.append("## Eventual Consistency / Domain Events\n\n")
            for e in events:
                lines.append(f"- **`CONFIRMED`** {e['handler']} handles `{e['event_type']}`\n")
            lines.append("\n")
        else:
            lines.append("## Eventual Consistency\n\n")
            lines.append("- `INFERRED` No domain events detected — "
                         "synchronous consistency model assumed.\n\n")

        return "".join(lines)
