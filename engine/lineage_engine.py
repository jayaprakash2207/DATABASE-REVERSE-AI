"""
Universal Lineage Engine

Traces data flow from API endpoints → handlers/services → repositories → entities.
Operates on the Universal Semantic Model — technology agnostic.

Produces:
  - Endpoint→Entity lineage chains
  - Impact analysis: which endpoints touch which entities
  - Reverse: which entities are exposed via which endpoints
  - Handler call graphs
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models.semantic_model import SemanticModel
from models.universal import (
    UniversalEndpoint, UniversalRepository, UniversalHandler,
    UniversalEntity,
)


class LineageEngine:
    """
    Traces data flow through the semantic model.
    Call trace() to build lineage chains.
    """

    def trace(self, model: SemanticModel) -> dict[str, Any]:
        """
        Build lineage maps for the given model.
        Returns a dict with keys: endpoint_lineage, entity_exposure,
        call_graph, impact_summary.
        """
        entity_index = {e.name: e for e in model.entities}
        repo_index   = {r.name: r for r in model.repositories}
        handler_idx  = {h.name: h for h in model.handlers}

        # Build repository → entity map
        repo_to_entity = {r.name: r.entity for r in model.repositories}

        # Build handler → entities map (direct + via repos)
        handler_entities: dict[str, set[str]] = {}
        for h in model.handlers:
            entities: set[str] = set(h.entities_touched)
            for repo_name in h.repositories:
                repo = repo_index.get(repo_name)
                if repo and repo.entity:
                    entities.add(repo.entity)
            handler_entities[h.name] = entities

        # Endpoint lineage
        endpoint_lineage: list[dict] = []
        for ep in model.endpoints:
            touched: set[str] = set(ep.entities_touched)

            # Via repositories directly referenced
            for repo_name in ep.repositories:
                repo = repo_index.get(repo_name)
                if repo and repo.entity:
                    touched.add(repo.entity)

            # Via services → handlers
            for svc_name in ep.services:
                hdl = handler_idx.get(svc_name)
                if hdl:
                    touched.update(handler_entities.get(hdl.name, set()))

            endpoint_lineage.append({
                "method":           ep.method,
                "path":             ep.path,
                "handler_class":    ep.handler_class,
                "handler_method":   ep.handler_method,
                "entities_touched": sorted(touched),
                "repositories":     ep.repositories,
                "services":         ep.services,
                "auth_required":    ep.auth_required,
                "source_file":      ep.source_file,
                "line_number":      ep.line_number,
                "confidence":       ep.confidence.value,
            })

        # Entity exposure (reverse index)
        entity_exposure: dict[str, dict] = {}
        for chain in endpoint_lineage:
            for entity_name in chain["entities_touched"]:
                if entity_name not in entity_exposure:
                    entity_exposure[entity_name] = {
                        "entity":       entity_name,
                        "endpoints":    [],
                        "write_exposed": False,
                        "read_exposed":  False,
                        "unauth_exposed": False,
                    }
                exp = entity_exposure[entity_name]
                exp["endpoints"].append(f"{chain['method']} {chain['path']}")
                if chain["method"] in ("POST", "PUT", "PATCH", "DELETE"):
                    exp["write_exposed"] = True
                else:
                    exp["read_exposed"] = True
                if not chain["auth_required"]:
                    exp["unauth_exposed"] = True

        # Call graph
        call_graph: dict[str, list[str]] = {}
        for ep in model.endpoints:
            node = f"ENDPOINT:{ep.method}:{ep.path}"
            targets: list[str] = []
            for repo_name in ep.repositories:
                targets.append(f"REPO:{repo_name}")
            for svc in ep.services:
                targets.append(f"HANDLER:{svc}")
            call_graph[node] = targets

        for h in model.handlers:
            node = f"HANDLER:{h.name}"
            targets = [f"REPO:{r}" for r in h.repositories]
            targets += [f"ENTITY:{e}" for e in h.entities_touched]
            call_graph[node] = targets

        for r in model.repositories:
            node = f"REPO:{r.name}"
            call_graph[node] = [f"ENTITY:{r.entity}"] if r.entity else []

        # Impact summary per entity
        impact_summary: list[dict] = []
        for entity in model.entities:
            name = entity.name
            exp  = entity_exposure.get(name, {})
            deps_on  = [r.target for r in model.relationships if r.source == name]
            deps_by  = [r.source for r in model.relationships if r.target == name]
            impact_summary.append({
                "entity":           name,
                "aggregate":        entity.aggregate,
                "endpoint_count":   len(exp.get("endpoints", [])),
                "write_exposed":    exp.get("write_exposed", False),
                "unauth_exposed":   exp.get("unauth_exposed", False),
                "depends_on":       sorted(set(deps_on)),
                "depended_by":      sorted(set(deps_by)),
                "is_hub":           (len(deps_on) + len(deps_by)) >= 4,
                "source_file":      entity.source_file,
            })

        return {
            "generated_at":     _now(),
            "endpoint_lineage": endpoint_lineage,
            "entity_exposure":  list(entity_exposure.values()),
            "call_graph":       call_graph,
            "impact_summary":   impact_summary,
        }

    def save(
        self,
        model: SemanticModel,
        output_dir: str | Path,
    ) -> dict[str, Any]:
        result = self.trace(model)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        (out / "lineage_analysis.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        (out / "call_graph.json").write_text(
            json.dumps(result["call_graph"], indent=2, default=str), encoding="utf-8"
        )
        (out / "impact_analysis.json").write_text(
            json.dumps(result["impact_summary"], indent=2, default=str), encoding="utf-8"
        )

        total_links = sum(len(v) for v in result["call_graph"].values())
        print(
            f"[LineageEngine] {len(result['endpoint_lineage'])} endpoint chains, "
            f"{len(result['entity_exposure'])} exposed entities, "
            f"{total_links} call graph edges"
        )
        return result


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
