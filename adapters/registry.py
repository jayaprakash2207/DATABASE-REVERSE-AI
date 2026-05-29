"""
Adapter Registry — technology detection + adapter selection.

Usage:
    registry = AdapterRegistry()
    tech_ctx = registry.detect(project_root)
    adapters = registry.select_adapters(tech_ctx)
    model    = registry.run(project_root)   # detect + extract in one call
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from models.semantic_model import SemanticModel, TechContext
from models.universal import Language, Technology
from .base import BaseAdapter


# ---------------------------------------------------------------------------
# Technology detection helpers
# ---------------------------------------------------------------------------

_PACKAGE_FILES = {
    "csharp":     ["*.csproj", "*.sln"],
    "java":       ["pom.xml", "build.gradle", "build.gradle.kts"],
    "python":     ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"],
    "javascript": ["package.json"],
    "typescript": ["tsconfig.json"],
    "kotlin":     ["build.gradle.kts"],
}

_TECH_SIGNALS: dict[str, list[re.Pattern]] = {
    # .NET / EF Core
    "ef_core":   [re.compile(r"Microsoft\.EntityFrameworkCore", re.I),
                  re.compile(r"DbContext|DbSet<", re.M)],
    # Spring / JPA
    "spring_jpa": [re.compile(r"spring-boot-starter-data-jpa", re.I),
                   re.compile(r"@Entity|@Repository|@Service", re.M)],
    "hibernate":  [re.compile(r"hibernate-core|hibernate\.cfg\.xml", re.I),
                   re.compile(r"@Entity|@Table|SessionFactory", re.M)],
    # Python ORMs
    "django_orm": [re.compile(r"django\.db\.models|from django", re.I),
                   re.compile(r"class.*\(models\.Model\)", re.M)],
    "sqlalchemy": [re.compile(r"from sqlalchemy|import sqlalchemy", re.I),
                   re.compile(r"declarative_base|DeclarativeBase|Column\(", re.M)],
    # Node.js ORMs
    "sequelize":  [re.compile(r"\"sequelize\"", re.I),
                   re.compile(r"DataTypes\.|Model\.init|@Table", re.M)],
    "mongoose":   [re.compile(r"\"mongoose\"", re.I),
                   re.compile(r"new Schema\(|mongoose\.model\(", re.M)],
    "typeorm":    [re.compile(r"\"typeorm\"", re.I),
                   re.compile(r"@Entity\(\)|@Column\(|createConnection", re.M)],
    "prisma":     [re.compile(r"\"@prisma/client\"", re.I),
                   re.compile(r"prisma\.schema|PrismaClient", re.M)],
}

_API_SIGNALS: dict[str, list[re.Pattern]] = {
    "aspnet_core":  [re.compile(r"Microsoft\.AspNetCore", re.I)],
    "spring_mvc":   [re.compile(r"@RestController|@Controller|@GetMapping", re.M)],
    "django_rest":  [re.compile(r"rest_framework|APIView|ViewSet", re.I)],
    "fastapi":      [re.compile(r"from fastapi|import fastapi", re.I),
                     re.compile(r"@app\.get|@router\.", re.M)],
    "flask":        [re.compile(r"from flask|import flask", re.I),
                     re.compile(r"@app\.route|Blueprint\(", re.M)],
    "express":      [re.compile(r"require\(['\"]express", re.I),
                     re.compile(r"express\(\)|Router\(\)", re.M)],
}

_DB_SIGNALS: dict[str, list[re.Pattern]] = {
    "postgresql":   [re.compile(r"postgres|psycopg2|npgsql|pg\b", re.I)],
    "sqlserver":    [re.compile(r"sqlserver|mssql|System\.Data\.SqlClient", re.I)],
    "sqlite":       [re.compile(r"sqlite|\.db\"", re.I)],
    "mysql":        [re.compile(r"mysql|MariaDB", re.I)],
    "mongodb":      [re.compile(r"mongodb|MongoClient|mongoDB", re.I)],
    "redis":        [re.compile(r"redis|StackExchange\.Redis", re.I)],
}


def _scan_text(root: Path, extensions: list[str], max_files: int = 30) -> str:
    """Collect text from key config/package files for signal scanning."""
    collected: list[str] = []
    for ext in extensions:
        for p in list(root.rglob(ext))[:max_files]:
            try:
                collected.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(collected)


def _detect_languages(root: Path) -> list[Language]:
    langs: dict[Language, int] = {}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        suf = f.suffix.lower()
        if suf == ".cs":
            langs[Language.CSHARP] = langs.get(Language.CSHARP, 0) + 1
        elif suf == ".java":
            langs[Language.JAVA] = langs.get(Language.JAVA, 0) + 1
        elif suf == ".kt":
            langs[Language.KOTLIN] = langs.get(Language.KOTLIN, 0) + 1
        elif suf == ".py":
            langs[Language.PYTHON] = langs.get(Language.PYTHON, 0) + 1
        elif suf == ".ts":
            langs[Language.TYPESCRIPT] = langs.get(Language.TYPESCRIPT, 0) + 1
        elif suf == ".js":
            langs[Language.JAVASCRIPT] = langs.get(Language.JAVASCRIPT, 0) + 1

    return [k for k, _ in sorted(langs.items(), key=lambda x: -x[1])]


def detect_tech_context(project_root: str | Path) -> TechContext:
    """
    Scan a project directory and return a populated TechContext.
    Does NOT invoke any parsers — purely signal-based detection.
    """
    root  = Path(project_root)
    langs = _detect_languages(root)

    # Collect representative text for signal matching
    config_text = _scan_text(root, [
        "*.csproj", "pom.xml", "build.gradle", "build.gradle.kts",
        "requirements.txt", "pyproject.toml", "package.json",
        "*.config", "appsettings*.json", "application*.properties",
        "application*.yml", "Pipfile",
    ])
    # Also scan a sample of source files
    src_text = _scan_text(root, ["*.cs", "*.java", "*.py", "*.ts", "*.js"], max_files=20)
    full_text = config_text + "\n" + src_text

    # Detect ORM technologies
    orm_hits: list[Technology] = []
    for tech_key, patterns in _TECH_SIGNALS.items():
        hits = sum(1 for p in patterns if p.search(full_text))
        if hits >= 1:
            try:
                orm_hits.append(Technology(tech_key))
            except ValueError:
                pass

    # Detect API frameworks
    api_styles: list[str] = []
    for api_key, patterns in _API_SIGNALS.items():
        if any(p.search(full_text) for p in patterns):
            api_styles.append(api_key)

    # Detect databases (from content signals)
    db_types: list[str] = []
    for db_key, patterns in _DB_SIGNALS.items():
        if any(p.search(full_text) for p in patterns):
            db_types.append(db_key)

    # SQL file dialect detection
    sql_files = list(root.rglob("*.sql"))
    if sql_files:
        sql_sample = ""
        for sf in sql_files[:10]:
            try:
                sql_sample += sf.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                pass
        if re.search(r'\[dbo\]|\bGO\b|\bIDENTITY\b|nvarchar\b', sql_sample, re.I):
            if "sqlserver" not in db_types:
                db_types.insert(0, "sqlserver")
        elif re.search(r'\bSERIAL\b|\bBIGSERIAL\b|\bRETURNING\b|\$\d+\b', sql_sample, re.I):
            if "postgresql" not in db_types:
                db_types.insert(0, "postgresql")
        elif re.search(r'\bAUTO_INCREMENT\b|ENGINE\s*=\s*InnoDB', sql_sample, re.I):
            if "mysql" not in db_types:
                db_types.insert(0, "mysql")
        elif not db_types:
            db_types.append("sqlite")

    # Architecture hint
    architecture = "unknown"
    if Language.CSHARP in langs and orm_hits:
        architecture = "layered_monolith"
    elif len(langs) >= 2:
        architecture = "polyglot"

    # Collect package files found
    pkg_files: list[str] = []
    for ext in ["*.csproj", "pom.xml", "package.json", "requirements.txt", "build.gradle"]:
        pkg_files.extend(str(p) for p in list(root.rglob(ext))[:5])

    confidence = 0.5 + 0.1 * min(len(langs), 3) + 0.1 * min(len(orm_hits), 2)

    return TechContext(
        project_root  = str(root),
        languages     = langs,
        frameworks    = api_styles,
        orms          = orm_hits,
        api_styles    = api_styles,
        architecture  = architecture,
        db_types      = db_types,
        package_files = pkg_files,
        confidence    = min(confidence, 1.0),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AdapterRegistry:
    """
    Manages all registered adapters and orchestrates detection + extraction.
    """

    def __init__(self, extracted_dir: Optional[str | Path] = None) -> None:
        self._adapters: list[BaseAdapter] = []
        self._extracted_dir = Path(extracted_dir) if extracted_dir else None
        self._register_defaults()

    def _register_defaults(self) -> None:
        # Lazy imports to avoid circular deps
        try:
            from .dotnet.ef_adapter import EFCoreAdapter
            self.register(EFCoreAdapter())
        except Exception as e:
            print(f"[AdapterRegistry] EFCoreAdapter not loaded: {e}")

        try:
            from .java.spring_adapter import SpringAdapter
            self.register(SpringAdapter())
        except Exception as e:
            print(f"[AdapterRegistry] SpringAdapter not loaded: {e}")

        try:
            from .python.django_adapter import DjangoAdapter
            self.register(DjangoAdapter())
        except Exception as e:
            print(f"[AdapterRegistry] DjangoAdapter not loaded: {e}")

        try:
            from .nodejs.express_adapter import NodeJSAdapter
            self.register(NodeJSAdapter())
        except Exception as e:
            print(f"[AdapterRegistry] NodeJSAdapter not loaded: {e}")

        # Database-first SQL adapters
        _out = str(self._extracted_dir) if self._extracted_dir else None
        try:
            from .sqlserver.sql_adapter import SQLServerAdapter
            self.register(SQLServerAdapter(output_dir=_out))
        except Exception as e:
            print(f"[AdapterRegistry] SQLServerAdapter not loaded: {e}")

        try:
            from .postgresql.pg_adapter import PostgreSQLAdapter
            self.register(PostgreSQLAdapter(output_dir=_out))
        except Exception as e:
            print(f"[AdapterRegistry] PostgreSQLAdapter not loaded: {e}")

        try:
            from .mysql.mysql_adapter import MySQLAdapter
            self.register(MySQLAdapter(output_dir=_out))
        except Exception as e:
            print(f"[AdapterRegistry] MySQLAdapter not loaded: {e}")

        try:
            from .sqlite.sqlite_adapter import SQLiteAdapter
            self.register(SQLiteAdapter())
        except Exception as e:
            print(f"[AdapterRegistry] SQLiteAdapter not loaded: {e}")

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters.append(adapter)

    def detect(self, project_root: str | Path) -> TechContext:
        return detect_tech_context(project_root)

    def select_adapters(self, tech_context: TechContext) -> list[BaseAdapter]:
        selected = [a for a in self._adapters if a.can_handle(tech_context)]
        if not selected and self._adapters:
            # Fall back to the first adapter as a best-effort
            selected = [self._adapters[0]]
        return selected

    def run(self, project_root: str | Path) -> SemanticModel:
        """
        Full pipeline: detect technology → select adapters → extract → merge.
        Returns a unified SemanticModel.
        """
        tech_ctx = self.detect(project_root)
        print(f"[AdapterRegistry] Detected: langs={[l.value for l in tech_ctx.languages]} "
              f"orms={[o.value for o in tech_ctx.orms]} "
              f"arch={tech_ctx.architecture}")

        adapters = self.select_adapters(tech_ctx)
        print(f"[AdapterRegistry] Selected adapters: {[a.name for a in adapters]}")

        model = SemanticModel(project_root=str(project_root), tech_context=tech_ctx)

        for adapter in adapters:
            try:
                partial = adapter.extract(tech_ctx)
                model.merge(partial)
                print(f"[AdapterRegistry] {adapter.name}: "
                      f"{len(partial.entities)} entities, "
                      f"{len(partial.endpoints)} endpoints, "
                      f"{len(partial.relationships)} rels")
            except Exception as e:
                model.extraction_warnings.append(
                    f"Adapter {adapter.name} failed: {e}"
                )
                print(f"[AdapterRegistry] WARNING: {adapter.name} failed: {e}")

        return model
