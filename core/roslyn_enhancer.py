"""
Roslyn Semantic Enhancer — lightweight semantic symbol resolution.

Tree-sitter remains the PRIMARY parser.
This module provides SECONDARY semantic enhancement for cases where
AST structure alone cannot resolve type identity.

Capabilities (no actual Roslyn compiler required):
  - Namespace resolution via using directives + csproj assembly refs
  - Type binding (resolve short name → fully qualified name)
  - Inheritance chain flattening (resolve base class fields)
  - Interface implementation mapping

Does NOT do:
  - Full Roslyn workspace analysis
  - Dataflow analysis
  - Cross-file symbol binding (relies on entity catalog instead)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_USING_RE    = re.compile(r'^using\s+([\w.]+)\s*;', re.MULTILINE)
_NAMESPACE_RE = re.compile(r'^namespace\s+([\w.]+)', re.MULTILINE)
_CLASS_RE    = re.compile(r'(?:public|internal|private).*?class\s+(\w+)\s*(?::\s*([^{]+))?', re.MULTILINE)
_INTERFACE_RE = re.compile(r'interface\s+(\w+)\s*(?::\s*([^{]+))?', re.MULTILINE)


class RoslynEnhancer:
    """
    Lightweight semantic symbol resolution.

    Usage:
        enhancer = RoslynEnhancer()
        enhancer.load_project(project_root)
        fqn = enhancer.resolve_type("Order", context_file="src/Web/Pages/Order.cshtml.cs")
        chain = enhancer.inheritance_chain("BasketItem")
    """

    def __init__(self):
        self._namespace_map:   dict[str, str]        = {}   # short_name → namespace
        self._using_map:       dict[str, list[str]]  = {}   # file_path → [using namespaces]
        self._inheritance_map: dict[str, list[str]]  = {}   # class → [base classes]
        self._interface_map:   dict[str, list[str]]  = {}   # class → [interfaces]
        self._loaded = False

    # ------------------------------------------------------------------
    # Project loading
    # ------------------------------------------------------------------

    def load_project(self, project_root: str | Path) -> "RoslynEnhancer":
        """
        Walk all .cs files, extract:
          - namespace declarations
          - using directives per file
          - class inheritance chains
          - interface implementations
        """
        root = Path(project_root)
        _SKIP = {"bin", "obj", ".git", "node_modules", "Migrations"}

        for cs in root.rglob("*.cs"):
            if any(p in _SKIP for p in cs.parts):
                continue
            try:
                text = cs.read_text(encoding="utf-8", errors="ignore")
                self._index_file(str(cs), text)
            except OSError:
                pass

        self._loaded = True
        print(f"[RoslynEnhancer] Indexed {len(self._namespace_map)} types "
              f"from {len(self._using_map)} files")
        return self

    def _index_file(self, path: str, text: str) -> None:
        # Extract namespace
        ns_match = _NAMESPACE_RE.search(text)
        ns = ns_match.group(1) if ns_match else ""

        # Using directives
        usings = _USING_RE.findall(text)
        self._using_map[path] = usings

        # Classes
        for m in _CLASS_RE.finditer(text):
            class_name = m.group(1)
            if ns:
                self._namespace_map[class_name] = ns
            if m.group(2):
                bases = [b.strip().split("<")[0] for b in m.group(2).split(",")]
                ifaces = [b for b in bases if b.startswith("I") and len(b) > 1 and b[1].isupper()]
                base_cls = [b for b in bases if not (b.startswith("I") and b[1].isupper())]
                if base_cls:
                    self._inheritance_map[class_name] = base_cls
                if ifaces:
                    self._interface_map[class_name] = ifaces

    # ------------------------------------------------------------------
    # Type resolution
    # ------------------------------------------------------------------

    def resolve_type(self, short_name: str,
                     context_file: str = "") -> Optional[str]:
        """
        Resolve a short type name to its fully qualified name.

        Strategy:
        1. Direct namespace_map lookup
        2. Using directives from context file
        3. Return short_name if unresolvable (not an error)
        """
        ns = self._namespace_map.get(short_name)
        if ns:
            return f"{ns}.{short_name}"

        # Try using directives from context file
        if context_file and context_file in self._using_map:
            for using in self._using_map[context_file]:
                candidate = f"{using}.{short_name}"
                # Check if any class in that namespace exists
                for name, namespace in self._namespace_map.items():
                    if name == short_name and namespace.startswith(using):
                        return f"{namespace}.{short_name}"

        return short_name  # unresolved — return as-is

    def inheritance_chain(self, class_name: str,
                           max_depth: int = 5) -> list[str]:
        """Return the full inheritance chain for a class."""
        chain: list[str] = []
        current = class_name
        seen    = set()
        for _ in range(max_depth):
            bases = self._inheritance_map.get(current, [])
            if not bases or current in seen:
                break
            seen.add(current)
            chain.extend(bases)
            current = bases[0]  # follow primary base
        return chain

    def implements(self, class_name: str) -> list[str]:
        """Return interfaces implemented by a class (direct only)."""
        return self._interface_map.get(class_name, [])

    def classes_implementing(self, interface: str) -> list[str]:
        """Return all classes that implement a given interface."""
        return [cls for cls, ifaces in self._interface_map.items()
                if interface in ifaces]

    def namespace_of(self, class_name: str) -> Optional[str]:
        return self._namespace_map.get(class_name)

    def summary(self) -> dict:
        return {
            "loaded":          self._loaded,
            "types_indexed":   len(self._namespace_map),
            "files_indexed":   len(self._using_map),
            "with_inheritance": len(self._inheritance_map),
            "with_interfaces": len(self._interface_map),
        }
