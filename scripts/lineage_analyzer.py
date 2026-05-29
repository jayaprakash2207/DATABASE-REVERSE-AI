"""
Data Lineage Analyzer — full call-chain tracing with confidence scoring.

Traces:
  HTTP Client → API Endpoint → DTO → Repository → DbContext → Entity → DB Table

Stages:
  1. API stage   — endpoint + request/response models
  2. DTO stage   — request model field mapping
  3. Service stage  — injected services called by endpoint
  4. Repository stage — IRepository<T> calls
  5. Entity stage   — entity + owned value objects
  6. DB stage    — EF Core table + column names

Generates:
  memory/m3/lineage_analysis.json
  memory/m3/lineage-report.md
"""

from __future__ import annotations
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence


class LineageAnalyzer:
    def __init__(self, output_dir: str = "memory/m3"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        entities_data: dict[str, Any],
        apis_data:     dict[str, Any],
        rels_data:     dict[str, Any],
        ast_data:      dict[str, Any],
    ) -> dict[str, Any]:

        entity_map   = {e["entity"]: e for e in entities_data.get("entities", [])}
        vo_map       = {v["entity"]: v for v in entities_data.get("value_objects", [])}
        handler_map  = {h["request_type"]: h for h in apis_data.get("mediatr_handlers", [])
                        if h.get("request_type")}

        flows: list[dict] = []
        covered_entities: set[str] = set()

        # --- Flows from direct API endpoints ---
        for ep in apis_data.get("endpoints", []):
            flow = self._trace_endpoint(ep, entity_map, vo_map, handler_map, apis_data)
            flows.append(flow)
            for step in flow["steps"]:
                if step["layer"] == "entity":
                    covered_entities.add(step["component"])

        # --- Flows from MediatR handlers (Web layer) ---
        for hdl in apis_data.get("mediatr_handlers", []):
            flow = self._trace_handler(hdl, entity_map, vo_map)
            flows.append(flow)
            for step in flow["steps"]:
                if step["layer"] == "entity":
                    covered_entities.add(step["component"])

        # --- Gap flows: entities with no lineage ---
        for ent_name, ent in entity_map.items():
            if ent_name not in covered_entities:
                flows.append(self._gap_flow(ent_name, ent))

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "flow_count":   len(flows),
            "complete_flows": sum(1 for f in flows if not f.get("has_gaps")),
            "gap_flows":     sum(1 for f in flows if f.get("has_gaps")),
            "flows":         flows,
        }

        json_path = self.output_dir / "lineage_analysis.json"
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

        report = self._render_report(flows)
        md_path = self.output_dir / "lineage-report.md"
        md_path.write_text(report, encoding="utf-8")

        self._write_call_graph(flows, apis_data, entities_data)

        print(f"[LineageAnalyzer] {len(flows)} flows ({result['complete_flows']} complete, "
              f"{result['gap_flows']} gaps) -> {md_path}")
        return result

    # ------------------------------------------------------------------
    # Call graph generation
    # ------------------------------------------------------------------

    def _write_call_graph(self, flows: list[dict],
                          apis_data: dict, entities_data: dict) -> None:
        """
        Build a method-level call graph from lineage flows.
        Nodes: API endpoints, handlers, repositories, entities.
        Edges: directed calls with evidence.
        """
        nodes: dict[str, dict] = {}
        edges: list[dict]      = []

        def _node_id(layer: str, component: str) -> str:
            return f"{layer.upper()}:{component}"

        def _ensure_node(layer: str, component: str,
                         source_file: str = "", line: int = None) -> str:
            nid = _node_id(layer, component)
            if nid not in nodes:
                nodes[nid] = {
                    "id":          nid,
                    "layer":       layer,
                    "component":   component,
                    "source_file": source_file,
                    "line_number": line,
                }
            return nid

        for flow in flows:
            steps = flow.get("steps", [])
            for i in range(len(steps) - 1):
                a, b = steps[i], steps[i + 1]
                a_id = _ensure_node(a["layer"], a.get("component","?"),
                                    a.get("source_file",""), a.get("line_number"))
                b_id = _ensure_node(b["layer"], b.get("component","?"),
                                    b.get("source_file",""), b.get("line_number"))
                edges.append({
                    "from":       a_id,
                    "to":         b_id,
                    "flow_id":    flow.get("flow_id",""),
                    "confidence": min(a.get("confidence","MEDIUM"),
                                      b.get("confidence","MEDIUM")),
                    "evidence":   f"Lineage flow: {flow.get('flow','')}",
                })

        # Dependency injection map: constructor parameter → injected service
        di_graph: list[dict] = []
        for ep in apis_data.get("endpoints", []):
            for svc in ep.get("services", []):
                di_graph.append({
                    "consumer":    ep.get("class_name", "?"),
                    "dependency":  svc,
                    "type":        "constructor_injection",
                    "source_file": ep.get("source_file", ""),
                    "confidence":  "MEDIUM",
                })
            for repo in ep.get("repositories", []):
                di_graph.append({
                    "consumer":    ep.get("class_name", "?"),
                    "dependency":  repo,
                    "type":        "repository_injection",
                    "source_file": ep.get("source_file", ""),
                    "confidence":  "HIGH",
                })
        for hdl in apis_data.get("mediatr_handlers", []):
            for repo in hdl.get("repositories", []):
                di_graph.append({
                    "consumer":    hdl.get("class_name", "?"),
                    "dependency":  repo,
                    "type":        "repository_injection",
                    "source_file": hdl.get("source_file", ""),
                    "confidence":  "HIGH",
                })

        result = {
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "node_count":     len(nodes),
            "edge_count":     len(edges),
            "di_edge_count":  len(di_graph),
            "nodes":          list(nodes.values()),
            "edges":          edges,
            "di_graph":       di_graph,
        }

        out_path = self.output_dir.parent / "extracted" / "call_graph.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[LineageAnalyzer] Call graph -> {out_path}")

    # ------------------------------------------------------------------
    # Endpoint flow tracer
    # ------------------------------------------------------------------

    def _trace_endpoint(self, ep: dict, entity_map: dict, vo_map: dict,
                         handler_map: dict, apis_data: dict) -> dict:
        steps: list[dict] = []
        method  = ep.get("method", "?")
        route   = ep.get("endpoint", "?")
        src     = ep.get("source_file", "")

        # Stage 1 — API
        steps.append({
            "layer":       "api",
            "component":   f"{method} {route}",
            "class":       ep.get("class_name"),
            "source_file": src,
            "line_number": ep.get("line_number"),
            "confidence":  Confidence.HIGH.value,
        })

        # Stage 2 — DTO / request model
        req_model = ep.get("request_model")
        if req_model:
            steps.append({
                "layer":       "dto",
                "component":   req_model,
                "direction":   "inbound",
                "fields":      [f["name"] for f in ep.get("request_fields", [])],
                "source_file": src,
                "confidence":  Confidence.HIGH.value,
            })

        # Stage 3 — Services
        for svc in ep.get("services", []):
            steps.append({
                "layer":       "service",
                "component":   svc,
                "source_file": src,
                "confidence":  Confidence.MEDIUM.value,
            })

        # Stage 4 — Repositories
        for repo in ep.get("repositories", []):
            entity_name = self._entity_from_repo(repo)
            steps.append({
                "layer":       "repository",
                "component":   repo,
                "entity":      entity_name,
                "source_file": src,
                "confidence":  Confidence.HIGH.value,
            })

            # Stage 5 — Entity
            if entity_name and entity_name in entity_map:
                ent = entity_map[entity_name]
                steps.append({
                    "layer":       "entity",
                    "component":   entity_name,
                    "source_file": ent.get("source_file", ""),
                    "line_number": ent.get("line_number"),
                    "fields":      [f["name"] for f in ent.get("fields", [])],
                    "confidence":  Confidence.HIGH.value,
                })

                # Stage 5b — Owned value objects
                for nav in ent.get("navigation_scalar", []):
                    target = nav["target_entity"]
                    if target in vo_map:
                        vo = vo_map[target]
                        steps.append({
                            "layer":       "value_object",
                            "component":   target,
                            "owned_by":    entity_name,
                            "via":         nav["name"],
                            "source_file": vo.get("source_file", ""),
                            "confidence":  Confidence.HIGH.value,
                        })

        # Stage 6 — Response DTO
        resp_model = ep.get("response_model")
        if resp_model:
            steps.append({
                "layer":     "dto",
                "component":  resp_model,
                "direction":  "outbound",
                "fields":     [f["name"] for f in ep.get("response_fields", [])],
                "source_file": src,
                "confidence": Confidence.HIGH.value,
            })

        # Integration boundary detection
        boundaries = self._detect_boundaries(ep, steps)

        has_gaps = not any(s["layer"] == "entity" for s in steps)
        confs    = [s["confidence"] for s in steps]
        overall  = Confidence.HIGH.value if all(c == Confidence.HIGH.value for c in confs) \
                   else (Confidence.MEDIUM.value if Confidence.LOW.value not in confs
                         else Confidence.LOW.value)

        return {
            "flow_id":       f"{method}_{route}".replace("/", "_").replace("{", "").replace("}", ""),
            "flow":          f"{method} {route}",
            "type":          "rest_api",
            "auth_required": ep.get("auth_required", False),
            "steps":         steps,
            "boundaries":    boundaries,
            "has_gaps":      has_gaps,
            "confidence":    overall,
            "source_file":   src,
        }

    # ------------------------------------------------------------------
    # MediatR handler tracer
    # ------------------------------------------------------------------

    def _trace_handler(self, hdl: dict, entity_map: dict, vo_map: dict) -> dict:
        steps: list[dict] = []
        req_type = hdl.get("request_type", "Unknown")
        src      = hdl.get("source_file", "")

        steps.append({
            "layer":       "mediatr_handler",
            "component":   hdl.get("class_name", "?"),
            "request_type": req_type,
            "source_file": src,
            "line_number": hdl.get("line_number"),
            "confidence":  Confidence.HIGH.value,
        })

        for repo in hdl.get("repositories", []):
            ent_name = self._entity_from_repo(repo)
            steps.append({
                "layer":      "repository",
                "component":  repo,
                "entity":     ent_name,
                "source_file": src,
                "confidence": Confidence.HIGH.value,
            })
            if ent_name and ent_name in entity_map:
                ent = entity_map[ent_name]
                steps.append({
                    "layer":       "entity",
                    "component":   ent_name,
                    "source_file": ent.get("source_file", ""),
                    "confidence":  Confidence.HIGH.value,
                })

        has_gaps = not any(s["layer"] == "entity" for s in steps)
        return {
            "flow_id":   f"handler_{hdl.get('class_name','')}",
            "flow":      f"MediatR: {req_type}",
            "type":      "mediatr_handler",
            "steps":     steps,
            "boundaries": [],
            "has_gaps":  has_gaps,
            "confidence": Confidence.HIGH.value if not has_gaps else Confidence.MEDIUM.value,
        }

    # ------------------------------------------------------------------
    # Gap flow (no API trace)
    # ------------------------------------------------------------------

    def _gap_flow(self, ent_name: str, ent: dict) -> dict:
        return {
            "flow_id":   f"gap_{ent_name}",
            "flow":      f"Entity: {ent_name} (no direct API trace)",
            "type":      "gap",
            "note":      "Entity exists in domain but has no traced API or MediatR endpoint. "
                         "Likely managed via Web Razor Pages or background service.",
            "steps": [{
                "layer":       "entity",
                "component":   ent_name,
                "source_file": ent.get("source_file", ""),
                "line_number": ent.get("line_number"),
                "confidence":  Confidence.HIGH.value,
            }],
            "boundaries": [],
            "has_gaps":  True,
            "confidence": Confidence.MEDIUM.value,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _entity_from_repo(self, repo_str: str) -> Optional[str]:
        m = re.search(r'<(\w+)>', repo_str)
        return m.group(1) if m else None

    def _detect_boundaries(self, ep: dict, steps: list[dict]) -> list[dict]:
        boundaries: list[dict] = []

        # Auth boundary
        if ep.get("auth_required"):
            boundaries.append({
                "type":        "authentication",
                "description": "JWT Bearer token required",
                "confidence":  Confidence.HIGH.value,
            })

        # Cross-domain: endpoint touches multiple entity namespaces
        entities = [s["component"] for s in steps if s["layer"] == "entity"]
        if len(set(entities)) > 1:
            boundaries.append({
                "type":        "cross_entity",
                "description": f"Endpoint touches multiple entities: {', '.join(set(entities))}",
                "confidence":  Confidence.MEDIUM.value,
            })

        # DTO transformation boundary
        has_req = any(s["layer"] == "dto" and s.get("direction") == "inbound"  for s in steps)
        has_ent = any(s["layer"] == "entity" for s in steps)
        if has_req and has_ent:
            boundaries.append({
                "type":        "dto_transformation",
                "description": "DTO → Entity mapping (via AutoMapper or manual)",
                "confidence":  Confidence.MEDIUM.value,
            })

        return boundaries

    # ------------------------------------------------------------------
    # Report rendering
    # ------------------------------------------------------------------

    def _render_report(self, flows: list[dict]) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        complete = [f for f in flows if not f.get("has_gaps")]
        gaps     = [f for f in flows if f.get("has_gaps") and f.get("type") != "gap"]
        no_trace = [f for f in flows if f.get("type") == "gap"]

        lines = [
            "# Data Lineage Report\n\n",
            f"_Generated: {ts} by M3 Data Architecture Agent_\n\n",
            "## Layer Architecture\n\n",
            "```\n",
            "HTTP Client\n",
            "     |\n",
            "     v\n",
            "[API Endpoint] → DTO (request model)\n",
            "     |\n",
            "     v\n",
            "[Service / MediatR Handler]\n",
            "     |\n",
            "     v\n",
            "[IRepository<T>] (Ardalis.Specification)\n",
            "     |\n",
            "     v\n",
            "[EF Core DbContext] → SQL Server\n",
            "     |\n",
            "     v\n",
            "[Domain Entity] + [Owned Value Objects]\n",
            "     |\n",
            "     v\n",
            "DTO (response model) → HTTP Client\n",
            "```\n\n",
            f"| Metric | Count |\n|--------|-------|\n",
            f"| Total flows | {len(flows)} |\n",
            f"| Complete traces | {len(complete)} |\n",
            f"| Partial traces (gaps) | {len(gaps)} |\n",
            f"| No API trace (entity-only) | {len(no_trace)} |\n\n",
        ]

        if complete:
            lines.append("---\n\n## Complete Traces\n\n")
            for flow in complete:
                lines.extend(self._render_flow(flow))

        if gaps:
            lines.append("---\n\n## Partial Traces (Gaps Present)\n\n")
            for flow in gaps:
                lines.extend(self._render_flow(flow))

        if no_trace:
            lines.append("---\n\n## Entities With No API Coverage\n\n")
            lines.append("These entities are managed via Web Razor Pages or background services "
                         "— no REST endpoint in PublicApi.\n\n")
            lines.append("| Entity | Source File | Note |\n|--------|------------|------|\n")
            for flow in no_trace:
                ent_step = next((s for s in flow["steps"] if s["layer"] == "entity"), {})
                src = Path(ent_step.get("source_file", "")).name
                lines.append(
                    f"| {ent_step.get('component','?')} | {src} | {flow.get('note','')} |\n"
                )

        return "".join(lines)

    def _render_flow(self, flow: dict) -> list[str]:
        lines = [f"\n### `{flow['flow']}`\n\n"]
        if flow.get("note"):
            lines.append(f"> _{flow['note']}_\n\n")

        conf_badge = {
            Confidence.HIGH.value:   "[HIGH]",
            Confidence.MEDIUM.value: "[MEDIUM]",
            Confidence.LOW.value:    "[LOW]",
        }

        for i, step in enumerate(flow["steps"]):
            indent = "    " * i
            badge  = conf_badge.get(step["confidence"], "")
            layer  = step["layer"].upper()
            comp   = step["component"]
            src    = Path(step.get("source_file") or "").name
            extra  = ""
            if step.get("fields"):
                extra = f" | fields: {', '.join(step['fields'][:5])}"
            lines.append(f"{indent}**[{layer}]** `{comp}` {badge}{extra}")
            if src:
                lines.append(f"  — _{src}_")
            lines.append("\n")
            if i < len(flow["steps"]) - 1:
                lines.append(f"{indent}  ↓\n")

        if flow.get("boundaries"):
            lines.append("\n**Integration Boundaries:**\n")
            for b in flow["boundaries"]:
                lines.append(f"- `{b['type']}`: {b['description']}\n")

        lines.append("\n")
        return lines
