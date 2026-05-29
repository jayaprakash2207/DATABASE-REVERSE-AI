"""
Project Layout Detector

Auto-discovers .NET project layer directories for any project structure:
  Clean Architecture, DDD, Onion, Hexagonal, Microservices, Monolith, etc.

Returns a ProjectLayout with confidence-ranked dirs for:
  domain_dirs   — where entities / domain objects live
  infra_dirs    — where EF configs / DbContexts live
  api_dirs      — REST API / endpoint projects
  web_dirs      — MVC / Razor Pages / Blazor UI projects

Usage:
    from project_layout import detect_layout
    layout = detect_layout("/path/to/any/dotnet/project")
    print(layout.summary())
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Name-pattern classifiers
# ---------------------------------------------------------------------------

_DOMAIN_NAME = re.compile(
    r'(Domain|ApplicationCore|Application\.Core|DomainModel|BusinessCore'
    r'|DomainEntities|\.Core$)', re.IGNORECASE)

_INFRA_NAME = re.compile(
    r'(Infrastructure|\.Data$|\.Persistence$|DataAccess|Repository'
    r'|Repositories|EntityFramework|\.EF$|\.Dal$)', re.IGNORECASE)

_API_NAME = re.compile(
    r'(PublicApi|WebApi|\.Api$|\.API$|ApiHost|Endpoints'
    r'|ApiGateway|RestApi|GrpcService|GraphQL)', re.IGNORECASE)

_WEB_NAME = re.compile(
    r'(\.Web$|\.WebApp$|\.Mvc$|\.Blazor$|\.BlazorServer$'
    r'|\.BlazorWasm$|\.Pages$|\.UI$|\.Portal$|\.Frontend$)', re.IGNORECASE)

_TEST_NAME = re.compile(
    r'(Tests?|Specs?|IntegrationTests?|UnitTests?|FunctionalTests?'
    r'|E2ETests?|AcceptanceTests?)', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Content-signal patterns (sampled from .cs files)
# ---------------------------------------------------------------------------

_DOMAIN_SIGNALS = re.compile(
    r'IAggregateRoot|IValueObject|AggregateRoot\b|ValueObject\b'
    r'|IDomainEvent|DomainEvent\b|IEntity\b')

_INFRA_SIGNALS = re.compile(
    r'DbContext\b|IEntityTypeConfiguration|DbSet<|MigrationBuilder'
    r'|EfRepository\b|IAsyncRepository')

_API_SIGNALS = re.compile(
    r'\[ApiController\]|\[Route\(|ControllerBase\b|IEndpoint\b'
    r'|MapGet\(|MapPost\(|MapPut\(|MapDelete\(')

_WEB_SIGNALS = re.compile(
    r'PageModel\b|IActionResult\b|ViewResult\b|RazorPage'
    r'|@inject\b|@model\b|\.cshtml\b')

# Directories to skip during scanning
_SKIP_DIRS = frozenset({
    "bin", "obj", ".git", ".vs", ".idea", ".vscode",
    "node_modules", "packages", "__pycache__", "TestResults",
    ".github", ".docker",
})

# Namespace/folder segments considered generic (not useful for grouping)
_GENERIC_SEGMENTS = frozenset({
    "Entities", "Models", "ApplicationCore", "Infrastructure",
    "Application", "Persistence", "Data", "Business", "src",
    "lib", "main", "Aggregates", "ValueObjects",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LayerInfo:
    project_dir:  Path
    project_name: str
    layer_type:   str    # domain | infrastructure | api | web | test | other
    confidence:   float  # 0.0 – 1.0


@dataclass
class ProjectLayout:
    solution_root: Path
    layers:        list[LayerInfo] = field(default_factory=list)
    domain_dirs:   list[Path]      = field(default_factory=list)
    infra_dirs:    list[Path]      = field(default_factory=list)
    api_dirs:      list[Path]      = field(default_factory=list)
    web_dirs:      list[Path]      = field(default_factory=list)

    @property
    def primary_infra_dir(self) -> Path | None:
        return self.infra_dirs[0] if self.infra_dirs else None

    def summary(self) -> dict:
        return {
            "solution_root": str(self.solution_root),
            "domain_dirs":   [str(d) for d in self.domain_dirs],
            "infra_dirs":    [str(d) for d in self.infra_dirs],
            "api_dirs":      [str(d) for d in self.api_dirs],
            "web_dirs":      [str(d) for d in self.web_dirs],
            "layers": [
                {
                    "dir":        str(li.project_dir),
                    "name":       li.project_name,
                    "type":       li.layer_type,
                    "confidence": round(li.confidence, 2),
                }
                for li in self.layers
            ],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_layout(project_root: str | Path) -> ProjectLayout:
    """
    Auto-detect layer directories for any .NET project.

    Scans for .csproj files, classifies each by name + content signals,
    and returns a ProjectLayout with confidence-ranked dirs per layer.

    Falls back to heuristic folder-name scan if no .csproj files found.
    """
    root   = Path(project_root).resolve()
    layout = ProjectLayout(solution_root=root)

    csproj_files = _find_csproj(root)

    if not csproj_files:
        _fallback_scan(root, layout)
        return layout

    for csproj in csproj_files:
        proj_dir  = csproj.parent
        proj_name = csproj.stem
        ltype, conf = _classify(proj_dir, proj_name)

        li = LayerInfo(proj_dir, proj_name, ltype, conf)
        layout.layers.append(li)

        if ltype == "domain":
            layout.domain_dirs.append(proj_dir)
        elif ltype == "infrastructure":
            layout.infra_dirs.append(proj_dir)
        elif ltype == "api":
            layout.api_dirs.append(proj_dir)
        elif ltype == "web":
            layout.web_dirs.append(proj_dir)

    # Sort by descending confidence within each layer list
    def _score(d: Path, lt: str) -> float:
        for li in layout.layers:
            if li.project_dir == d and li.layer_type == lt:
                return li.confidence
        return 0.0

    layout.domain_dirs.sort(key=lambda d: _score(d, "domain"),         reverse=True)
    layout.infra_dirs.sort( key=lambda d: _score(d, "infrastructure"), reverse=True)
    layout.api_dirs.sort(   key=lambda d: _score(d, "api"),            reverse=True)
    layout.web_dirs.sort(   key=lambda d: _score(d, "web"),            reverse=True)

    return layout


def aggregate_from_path(namespace: str, entity: str,
                        source_file: str = "") -> str:
    """
    Generic aggregate name derivation — works for any .NET project.

    Priority:
    1. Namespace segment ending in 'Aggregate'  (standard DDD naming)
    2. Folder path segment ending in 'Aggregate'
    3. Parent folder name (when it's a meaningful DDD name, not a generic layer name)
    4. Last non-generic namespace segment
    5. entity + 'Aggregate'  (last resort)
    """
    # 1. Namespace: segment explicitly named "XxxAggregate"
    for part in namespace.split("."):
        if part.endswith("Aggregate") and len(part) > len("Aggregate"):
            return part

    # 2. Folder path: any segment named "XxxAggregate"
    if source_file:
        for part in Path(source_file).parts:
            if part.endswith("Aggregate") and len(part) > len("Aggregate"):
                return part

        # 3. Parent folder — meaningful DDD folder (not a generic layer name)
        parent = Path(source_file).parent.name
        if parent and parent not in _GENERIC_SEGMENTS:
            return parent

    # 4. Last non-generic namespace segment
    for part in reversed(namespace.split(".")):
        if part and part not in _GENERIC_SEGMENTS and len(part) > 2:
            return part

    # 5. Derive from entity name
    return entity + "Aggregate"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _find_csproj(root: Path) -> list[Path]:
    results = []
    for p in root.rglob("*.csproj"):
        if not any(seg in _SKIP_DIRS or seg.startswith(".")
                   for seg in p.parts):
            results.append(p)
    return results


def _classify(proj_dir: Path, proj_name: str) -> tuple[str, float]:
    """Return (layer_type, confidence 0–1)."""
    scores: dict[str, float] = {
        "domain": 0.0, "infrastructure": 0.0,
        "api": 0.0, "web": 0.0, "test": 0.0,
    }

    # --- Name signals (weight 0.5) ---
    if _TEST_NAME.search(proj_name):
        return "test", 0.95

    if _DOMAIN_NAME.search(proj_name):    scores["domain"]         += 0.5
    if _INFRA_NAME.search(proj_name):     scores["infrastructure"] += 0.5
    if _API_NAME.search(proj_name):       scores["api"]            += 0.5
    if _WEB_NAME.search(proj_name):       scores["web"]            += 0.5

    # --- Content signals (weight up to 0.5) ---
    sample = _read_sample(proj_dir, max_files=20, chars_per_file=800)
    if sample:
        def _hits(pattern: re.Pattern) -> int:
            return len(pattern.findall(sample))

        scores["domain"]         += min(_hits(_DOMAIN_SIGNALS) * 0.05, 0.5)
        scores["infrastructure"] += min(_hits(_INFRA_SIGNALS)  * 0.05, 0.5)
        scores["api"]            += min(_hits(_API_SIGNALS)    * 0.05, 0.5)
        scores["web"]            += min(_hits(_WEB_SIGNALS)    * 0.05, 0.5)

    best = max(scores, key=scores.__getitem__)
    conf = scores[best]

    if conf < 0.05:
        return "other", 0.0

    return best, min(conf, 1.0)


def _read_sample(proj_dir: Path, max_files: int, chars_per_file: int) -> str:
    chunks: list[str] = []
    count = 0
    for cs in proj_dir.rglob("*.cs"):
        if count >= max_files:
            break
        if any(seg in _SKIP_DIRS for seg in cs.parts):
            continue
        try:
            chunks.append(cs.read_text(encoding="utf-8", errors="ignore")[:chars_per_file])
            count += 1
        except OSError:
            pass
    return "\n".join(chunks)


def _fallback_scan(root: Path, layout: ProjectLayout) -> None:
    """
    No .csproj found — heuristic folder-name matching.
    Handles non-SDK projects, monorepos, or non-standard layouts.
    """
    _D = re.compile(r'^(domain|applicationcore|entities|models|core)$', re.I)
    _I = re.compile(r'^(infrastructure|data|persistence|repositories|repository)$', re.I)
    _A = re.compile(r'^(api|publicapi|webapi|endpoints|controllers)$', re.I)
    _W = re.compile(r'^(web|mvc|blazor|pages|ui|frontend|portal)$', re.I)

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _SKIP_DIRS:
            continue
        name = child.name
        if   _D.match(name): _add(layout, child, name, "domain",         0.55)
        elif _I.match(name): _add(layout, child, name, "infrastructure", 0.55)
        elif _A.match(name): _add(layout, child, name, "api",            0.55)
        elif _W.match(name): _add(layout, child, name, "web",            0.55)


def _add(layout: ProjectLayout, d: Path, name: str,
         ltype: str, conf: float) -> None:
    layout.layers.append(LayerInfo(d, name, ltype, conf))
    getattr(layout, f"{ltype}_dirs").append(d)
