"""
Technology Detector

Multi-language, multi-framework technology stack detection.
Produces a TechContext without any parsing — only file presence
and content signal scanning.

Used by both AdapterRegistry (adapter selection) and main.py
(reporting + reasoning).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from models.semantic_model import TechContext
from models.universal import Language, Technology


# ---------------------------------------------------------------------------
# Signal tables
# ---------------------------------------------------------------------------

# File-level presence signals (glob patterns → (language, confidence_boost))
_FILE_SIGNALS: list[tuple[str, Language, float]] = [
    ("*.cs",              Language.CSHARP,     0.6),
    ("*.java",            Language.JAVA,       0.6),
    ("*.kt",              Language.KOTLIN,     0.6),
    ("*.py",              Language.PYTHON,     0.6),
    ("*.ts",              Language.TYPESCRIPT, 0.5),
    ("*.js",              Language.JAVASCRIPT, 0.4),
    ("tsconfig.json",     Language.TYPESCRIPT, 0.3),
    ("package.json",      Language.JAVASCRIPT, 0.2),
    ("*.csproj",          Language.CSHARP,     0.4),
    ("pom.xml",           Language.JAVA,       0.5),
    ("build.gradle",      Language.JAVA,       0.4),
    ("build.gradle.kts",  Language.KOTLIN,     0.5),
    ("requirements.txt",  Language.PYTHON,     0.3),
    ("pyproject.toml",    Language.PYTHON,     0.3),
    ("Pipfile",           Language.PYTHON,     0.3),
]

# Content signals → (Technology, minimum_hits_required, confidence)
_ORM_CONTENT_SIGNALS: list[tuple[re.Pattern, Technology, int, float]] = [
    (re.compile(r'Microsoft\.EntityFrameworkCore|DbContext|DbSet<', re.I),  Technology.EF_CORE,    1, 0.9),
    (re.compile(r'spring-boot-starter-data-jpa|@Entity\b|JpaRepository',    re.M), Technology.SPRING_JPA, 1, 0.9),
    (re.compile(r'hibernate-core|SessionFactory|@Table\b',                   re.I), Technology.HIBERNATE,  1, 0.7),
    (re.compile(r'from django\.db import models|models\.Model',              re.I), Technology.DJANGO_ORM, 1, 0.9),
    (re.compile(r'from sqlalchemy|import sqlalchemy|declarative_base',       re.I), Technology.SQLALCHEMY, 1, 0.8),
    (re.compile(r'"sequelize"|DataTypes\.|Model\.init',                      re.I), Technology.SEQUELIZE,  1, 0.8),
    (re.compile(r'"mongoose"|new Schema\(|mongoose\.model\(',                re.I), Technology.MONGOOSE,   1, 0.8),
    (re.compile(r'"typeorm"|@Entity\(\)|createConnection',                   re.I), Technology.TYPEORM,    1, 0.8),
    (re.compile(r'"@prisma/client"|prisma\.|PrismaClient',                   re.I), Technology.PRISMA,     1, 0.9),
]

_ARCH_SIGNALS: dict[str, list[re.Pattern]] = {
    "microservices": [
        re.compile(r'ServiceBus|EventBus|MassTransit|RabbitMQ|Kafka|NServiceBus', re.I),
        re.compile(r'ApiGateway|Ocelot|YARP\.ReverseProxy', re.I),
        re.compile(r'IIntegrationEvent|IntegrationEventHandler', re.I),
    ],
    "modular_monolith": [
        re.compile(r'IModule|ModuleRegistration|\.Modules\b', re.I),
        re.compile(r'AddModule\(|UseModule\(', re.I),
    ],
    "layered": [
        re.compile(r'Domain|Application|Infrastructure|Presentation', re.I),
        re.compile(r'IRepository|IService|IUseCase', re.I),
    ],
}

_ARCH_ANTI: dict[str, list[re.Pattern]] = {
    "microservices": [
        re.compile(r'DbContext\b', re.I),  # shared DB = monolith signal
    ],
}

_DB_SIGNALS: dict[str, re.Pattern] = {
    "postgresql": re.compile(r'Npgsql|postgres|psycopg2|pg\b', re.I),
    "sqlserver":  re.compile(r'SqlServer|mssql|System\.Data\.SqlClient', re.I),
    "sqlite":     re.compile(r'sqlite|\.db"', re.I),
    "mysql":      re.compile(r'mysql|MariaDB', re.I),
    "mongodb":    re.compile(r'mongodb|MongoClient|mongoDB', re.I),
    "redis":      re.compile(r'StackExchange\.Redis|redis', re.I),
}

_SKIP_DIRS = {
    "bin", "obj", "target", "build", ".git", "node_modules",
    "dist", "venv", ".venv", "env", "__pycache__", "Migrations",
    "migrations", ".next", "coverage", "vendor",
}


class TechnologyDetector:
    """
    Scans a project directory and emits a TechContext.
    Results are cached per project_root.
    """

    def __init__(self) -> None:
        self._cache: dict[str, TechContext] = {}

    def detect(self, project_root: str | Path) -> TechContext:
        root = Path(project_root).resolve()
        key  = str(root)
        if key in self._cache:
            return self._cache[key]

        ctx = self._run_detection(root)
        self._cache[key] = ctx
        return ctx

    # ------------------------------------------------------------------

    def _run_detection(self, root: Path) -> TechContext:
        # 1. Language detection via file counts
        lang_counts: dict[Language, int] = {}
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if any(p in _SKIP_DIRS for p in f.parts):
                continue
            suf = f.suffix.lower()
            lang_map = {
                ".cs": Language.CSHARP, ".java": Language.JAVA,
                ".kt": Language.KOTLIN, ".py": Language.PYTHON,
                ".ts": Language.TYPESCRIPT, ".js": Language.JAVASCRIPT,
            }
            if suf in lang_map:
                lang_counts[lang_map[suf]] = lang_counts.get(lang_map[suf], 0) + 1

        languages = [k for k, _ in sorted(lang_counts.items(), key=lambda x: -x[1])]

        # 2. Scan config + source files for content signals
        scan_text = self._collect_signal_text(root)

        # 3. ORM detection
        orms: list[tuple[Technology, float]] = []
        for pattern, tech, min_hits, conf in _ORM_CONTENT_SIGNALS:
            hits = len(pattern.findall(scan_text))
            if hits >= min_hits:
                orms.append((tech, conf))
        orms_sorted = [t for t, _ in sorted(orms, key=lambda x: -x[1])]

        # 4. Architecture detection
        arch = self._detect_architecture(scan_text)

        # 5. DB detection (content signals)
        db_types = [db for db, pat in _DB_SIGNALS.items() if pat.search(scan_text)]

        # 5b. SQL file presence → dialect detection
        sql_files = [f for f in root.rglob("*.sql")
                     if not any(p in _SKIP_DIRS for p in f.parts)]
        if sql_files:
            sql_sample = ""
            for sf in sql_files[:10]:
                try:
                    sql_sample += sf.read_text(encoding="utf-8", errors="ignore")[:4000]
                except OSError:
                    pass
            # SQL Server signals: [dbo], GO terminator, IDENTITY, nvarchar
            if re.search(r'\[dbo\]|\bGO\b|\bIDENTITY\b|nvarchar\b|\bNOCOUNT\b', sql_sample, re.I):
                if "sqlserver" not in db_types:
                    db_types.insert(0, "sqlserver")
            # PostgreSQL signals: SERIAL, $n params, RETURNING, pg_ catalog
            elif re.search(r'\bSERIAL\b|\bBIGSERIAL\b|\bRETURNING\b|\$\d+\b|pg_catalog', sql_sample, re.I):
                if "postgresql" not in db_types:
                    db_types.insert(0, "postgresql")
            # MySQL signals: AUTO_INCREMENT, ENGINE=InnoDB, backtick idents
            elif re.search(r'\bAUTO_INCREMENT\b|ENGINE\s*=\s*InnoDB|`\w+`\s+`\w+`', sql_sample, re.I):
                if "mysql" not in db_types:
                    db_types.insert(0, "mysql")
            # Generic SQL (SQLite-assumed) when no other signal matches
            elif not db_types:
                db_types.append("sqlite")

        # 6. API frameworks
        api_styles = self._detect_api_styles(scan_text, languages)

        # 7. Package files
        pkg_files: list[str] = []
        for glob in ["*.csproj", "pom.xml", "package.json", "requirements.txt",
                     "build.gradle", "pyproject.toml", "Pipfile"]:
            pkg_files.extend(str(p) for p in list(root.rglob(glob))[:3])

        confidence = min(
            0.4 + 0.1 * min(len(languages), 4) + 0.1 * min(len(orms_sorted), 2),
            1.0,
        )

        return TechContext(
            project_root  = str(root),
            languages     = languages,
            frameworks    = api_styles,
            orms          = orms_sorted,
            api_styles    = api_styles,
            architecture  = arch,
            db_types      = db_types,
            package_files = pkg_files,
            confidence    = confidence,
        )

    def _collect_signal_text(self, root: Path, max_bytes: int = 500_000) -> str:
        """Collect representative text from config and source files."""
        chunks: list[str] = []
        total  = 0

        # Priority: package/config files first
        priority_globs = [
            "*.csproj", "pom.xml", "build.gradle", "build.gradle.kts",
            "requirements.txt", "pyproject.toml", "Pipfile", "package.json",
            "appsettings*.json", "application*.properties", "application*.yml",
            "docker-compose*.yml",
        ]
        for glob in priority_globs:
            for f in root.rglob(glob):
                if any(p in _SKIP_DIRS for p in f.parts):
                    continue
                try:
                    chunk = f.read_text(encoding="utf-8", errors="ignore")[:20_000]
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        return "\n".join(chunks)
                except OSError:
                    pass

        # Then source files (sample) + SQL schema files
        src_globs = ["*.cs", "*.java", "*.py", "*.ts", "*.js", "*.kt", "*.sql"]
        seen = 0
        for glob in src_globs:
            for f in list(root.rglob(glob))[:15]:
                if any(p in _SKIP_DIRS for p in f.parts):
                    continue
                try:
                    chunk = f.read_text(encoding="utf-8", errors="ignore")[:10_000]
                    chunks.append(chunk)
                    total += len(chunk)
                    seen += 1
                    if seen >= 30 or total > max_bytes:
                        return "\n".join(chunks)
                except OSError:
                    pass

        return "\n".join(chunks)

    def _detect_architecture(self, scan_text: str) -> str:
        # Microservices: needs 2+ signals, no anti-signals
        ms_hits = sum(1 for p in _ARCH_SIGNALS["microservices"] if p.search(scan_text))
        ms_anti = sum(1 for p in _ARCH_ANTI.get("microservices", []) if p.search(scan_text))
        if ms_hits >= 2 and ms_anti == 0:
            return "microservices"

        mm_hits = sum(1 for p in _ARCH_SIGNALS["modular_monolith"] if p.search(scan_text))
        if mm_hits >= 2:
            return "modular_monolith"

        lm_hits = sum(1 for p in _ARCH_SIGNALS["layered"] if p.search(scan_text))
        if lm_hits >= 1:
            return "layered_monolith"

        return "unknown"

    def _detect_api_styles(self, scan_text: str, languages: list[Language]) -> list[str]:
        styles: list[str] = []
        checks = [
            ("aspnet_core",  re.compile(r'Microsoft\.AspNetCore', re.I)),
            ("spring_mvc",   re.compile(r'@RestController|@Controller|@GetMapping', re.M)),
            ("django_rest",  re.compile(r'rest_framework|APIView|ViewSet', re.I)),
            ("fastapi",      re.compile(r'from fastapi|import fastapi|@app\.(get|post)', re.I)),
            ("flask",        re.compile(r'from flask|import flask|@app\.route', re.I)),
            ("express",      re.compile(r"require\(['\"]express", re.I)),
            ("graphql",      re.compile(r'graphql|@Query\b|@Mutation\b', re.I)),
            ("grpc",         re.compile(r'\.proto\b|grpc\b|protobuf', re.I)),
        ]
        for name, pattern in checks:
            if pattern.search(scan_text):
                styles.append(name)
        return styles
