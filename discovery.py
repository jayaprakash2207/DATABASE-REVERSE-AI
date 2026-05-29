"""
discovery.py — Project Discovery Scanner

Scans a legacy enterprise application and produces REVIEW/inventory.json
with full stack detection and semantic file classification.

Usage:
    python discovery.py --project ../eShopOnWeb-main
    python discovery.py --project ../eShopOnWeb-main --out REVIEW/inventory.json
"""

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Language detection — extension -> language name
# ---------------------------------------------------------------------------

LANGUAGE_MAP: dict[str, str] = {
    ".cs":      "C#",
    ".razor":   "Razor (Blazor)",
    ".cshtml":  "Razor (MVC/Pages)",
    ".html":    "HTML",
    ".css":     "CSS",
    ".scss":    "SCSS",
    ".js":      "JavaScript",
    ".ts":      "TypeScript",
    ".py":      "Python",
    ".json":    "JSON",
    ".xml":     "XML",
    ".yml":     "YAML",
    ".yaml":    "YAML",
    ".bicep":   "Bicep",
    ".sql":     "SQL",
    ".md":      "Markdown",
    ".sh":      "Shell",
    ".ps1":     "PowerShell",
    ".dockerfile": "Dockerfile",
}

# Extension groups that count as "primary" languages (not config/markup)
PRIMARY_EXTENSIONS = {".cs", ".razor", ".cshtml", ".js", ".ts", ".py", ".sql", ".bicep"}


# ---------------------------------------------------------------------------
# Framework detection — NuGet package -> canonical framework name
# ---------------------------------------------------------------------------

FRAMEWORK_SIGNALS: list[tuple[re.Pattern, str]] = [
    # ASP.NET Core
    (re.compile(r"Microsoft\.AspNetCore\.Components\.WebAssembly", re.I), "Blazor WebAssembly"),
    (re.compile(r"Microsoft\.AspNetCore\.Components", re.I),              "Blazor Server"),
    (re.compile(r"Microsoft\.AspNetCore\.Mvc", re.I),                     "ASP.NET Core MVC"),
    (re.compile(r"Microsoft\.AspNetCore\.Identity", re.I),                "ASP.NET Core Identity"),
    (re.compile(r"Microsoft\.AspNetCore\.Authentication\.JwtBearer", re.I), "JWT Bearer Auth"),
    # EF Core
    (re.compile(r"Microsoft\.EntityFrameworkCore", re.I),                 "Entity Framework Core"),
    # Patterns / DDD
    (re.compile(r"MediatR", re.I),                                        "MediatR (CQRS)"),
    (re.compile(r"AutoMapper", re.I),                                     "AutoMapper"),
    (re.compile(r"FluentValidation", re.I),                               "FluentValidation"),
    (re.compile(r"Ardalis\.Specification", re.I),                         "Ardalis Specification (Repository)"),
    (re.compile(r"Ardalis\.ApiEndpoints", re.I),                          "Ardalis API Endpoints"),
    (re.compile(r"Ardalis\.GuardClauses", re.I),                          "Ardalis Guard Clauses"),
    (re.compile(r"MinimalApi\.Endpoint", re.I),                           "Minimal API"),
    # Azure
    (re.compile(r"Azure\.Identity", re.I),                                "Azure Identity"),
    (re.compile(r"Azure\.Extensions\.AspNetCore\.Configuration\.Secrets", re.I), "Azure Key Vault"),
    # API Docs
    (re.compile(r"Swashbuckle", re.I),                                    "Swagger / OpenAPI"),
    # Testing
    (re.compile(r"xunit", re.I),                                          "xUnit"),
    (re.compile(r"NSubstitute", re.I),                                    "NSubstitute"),
    (re.compile(r"Microsoft\.AspNetCore\.Mvc\.Testing", re.I),            "ASP.NET Core Integration Testing"),
    # Storage
    (re.compile(r"Blazored\.LocalStorage", re.I),                         "Blazored LocalStorage"),
]


# ---------------------------------------------------------------------------
# Database detection — package or config key -> database name
# ---------------------------------------------------------------------------

DB_PACKAGE_SIGNALS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"EntityFrameworkCore\.SqlServer", re.I),  "SQL Server (EF Core)"),
    (re.compile(r"EntityFrameworkCore\.InMemory", re.I),   "In-Memory DB (EF Core)"),
    (re.compile(r"EntityFrameworkCore\.Sqlite", re.I),     "SQLite (EF Core)"),
    (re.compile(r"EntityFrameworkCore\.Cosmos", re.I),     "Azure Cosmos DB"),
    (re.compile(r"Npgsql", re.I),                          "PostgreSQL"),
    (re.compile(r"MySql\.Data|Pomelo\.EntityFrameworkCore\.MySql", re.I), "MySQL"),
    (re.compile(r"MongoDB", re.I),                         "MongoDB"),
    (re.compile(r"Redis|StackExchange\.Redis", re.I),      "Redis"),
    (re.compile(r"Azure\.Storage", re.I),                  "Azure Blob Storage"),
]

DB_CONFIG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Server=|Data Source=.*\.mdf|sqlserver", re.I), "SQL Server"),
    (re.compile(r"Host=.*port=5432|postgresql", re.I),           "PostgreSQL"),
    (re.compile(r"\.db\b|sqlite", re.I),                         "SQLite"),
    (re.compile(r"mongodb\+srv|mongodb://", re.I),               "MongoDB"),
    (re.compile(r"redis://|6379", re.I),                         "Redis"),
    (re.compile(r"AccountEndpoint.*cosmos|cosmos\.azure\.com", re.I), "Azure Cosmos DB"),
]


# ---------------------------------------------------------------------------
# Architecture type detection
# ---------------------------------------------------------------------------

ARCH_SIGNALS: dict[str, list[re.Pattern]] = {
    "Clean Architecture": [
        re.compile(r"/ApplicationCore/|/Application/|/Domain/", re.I),
        re.compile(r"/Infrastructure/", re.I),
    ],
    "DDD (Domain-Driven Design)": [
        re.compile(r"Aggregate|AggregateRoot|ValueObject|DomainEvent", re.I),
        re.compile(r"/Entities/.*Aggregate|IAggregateRoot", re.I),
    ],
    "CQRS": [
        re.compile(r"IRequest|IRequestHandler|MediatR|Command|Query", re.I),
    ],
    "Repository Pattern": [
        re.compile(r"IRepository|EfRepository|IAsyncRepository|Specification", re.I),
    ],
    "Minimal API": [
        re.compile(r"IEndpoint|MapGet|MapPost|MinimalApi|ApiEndpoints", re.I),
    ],
    "MVC": [
        re.compile(r"Controller|ActionResult|IActionResult|ViewResult", re.I),
    ],
    # Microservices requires REAL evidence — docker alone is not enough
    "Microservices": [
        re.compile(r"ServiceBus|EventBus|MessageBroker|Dapr|NServiceBus|MassTransit|RabbitMQ|Kafka", re.I),
        re.compile(r"ApiGateway|Ocelot|YARP\.ReverseProxy|nginx.*upstream", re.I),
        re.compile(r"IIntegrationEvent|IntegrationEventHandler|OutboxMessage", re.I),
    ],
    "Layered Monolith": [
        re.compile(r"/Infrastructure/|/DataAccess/|/Persistence/", re.I),
        re.compile(r"/ApplicationCore/|/Application/|/Domain/|/Business/", re.I),
    ],
    "Modular Monolith": [
        re.compile(r"/Modules/|/Features/|/Slices/", re.I),
        re.compile(r"IModule\b|ModuleInitializer|FeatureManagement", re.I),
    ],
}

# Minimum signal hits required per architecture type
_ARCH_MIN_SIGNALS: dict[str, int] = {
    "Clean Architecture":        1,
    "DDD (Domain-Driven Design)": 1,
    "CQRS":                      1,
    "Repository Pattern":        1,
    "Minimal API":               1,
    "MVC":                       1,
    "Microservices":             2,   # MUST match at least 2 of 3 real microservices signals
    "Layered Monolith":          2,
    "Modular Monolith":          2,
}

# Anti-signals: if ALL of these are present, suppress the classification
_ARCH_ANTI_SIGNALS: dict[str, list[re.Pattern]] = {
    # If it has a shared DbContext and no inter-service comms, it's NOT microservices
    "Microservices": [
        re.compile(r"DbContext\b", re.I),                      # single shared DB = monolith
    ],
}


# ---------------------------------------------------------------------------
# File map classification — semantic role per path
# ---------------------------------------------------------------------------

FILE_MAP_RULES: dict[str, list[re.Pattern]] = {
    "data": [
        re.compile(r"/Infrastructure/Data/|/Migrations/|DbContext|/Config/.*Config\.cs", re.I),
        re.compile(r"\.sql$|EntityTypeConfiguration|IEntityTypeConfiguration", re.I),
        re.compile(r"/Entities/|/Models/|Entity\.cs|DbSet", re.I),
    ],
    "business": [
        re.compile(r"/ApplicationCore/Services/|/Services/.*Service\.cs", re.I),
        re.compile(r"/Specifications/|/Interfaces/|/Domain/|/UseCases/", re.I),
        re.compile(r"Handler\.cs|Command\.cs|Query\.cs|IUseCase", re.I),
        re.compile(r"/ApplicationCore/Entities/", re.I),
    ],
    "integration": [
        re.compile(r"/PublicApi/|/Controllers/|/Endpoints/|/Api/", re.I),
        re.compile(r"Controller\.cs|Endpoint\.cs|MapGet|MapPost", re.I),
        re.compile(r"appsettings.*\.json$|Program\.cs$|Startup\.cs$", re.I),
        re.compile(r"docker-compose|Dockerfile|\.bicep$|azure\.yaml$", re.I),
    ],
    "quality": [
        re.compile(r"/tests/|/Tests/|\.Tests\.|Spec\.cs$|Tests\.cs$", re.I),
        re.compile(r"FunctionalTest|IntegrationTest|UnitTest|xunit|NSubstitute", re.I),
        re.compile(r"\.runsettings$", re.I),
    ],
    "architect": [
        re.compile(r"/Interfaces/|/Abstractions/|/Contracts/", re.I),
        re.compile(r"IRepository|IService|IUnitOfWork|IAggregateRoot", re.I),
        re.compile(r"/ApplicationCore/|/Domain/|AggregateRoot|ValueObject", re.I),
        re.compile(r"README\.md$|\.sln$|Directory\.Packages\.props$", re.I),
    ],
}

# Priority order for file_map: first match wins
FILE_MAP_PRIORITY = ["quality", "data", "integration", "business", "architect"]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class ProjectScanner:
    def __init__(self, project_path: str):
        self.root = Path(project_path).resolve()
        self._all_files: list[Path] = []
        self._packages: list[str] = []       # all PackageReference names
        self._config_content: str = ""       # concatenated appsettings content
        self._source_sample: str = ""        # small sample of .cs file content

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def scan(self) -> dict:
        self._collect_files()
        self._collect_packages()
        self._collect_config_content()
        self._collect_source_sample()

        stack = {
            "languages":       self._detect_languages(),
            "frameworks":      self._detect_frameworks(),
            "databases":       self._detect_databases(),
            "architecture_type": self._detect_architecture(),
        }

        file_map = self._build_file_map()

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_project": str(self.root),
            "project_name":   self.root.name,
            "total_files":    len(self._all_files),
            "stack":          stack,
            "file_map":       file_map,
        }

    # ------------------------------------------------------------------
    # File collection
    # ------------------------------------------------------------------

    def _collect_files(self) -> None:
        SKIP_DIRS = {
            ".git", ".vs", ".vscode", "bin", "obj",
            "node_modules", "wwwroot/lib", "wwwroot/fonts",
        }
        for path in self.root.rglob("*"):
            if path.is_file():
                parts = set(path.relative_to(self.root).parts)
                if not parts & SKIP_DIRS:
                    self._all_files.append(path)

    # ------------------------------------------------------------------
    # Package collection from .csproj / Directory.Packages.props
    # ------------------------------------------------------------------

    def _collect_packages(self) -> None:
        packages: list[str] = []
        for f in self._all_files:
            if f.suffix in (".csproj", ".props"):
                try:
                    tree = ET.parse(f)
                    for elem in tree.iter():
                        if elem.tag in ("PackageReference", "PackageVersion"):
                            name = elem.get("Include", "")
                            if name:
                                packages.append(name)
                except ET.ParseError:
                    pass
        self._packages = packages

    # ------------------------------------------------------------------
    # Config content
    # ------------------------------------------------------------------

    def _collect_config_content(self) -> None:
        chunks: list[str] = []
        for f in self._all_files:
            if "appsettings" in f.name.lower() and f.suffix == ".json":
                try:
                    chunks.append(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        self._config_content = "\n".join(chunks)

    # ------------------------------------------------------------------
    # Source sample (for arch signals not in packages)
    # ------------------------------------------------------------------

    def _collect_source_sample(self) -> None:
        chunks: list[str] = []
        for f in self._all_files:
            if f.suffix == ".cs" and len(chunks) < 30:
                try:
                    chunks.append(f.read_text(encoding="utf-8", errors="replace")[:2000])
                except OSError:
                    pass
        self._source_sample = "\n".join(chunks)

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    def _detect_languages(self) -> list[dict]:
        counts: dict[str, int] = defaultdict(int)
        for f in self._all_files:
            ext = f.suffix.lower()
            if ext == "" and f.name.lower() == "dockerfile":
                ext = ".dockerfile"
            if ext in LANGUAGE_MAP:
                counts[LANGUAGE_MAP[ext]] += 1

        # Sort by count descending; flag primaries
        sorted_langs = sorted(counts.items(), key=lambda x: -x[1])
        result = []
        for lang, count in sorted_langs:
            # Find original extension for this lang
            ext = next((e for e, n in LANGUAGE_MAP.items() if n == lang), "")
            result.append({
                "name":    lang,
                "files":   count,
                "primary": ext in PRIMARY_EXTENSIONS,
            })
        return result

    # ------------------------------------------------------------------
    # Framework detection
    # ------------------------------------------------------------------

    def _detect_frameworks(self) -> list[dict]:
        combined = " ".join(self._packages) + "\n" + self._source_sample
        found: dict[str, dict] = {}

        for pattern, name in FRAMEWORK_SIGNALS:
            if name in found:
                continue
            m = pattern.search(combined)
            if m:
                # Find which package matched for traceability
                pkg = next(
                    (p for p in self._packages if pattern.search(p)),
                    "source-code"
                )
                found[name] = {"name": name, "detected_via": pkg}

        return list(found.values())

    # ------------------------------------------------------------------
    # Database detection
    # ------------------------------------------------------------------

    def _detect_databases(self) -> list[dict]:
        pkg_text = " ".join(self._packages)
        found: dict[str, dict] = {}

        # From NuGet packages
        for pattern, name in DB_PACKAGE_SIGNALS:
            if name in found:
                continue
            m = pattern.search(pkg_text)
            if m:
                pkg = next((p for p in self._packages if pattern.search(p)), "")
                found[name] = {"name": name, "detected_via": pkg, "source": "package"}

        # From connection strings / appsettings
        for pattern, name in DB_CONFIG_PATTERNS:
            if name in found:
                continue
            if pattern.search(self._config_content):
                found[name] = {"name": name, "detected_via": "appsettings.json", "source": "config"}

        return list(found.values())

    # ------------------------------------------------------------------
    # Architecture type detection
    # ------------------------------------------------------------------

    def _detect_architecture(self) -> list[dict]:
        dir_paths = "\n".join(
            str(f.relative_to(self.root)) for f in self._all_files
        )
        corpus = dir_paths + "\n" + self._source_sample + "\n" + " ".join(self._packages)

        result: list[dict] = []
        reasoning: list[dict] = []

        for arch_name, patterns in ARCH_SIGNALS.items():
            min_req   = _ARCH_MIN_SIGNALS.get(arch_name, 1)
            matches   = [p for p in patterns if p.search(corpus)]
            matched_n = len(matches)

            # Check anti-signals (suppress classification if anti-signals present)
            anti_hits: list[str] = []
            for ap in _ARCH_ANTI_SIGNALS.get(arch_name, []):
                if ap.search(corpus):
                    anti_hits.append(ap.pattern)

            rejected_by_anti = bool(anti_hits)
            qualified        = (matched_n >= min_req) and not rejected_by_anti

            rec = {
                "pattern":          arch_name,
                "signals_matched":  matched_n,
                "signals_required": min_req,
                "signals_total":    len(patterns),
                "matched_patterns": [p.pattern for p in patterns if p.search(corpus)],
                "rejected_signals": anti_hits,
                "qualified":        qualified,
            }
            if qualified:
                rec["confidence"] = "high" if matched_n == len(patterns) else "medium"
                result.append({
                    "pattern":         arch_name,
                    "confidence":      rec["confidence"],
                    "signals_matched": matched_n,
                    "signals_total":   len(patterns),
                    "evidence":        rec["matched_patterns"],
                })
            else:
                rec["reason_rejected"] = (
                    f"anti_signal={anti_hits}" if rejected_by_anti
                    else f"need {min_req} signals, got {matched_n}"
                )
            reasoning.append(rec)

        # Write reasoning report
        self._write_arch_reasoning(reasoning)

        # Sort: high confidence first
        result.sort(key=lambda x: (x["confidence"] != "high", -x["signals_matched"]))
        return result

    def _write_arch_reasoning(self, reasoning: list[dict]) -> None:
        out_dir = self.root.parent / "enterprise-data-architect" / "memory" / "extracted"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "architecture_confidence_reasoning.json"
            path.write_text(
                json.dumps({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "reasoning": reasoning,
                }, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # File map classification
    # ------------------------------------------------------------------

    def _build_file_map(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {k: [] for k in FILE_MAP_RULES}

        for f in self._all_files:
            rel = str(f.relative_to(self.root)).replace("\\", "/")
            assigned = False

            for category in FILE_MAP_PRIORITY:
                patterns = FILE_MAP_RULES[category]
                # Check against both the relative path and file content hint
                if any(p.search(rel) for p in patterns):
                    buckets[category].append(rel)
                    assigned = True
                    break

            # Uncategorized files fall into "architect" as catch-all
            # (only for .cs/.razor/.cshtml — skip assets/configs)
            if not assigned and f.suffix in (".cs", ".razor", ".cshtml"):
                buckets["architect"].append(rel)

        # Sort each bucket for deterministic output
        for key in buckets:
            buckets[key].sort()

        return buckets


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Project Discovery Scanner — generates REVIEW/inventory.json"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to the project root to scan",
    )
    parser.add_argument(
        "--out",
        default="REVIEW/inventory.json",
        help="Output path for inventory.json (default: REVIEW/inventory.json)",
    )
    args = parser.parse_args()

    project_path = Path(args.project)
    if not project_path.exists():
        print(f"[discovery] ERROR: project path not found: {project_path}")
        raise SystemExit(1)

    print(f"[discovery] Scanning {project_path.resolve()} ...")
    scanner = ProjectScanner(str(project_path))
    inventory = scanner.scan()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)

    # Print summary
    stack = inventory["stack"]
    print(f"[discovery] Files scanned  : {inventory['total_files']}")
    print(f"[discovery] Languages      : {len(stack['languages'])}")
    print(f"[discovery] Frameworks     : {len(stack['frameworks'])}")
    print(f"[discovery] Databases      : {len(stack['databases'])}")
    print(f"[discovery] Arch patterns  : {len(stack['architecture_type'])}")

    fm = inventory["file_map"]
    print(f"[discovery] file_map.data        : {len(fm['data'])} files")
    print(f"[discovery] file_map.business    : {len(fm['business'])} files")
    print(f"[discovery] file_map.integration : {len(fm['integration'])} files")
    print(f"[discovery] file_map.quality     : {len(fm['quality'])} files")
    print(f"[discovery] file_map.architect   : {len(fm['architect'])} files")
    print(f"[discovery] Inventory written -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
