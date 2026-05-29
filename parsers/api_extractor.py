"""
API Extractor — AST + regex hybrid for complete endpoint/handler extraction.

Detects:
  • MinimalApi IEndpoint<IResult, TRequest, IRepository<T>> pattern
  • Ardalis EndpointBase pattern
  • ASP.NET Core Controllers (ControllerBase / ApiController)
  • MediatR IRequestHandler<TReq, TResp>
  • MediatR IRequest<T> / IRequest command/query objects
  • Razor Page handlers (PageModel.OnGet/OnPost)
  • DTOs (request/response records and classes)
  • AutoMapper profiles

Generates:
  memory/extracted/apis.json
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence

warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

HAS_TREESITTER = False
_TS_PARSER = None
try:
    from tree_sitter_languages import get_parser as _ts_get_parser
    _TS_PARSER = _ts_get_parser("c_sharp")
    HAS_TREESITTER = True
except Exception:
    pass

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Endpoint patterns
_RE_ENDPOINT_BASE = re.compile(
    r'IEndpoint\s*<\s*IResult\s*,\s*(\w+)\s*,?\s*(?:IRepository<(\w+)>|IReadRepository<(\w+)>)?\s*>|'
    r'EndpointBase(?:Async)?\.WithRequest<(\w+)>\.WithActionResult<(\w+)>|'
    r'EndpointBase(?:Async)?\.WithRequest<(\w+)>\.WithResponse<(\w+)>',
    re.DOTALL)

# Route patterns (MapGet/Post/Put/Delete/Patch)
_RE_MAP_ROUTE = re.compile(
    r'app\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*["\']([^"\']+)["\']', re.DOTALL)

# Auth attribute
_RE_AUTH = re.compile(r'\[Authorize')

# Controller base
_RE_CONTROLLER = re.compile(r':\s*(?:Controller|ControllerBase|ApiController)\b')
_RE_HTTP_METHOD = re.compile(r'\[(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)\s*(?:\([^)]*\))?\]')
_RE_ROUTE_ATTR  = re.compile(r'\[Route\s*\(\s*["\']([^"\']+)["\']')

# MediatR
_RE_MEDIATR_HANDLER = re.compile(
    r'IRequestHandler\s*<\s*(\w+)\s*(?:,\s*(\w+(?:<[^>]+>)?))?\s*>')
_RE_MEDIATR_REQUEST = re.compile(
    r':\s*IRequest\s*(?:<\s*(\w+(?:<[^>]+>)?)\s*>)?')

# Repository injection
_RE_REPO = re.compile(r'IRepository<(\w+)>|IReadRepository<(\w+)>')

# Service injection
_RE_SERVICE = re.compile(
    r'private\s+(?:readonly\s+)?I(\w+(?:Service|Manager|Provider|Factory))\b')

# DTO: record or class ending in Request/Response/Dto/ViewModel/Command/Query
_RE_DTO_CLASS = re.compile(
    r'(?:public|internal)\s+(?:(?:partial|sealed|abstract)\s+)*'
    r'(?:class|record)\s+(\w+(?:Request|Response|Dto|ViewModel|Command|Query|Result|Model))\b',
    re.IGNORECASE)

# AutoMapper profile
_RE_MAPPER = re.compile(r'CreateMap\s*<\s*(\w+)\s*,\s*(\w+)\s*>')

# Razor page handler
_RE_PAGE_MODEL = re.compile(r':\s*PageModel\b')
_RE_PAGE_HANDLER = re.compile(r'public\s+(?:async\s+)?(?:Task<[^>]+>|IActionResult|void)\s+'
                               r'On(Get|Post|Put|Delete)(\w*)\s*\(')

# Namespace
_RE_NS = re.compile(r'namespace\s+([\w.]+)')

# Class name
_RE_CLASS_NAME = re.compile(r'(?:public|internal)\s+(?:partial\s+)?class\s+(\w+)')


# ---------------------------------------------------------------------------
# File cache
# ---------------------------------------------------------------------------

class _FileCache:
    def __init__(self, cache_dir: Optional[Path]):
        self._dir   = Path(cache_dir) if cache_dir else None
        self.hits   = 0
        self.misses = 0

    def _key(self, path: str) -> str:
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:24]
        return f"api_{h}.json"

    def get(self, path: str) -> Optional[dict]:
        if not self._dir:
            return None
        try:
            p = self._dir / self._key(path)
            if p.exists():
                self.hits += 1
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        self.misses += 1
        return None

    def set(self, path: str, data: dict) -> None:
        if not self._dir:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / self._key(path)).write_text(
                json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Per-file extraction
# ---------------------------------------------------------------------------

def _extract_file(path: str) -> dict:
    """Extract all API-related constructs from one .cs file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    result: dict = {
        "endpoints":      [],
        "mediatr_handlers": [],
        "mediatr_requests": [],
        "dtos":           [],
        "page_handlers":  [],
        "mapper_profiles": [],
        "source_file":    path,
    }

    ns_m = _RE_NS.search(text)
    namespace = ns_m.group(1) if ns_m else ""

    cls_m = _RE_CLASS_NAME.search(text)
    class_name = cls_m.group(1) if cls_m else Path(path).stem

    has_auth = bool(_RE_AUTH.search(text))
    lines    = text.splitlines()

    # ---------------------------------------------------------------
    # MinimalApi / EndpointBase endpoints
    # ---------------------------------------------------------------
    ep_m = _RE_ENDPOINT_BASE.search(text)
    if ep_m:
        req_model  = ep_m.group(1) or ep_m.group(4) or ep_m.group(6) or ""
        resp_model = ep_m.group(5) or ep_m.group(7) or ""
        repos      = [m.group(1) or m.group(2) for m in _RE_REPO.finditer(text)
                      if m.group(1) or m.group(2)]
        services   = [m.group(1) for m in _RE_SERVICE.finditer(text)]

        # Find route from MapGet/Post/etc or AddRoute
        route, method = _find_route(text)

        ep: dict[str, Any] = {
            "class_name":     class_name,
            "namespace":      namespace,
            "method":         method,
            "endpoint":       route,
            "request_model":  req_model,
            "response_model": resp_model,
            "repositories":   [f"IRepository<{r}>" for r in repos if r],
            "services":       services,
            "entities_touched": list({r for r in repos if r}),
            "auth_required":  has_auth,
            "source_file":    path,
            "line_number":    _find_class_line(text, class_name),
            "confidence":     Confidence.HIGH.value,
            "pattern":        "MinimalApi.Endpoint",
        }
        # Resolve request/response fields if same file has the DTO
        ep["request_fields"]  = _find_dto_fields(text, req_model)
        ep["response_fields"] = _find_dto_fields(text, resp_model)
        result["endpoints"].append(ep)

    # ---------------------------------------------------------------
    # ASP.NET Core Controllers
    # ---------------------------------------------------------------
    if _RE_CONTROLLER.search(text):
        for m in _RE_HTTP_METHOD.finditer(text):
            http_verb = m.group(1).replace("Http", "").upper()
            route_m   = _RE_ROUTE_ATTR.search(text)
            route     = route_m.group(1) if route_m else f"/{class_name}"
            repos     = [m2.group(1) or m2.group(2) for m2 in _RE_REPO.finditer(text)
                         if m2.group(1) or m2.group(2)]
            line      = text[:m.start()].count("\n") + 1
            result["endpoints"].append({
                "class_name":     class_name,
                "namespace":      namespace,
                "method":         http_verb,
                "endpoint":       route,
                "request_model":  "",
                "response_model": "",
                "repositories":   [f"IRepository<{r}>" for r in repos if r],
                "services":       [],
                "entities_touched": list({r for r in repos if r}),
                "auth_required":  has_auth,
                "source_file":    path,
                "line_number":    line,
                "confidence":     Confidence.HIGH.value,
                "pattern":        "MvcController",
                "request_fields":  [],
                "response_fields": [],
            })

    # ---------------------------------------------------------------
    # MediatR handlers
    # ---------------------------------------------------------------
    for m in _RE_MEDIATR_HANDLER.finditer(text):
        req_type  = m.group(1)
        resp_type = m.group(2) or "void"
        repos     = [m2.group(1) or m2.group(2) for m2 in _RE_REPO.finditer(text)
                     if m2.group(1) or m2.group(2)]
        line      = text[:m.start()].count("\n") + 1
        result["mediatr_handlers"].append({
            "class_name":     class_name,
            "namespace":      namespace,
            "request_type":   req_type,
            "response_type":  resp_type,
            "repositories":   [f"IRepository<{r}>" for r in repos if r],
            "entities_touched": list({r for r in repos if r}),
            "source_file":    path,
            "line_number":    line,
            "confidence":     Confidence.HIGH.value,
        })

    # ---------------------------------------------------------------
    # MediatR IRequest objects (commands/queries)
    # ---------------------------------------------------------------
    for m in _RE_MEDIATR_REQUEST.finditer(text):
        resp = m.group(1) or "void"
        result["mediatr_requests"].append({
            "class_name":   class_name,
            "namespace":    namespace,
            "response_type": resp,
            "source_file":  path,
            "confidence":   Confidence.HIGH.value,
        })

    # ---------------------------------------------------------------
    # DTOs
    # ---------------------------------------------------------------
    for m in _RE_DTO_CLASS.finditer(text):
        dto_name = m.group(1)
        fields   = _find_dto_fields(text, dto_name)
        line     = text[:m.start()].count("\n") + 1
        result["dtos"].append({
            "name":        dto_name,
            "namespace":   namespace,
            "fields":      fields,
            "source_file": path,
            "line_number": line,
            "confidence":  Confidence.HIGH.value,
        })

    # ---------------------------------------------------------------
    # Razor Page handlers
    # ---------------------------------------------------------------
    if _RE_PAGE_MODEL.search(text):
        repos = [m2.group(1) or m2.group(2) for m2 in _RE_REPO.finditer(text)
                 if m2.group(1) or m2.group(2)]
        for m in _RE_PAGE_HANDLER.finditer(text):
            verb   = m.group(1).upper()
            suffix = m.group(2)
            line   = text[:m.start()].count("\n") + 1
            result["page_handlers"].append({
                "class_name":     class_name,
                "namespace":      namespace,
                "method":         verb,
                "handler_suffix": suffix,
                "repositories":   [f"IRepository<{r}>" for r in repos if r],
                "entities_touched": list({r for r in repos if r}),
                "source_file":    path,
                "line_number":    line,
                "confidence":     Confidence.HIGH.value,
            })

    # ---------------------------------------------------------------
    # AutoMapper profiles
    # ---------------------------------------------------------------
    for m in _RE_MAPPER.finditer(text):
        result["mapper_profiles"].append({
            "source":      m.group(1),
            "destination": m.group(2),
            "source_file": path,
            "confidence":  Confidence.HIGH.value,
        })

    return result


def _find_route(text: str) -> tuple[str, str]:
    """Find route and HTTP method from MapGet/Post/etc or AddRoute."""
    m = _RE_MAP_ROUTE.search(text)
    if m:
        return m.group(2), m.group(1).upper()
    # Look for string literal in AddRoute context
    route_m = re.search(r'MapGet|MapPost|MapPut|MapDelete|MapPatch', text)
    if route_m:
        method = route_m.group(0).replace("Map", "").upper()
        str_m  = re.search(r'"([^"]+)"', text[route_m.start():route_m.start()+200])
        route  = str_m.group(1) if str_m else "/unknown"
        return route, method
    return "/unknown", "GET"


def _find_class_line(text: str, class_name: str) -> int:
    m = re.search(rf'\bclass\s+{re.escape(class_name)}\b', text)
    return text[:m.start()].count("\n") + 1 if m else 1


def _find_dto_fields(text: str, dto_name: str) -> list[dict]:
    """Extract fields from a DTO class/record in the same file."""
    if not dto_name:
        return []
    # Find class/record block
    pattern = rf'(?:class|record)\s+{re.escape(dto_name)}\b[^{{]*\{{([^}}]*)\}}'
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    fields = []
    for pm in re.finditer(
            r'(?:public|internal)\s+([\w<>?, \[\]]+?)\s+(\w+)\s*\{?\s*(?:get|set|init)',
            body):
        fields.append({"name": pm.group(2), "type": pm.group(1).strip()})
    # Also handle positional record parameters
    if not fields:
        for pm in re.finditer(r'(\w[\w<>?, \[\]]*)\s+(\w+)\s*[,)]', body):
            fields.append({"name": pm.group(2), "type": pm.group(1).strip()})
    return fields


# ---------------------------------------------------------------------------
# APIExtractor (public API)
# ---------------------------------------------------------------------------

_SKIP_FILES = re.compile(
    r'(Program|Startup|appsettings|AssemblyInfo|Migrations|\.Designer\.|Snapshot)',
    re.IGNORECASE)


class APIExtractor:
    def __init__(
        self,
        output_dir: str = "memory/extracted",
        cache_dir:  str = "memory/cache",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cache = _FileCache(Path(cache_dir) if cache_dir else None)

    def extract_from_dir(self, api_dir: str) -> dict[str, Any]:
        src  = Path(api_dir)
        files = [f for f in src.rglob("*.cs") if not _SKIP_FILES.search(f.name)]
        print(f"[APIExtractor] Scanning {len(files)} files in {src.name}/")

        endpoints:       list[dict] = []
        handlers:        list[dict] = []
        requests:        list[dict] = []
        dtos:            list[dict] = []
        page_handlers:   list[dict] = []
        mapper_profiles: list[dict] = []

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(self._extract_one, str(f)): f for f in files}
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                    endpoints.extend(r["endpoints"])
                    handlers.extend(r["mediatr_handlers"])
                    requests.extend(r["mediatr_requests"])
                    dtos.extend(r["dtos"])
                    page_handlers.extend(r["page_handlers"])
                    mapper_profiles.extend(r["mapper_profiles"])
                except Exception as exc:
                    print(f"  [WARN] {futs[fut].name}: {exc}")

        # Build model map for cross-file DTO resolution
        model_map: dict[str, dict] = {}
        for dto in dtos:
            model_map[dto["name"]] = dto

        # Enrich endpoints with DTO fields from model_map
        for ep in endpoints:
            for field_key in ("request_fields", "response_fields"):
                if not ep.get(field_key):
                    model_name = ep.get("request_model" if "request" in field_key
                                        else "response_model", "")
                    if model_name and model_name in model_map:
                        ep[field_key] = model_map[model_name].get("fields", [])

        # Determine group from namespace/directory
        groups = self._build_groups(endpoints)

        result: dict[str, Any] = {
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "endpoint_count": len(endpoints),
            "handler_count":  len(handlers),
            "request_count":  len(requests),
            "dto_count":      len(dtos),
            "page_handler_count": len(page_handlers),
            "group_count":    len(groups),
            "endpoints":      endpoints,
            "mediatr_handlers": handlers,
            "mediatr_requests": requests,
            "dtos":           dtos,
            "page_handlers":  page_handlers,
            "mapper_profiles":mapper_profiles,
            "groups":         groups,
            "models":         model_map,
            "model_count":    len(model_map),
        }

        out = self.output_dir / "apis.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[APIExtractor] {len(endpoints)} endpoints, {len(handlers)} MediatR handlers "
              f"-> {out}")
        return result

    def _extract_one(self, path: str) -> dict:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        result = _extract_file(path)
        self._cache.set(path, result)
        return result

    def _build_groups(self, endpoints: list[dict]) -> list[dict]:
        from collections import defaultdict
        by_ns: dict[str, list] = defaultdict(list)
        for ep in endpoints:
            ns = ep.get("namespace", "").split(".")[-1]
            by_ns[ns].append(ep["endpoint"])
        return [{"group": ns, "endpoints": eps} for ns, eps in by_ns.items()]
