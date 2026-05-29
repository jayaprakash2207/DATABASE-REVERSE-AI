"""
Enterprise Knowledge Graph — networkx-based semantic graph.

Nodes:
  entity        — domain entities
  value_object  — owned value types
  api_endpoint  — REST endpoints
  mediatr_handler — CQRS handlers
  page_handler  — Razor Page handlers
  repository    — IRepository<T> abstractions
  dto           — request/response models

Edges:
  OWNS          — entity → value_object (OwnsOne/OwnsMany)
  HAS_MANY      — entity → entity (one_to_many)
  BELONGS_TO    — entity → entity (many_to_one)
  REFERENCES    — entity → entity (cross-domain reference)
  SERVED_BY     — entity → api_endpoint
  HANDLED_BY    — entity → mediatr_handler
  QUERIES_VIA   — endpoint/handler → repository
  MAPS_TO       — dto → entity
  CALLS         — endpoint → mediatr_handler

Generates:
  memory/extracted/enterprise_graph.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx


# ---------------------------------------------------------------------------
# Node type constants
# ---------------------------------------------------------------------------

NT_ENTITY   = "entity"
NT_VO       = "value_object"
NT_ENDPOINT = "api_endpoint"
NT_HANDLER  = "mediatr_handler"
NT_PAGE     = "page_handler"
NT_REPO     = "repository"
NT_DTO      = "dto"


class EnterpriseKnowledgeGraph:
    def __init__(self, output_dir: str = "memory/extracted"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.G: nx.DiGraph = nx.DiGraph()

    # ------------------------------------------------------------------
    # Build from pipeline outputs
    # ------------------------------------------------------------------

    def build(
        self,
        entities_data:      dict[str, Any],
        relationships_data: dict[str, Any],
        apis_data:          dict[str, Any],
        governance_data:    dict[str, Any],
        lineage_data:       dict[str, Any],
    ) -> "EnterpriseKnowledgeGraph":

        self._add_entity_nodes(entities_data)
        self._add_relationship_edges(relationships_data)
        self._add_api_nodes(apis_data)
        self._add_lineage_edges(lineage_data, entities_data, apis_data)
        self._add_governance_annotations(governance_data)

        print(f"[KnowledgeGraph] {self.G.number_of_nodes()} nodes, "
              f"{self.G.number_of_edges()} edges")
        return self

    # ------------------------------------------------------------------
    # Node builders
    # ------------------------------------------------------------------

    def _add_entity_nodes(self, entities_data: dict) -> None:
        for ent in entities_data.get("entities", []):
            self.G.add_node(ent["entity"], node_type=NT_ENTITY,
                            aggregate=ent.get("aggregate", ""),
                            is_aggregate_root=ent.get("is_aggregate_root", False),
                            field_count=len(ent.get("fields", [])),
                            source_file=ent.get("source_file", ""),
                            line_number=ent.get("line_number"),
                            confidence=ent.get("confidence", {}).get("level", "MEDIUM"))
        for vo in entities_data.get("value_objects", []):
            self.G.add_node(vo["entity"], node_type=NT_VO,
                            owned_by=vo.get("owned_by", ""),
                            field_count=len(vo.get("fields", [])),
                            source_file=vo.get("source_file", ""),
                            confidence="HIGH")

    def _add_api_nodes(self, apis_data: dict) -> None:
        for ep in apis_data.get("endpoints", []):
            node_id = f"EP:{ep.get('method','GET')}:{ep.get('endpoint','/?')}"
            self.G.add_node(node_id, node_type=NT_ENDPOINT,
                            class_name=ep.get("class_name", ""),
                            method=ep.get("method", ""),
                            route=ep.get("endpoint", ""),
                            auth_required=ep.get("auth_required", False),
                            source_file=ep.get("source_file", ""),
                            confidence=ep.get("confidence", Confidence_HIGH))

            # Repo edges
            for repo in ep.get("repositories", []):
                entity = _entity_from_repo(repo)
                if entity:
                    repo_id = f"REPO:{entity}"
                    self.G.add_node(repo_id, node_type=NT_REPO, entity=entity)
                    self.G.add_edge(node_id, repo_id, edge_type="QUERIES_VIA",
                                    confidence="HIGH")
                    if entity in self.G:
                        self.G.add_edge(node_id, entity, edge_type="SERVED_BY",
                                        confidence="HIGH")

        for hdl in apis_data.get("mediatr_handlers", []):
            node_id = f"HDL:{hdl.get('class_name','?')}"
            self.G.add_node(node_id, node_type=NT_HANDLER,
                            class_name=hdl.get("class_name", ""),
                            request_type=hdl.get("request_type", ""),
                            response_type=hdl.get("response_type", ""),
                            source_file=hdl.get("source_file", ""),
                            confidence="HIGH")
            for repo in hdl.get("repositories", []):
                entity = _entity_from_repo(repo)
                if entity and entity in self.G:
                    self.G.add_edge(node_id, entity, edge_type="HANDLED_BY",
                                    confidence="HIGH")

        for ph in apis_data.get("page_handlers", []):
            node_id = f"PAGE:{ph.get('class_name','?')}:{ph.get('method','?')}"
            self.G.add_node(node_id, node_type=NT_PAGE,
                            class_name=ph.get("class_name", ""),
                            source_file=ph.get("source_file", ""),
                            confidence="HIGH")
            for entity in ph.get("entities_touched", []):
                if entity in self.G:
                    self.G.add_edge(node_id, entity, edge_type="SERVED_BY",
                                    confidence="HIGH")

        for dto in apis_data.get("dtos", []):
            node_id = f"DTO:{dto['name']}"
            self.G.add_node(node_id, node_type=NT_DTO,
                            fields=dto.get("fields", []),
                            source_file=dto.get("source_file", ""),
                            confidence="HIGH")

    def _add_relationship_edges(self, relationships_data: dict) -> None:
        edge_type_map = {
            "one_to_many":          "HAS_MANY",
            "many_to_one":          "BELONGS_TO",
            "many_to_many":         "HAS_MANY",
            "embeds_value_object":  "OWNS",
            "owns_many":            "OWNS",
            "one_to_one":           "BELONGS_TO",
            "references":           "REFERENCES",
        }
        for rel in relationships_data.get("relationships", []):
            src, tgt = rel["source"], rel["target"]
            if src not in self.G:
                self.G.add_node(src, node_type=NT_ENTITY)
            if tgt not in self.G:
                self.G.add_node(tgt, node_type=NT_ENTITY)
            edge_type = edge_type_map.get(rel["relationship"], "REFERENCES")
            self.G.add_edge(src, tgt,
                            edge_type=edge_type,
                            via=rel.get("via", ""),
                            confidence=rel.get("confidence", "MEDIUM"),
                            cross_domain=rel.get("cross_domain", False))

    def _add_lineage_edges(self, lineage_data: dict,
                           entities_data: dict, apis_data: dict) -> None:
        for flow in lineage_data.get("flows", []):
            if flow.get("has_gaps"):
                continue
            steps = flow.get("steps", [])
            for i in range(len(steps) - 1):
                a, b = steps[i], steps[i + 1]
                a_id = _step_node_id(a)
                b_id = _step_node_id(b)
                if a_id and b_id:
                    self.G.add_edge(a_id, b_id,
                                    edge_type="LINEAGE",
                                    flow=flow.get("flow_id", ""),
                                    confidence=flow.get("confidence", "MEDIUM"))

    def _add_governance_annotations(self, governance_data: dict) -> None:
        for finding in governance_data.get("findings", []):
            entity = finding.get("entity", "")
            if entity and entity in self.G:
                node = self.G.nodes[entity]
                node.setdefault("governance_flags", []).append({
                    "rule_type":   finding.get("rule_type", ""),
                    "field":       finding.get("field", ""),
                    "severity":    finding.get("severity", ""),
                    "confidence":  finding.get("confidence", "MEDIUM"),
                })

    # ------------------------------------------------------------------
    # Traversal / analysis
    # ------------------------------------------------------------------

    def get_entity_subgraph(self, entity: str, depth: int = 2) -> nx.DiGraph:
        """Return ego graph (all neighbors within N hops)."""
        nodes = nx.ego_graph(self.G, entity, radius=depth).nodes()
        return self.G.subgraph(nodes)

    def impact_analysis(self, entity: str) -> dict[str, Any]:
        """Return all nodes that depend on this entity."""
        predecessors = list(nx.ancestors(self.G, entity)) if entity in self.G else []
        successors   = list(nx.descendants(self.G, entity)) if entity in self.G else []
        return {
            "entity":       entity,
            "depends_on":   successors,
            "depended_by":  predecessors,
            "total_impact": len(predecessors) + len(successors),
        }

    def find_orphan_entities(self) -> list[str]:
        """Entities with no incoming or outgoing edges to other entities."""
        orphans = []
        for n, data in self.G.nodes(data=True):
            if data.get("node_type") not in (NT_ENTITY, NT_VO):
                continue
            neighbors = (list(self.G.predecessors(n)) +
                         list(self.G.successors(n)))
            if not any(self.G.nodes[nb].get("node_type") in (NT_ENTITY, NT_VO)
                       for nb in neighbors):
                orphans.append(n)
        return orphans

    def find_cycles(self) -> list[list[str]]:
        try:
            return list(nx.simple_cycles(self.G))
        except Exception:
            return []

    def domain_summary(self) -> dict[str, Any]:
        """Per-aggregate node/edge summary."""
        domains: dict[str, dict] = {}
        for n, data in self.G.nodes(data=True):
            agg = data.get("aggregate", "Unknown")
            if agg not in domains:
                domains[agg] = {"entities": [], "node_count": 0, "internal_edges": 0}
            if data.get("node_type") == NT_ENTITY:
                domains[agg]["entities"].append(n)
            domains[agg]["node_count"] += 1
        for src, tgt, data in self.G.edges(data=True):
            if not data.get("cross_domain", True):
                agg = self.G.nodes[src].get("aggregate", "Unknown")
                if agg in domains:
                    domains[agg]["internal_edges"] += 1
        return domains

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        nodes = []
        for n, data in self.G.nodes(data=True):
            nodes.append({"id": n, **data})

        edges = []
        for src, tgt, data in self.G.edges(data=True):
            edges.append({"source": src, "target": tgt, **data})

        orphans = self.find_orphan_entities()
        cycles  = self.find_cycles()

        result = {
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "node_count":     self.G.number_of_nodes(),
            "edge_count":     self.G.number_of_edges(),
            "orphan_entities": orphans,
            "cycles":         cycles,
            "domain_summary": self.domain_summary(),
            "nodes":          nodes,
            "edges":          edges,
        }

        out = self.output_dir / "enterprise_graph.json"
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"[KnowledgeGraph] Exported -> {out}  "
              f"({self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges)")

        self.export_impact_analysis()
        return result

    # ------------------------------------------------------------------
    # Impact analysis
    # ------------------------------------------------------------------

    def export_impact_analysis(self) -> dict[str, Any]:
        """
        For every entity/VO node, compute:
          - depends_on       (downstream nodes)
          - depended_by      (upstream nodes — who uses this)
          - dependency_depth (longest path from any source to this node)
          - bounded_context  (aggregate cluster)
          - shortest_paths   (to cross-domain neighbors)
          - is_hub           (high in/out degree — potential bottleneck)
        """
        entity_nodes = [
            n for n, d in self.G.nodes(data=True)
            if d.get("node_type") in (NT_ENTITY, NT_VO)
        ]

        analysis: list[dict] = []
        for node in entity_nodes:
            data = self.G.nodes[node]
            preds = list(nx.ancestors(self.G, node))   if node in self.G else []
            succs = list(nx.descendants(self.G, node)) if node in self.G else []

            # Dependency depth: longest path length from any leaf to this node
            depth = 0
            try:
                for pred in preds:
                    try:
                        p = nx.shortest_path_length(self.G, pred, node)
                        depth = max(depth, p)
                    except nx.NetworkXNoPath:
                        pass
            except Exception:
                pass

            # Cross-domain shortest paths
            cross_domain_paths: list[dict] = []
            node_agg = data.get("aggregate", "")
            for other in entity_nodes:
                if other == node:
                    continue
                other_agg = self.G.nodes[other].get("aggregate", "")
                if node_agg and other_agg and node_agg != other_agg:
                    try:
                        path = nx.shortest_path(self.G, node, other)
                        if len(path) <= 3:
                            cross_domain_paths.append({
                                "to":     other,
                                "length": len(path) - 1,
                                "path":   path,
                            })
                    except nx.NetworkXNoPath:
                        pass

            in_deg  = self.G.in_degree(node)  if node in self.G else 0
            out_deg = self.G.out_degree(node) if node in self.G else 0
            is_hub  = (in_deg + out_deg) >= 4

            analysis.append({
                "entity":              node,
                "aggregate":           node_agg,
                "node_type":           data.get("node_type", ""),
                "depends_on":          succs,
                "depended_by":         preds,
                "total_impact":        len(preds) + len(succs),
                "dependency_depth":    depth,
                "in_degree":           in_deg,
                "out_degree":          out_deg,
                "is_hub":              is_hub,
                "cross_domain_links":  cross_domain_paths,
                "governance_flags":    data.get("governance_flags", []),
            })

        # Sort by total_impact descending
        analysis.sort(key=lambda x: x["total_impact"], reverse=True)

        # Bounded context clusters
        agg_clusters: dict[str, list[str]] = {}
        for item in analysis:
            agg = item["aggregate"] or "Unknown"
            agg_clusters.setdefault(agg, []).append(item["entity"])

        result = {
            "generated_at":      datetime.now(timezone.utc).isoformat(),
            "entity_count":      len(analysis),
            "hub_entities":      [a["entity"] for a in analysis if a["is_hub"]],
            "orphan_entities":   self.find_orphan_entities(),
            "cycles":            self.find_cycles(),
            "bounded_contexts":  agg_clusters,
            "entities":          analysis,
        }

        out = self.output_dir / "impact_analysis.json"
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"[KnowledgeGraph] Impact analysis -> {out}")
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

Confidence_HIGH = "HIGH"


def _entity_from_repo(repo_str: str) -> str | None:
    import re
    m = re.search(r'<(\w+)>', repo_str)
    return m.group(1) if m else None


def _step_node_id(step: dict) -> str | None:
    layer = step.get("layer", "")
    comp  = step.get("component", "")
    if not comp:
        return None
    if layer == "entity":
        return comp
    if layer == "api":
        return f"EP:{comp}"
    if layer in ("mediatr_handler",):
        return f"HDL:{comp}"
    if layer == "repository":
        return f"REPO:{comp}"
    return None
