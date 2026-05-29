"""
AST Parser Layer â€” uses Tree-sitter to parse C# source files.
Extracts classes, methods, properties, interfaces, and attributes
and stores raw parse results as JSON in memory/extracted/.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any

try:
    from tree_sitter_languages import get_language, get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


class ASTParser:
    def __init__(self, output_dir: str = "memory/extracted"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._parser = None
        self._language = None

        if TREE_SITTER_AVAILABLE:
            self._language = get_language("c_sharp")
            self._parser = get_parser("c_sharp")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_project(self, project_path: str) -> dict[str, Any]:
        """Walk a directory, parse every .cs file, return aggregated results."""
        root = Path(project_path)
        results: list[dict] = []

        cs_files = list(root.rglob("*.cs"))
        print(f"[ASTParser] Found {len(cs_files)} C# files in {project_path}")

        for cs_file in cs_files:
            try:
                file_result = self.parse_file(str(cs_file))
                results.append(file_result)
            except Exception as exc:
                print(f"[ASTParser] ERROR parsing {cs_file}: {exc}")

        aggregated = {
            "parsed_at": datetime.utcnow().isoformat(),
            "source_project": project_path,
            "file_count": len(cs_files),
            "files": results,
        }

        out_path = self.output_dir / "ast_raw.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(aggregated, f, indent=2)

        print(f"[ASTParser] Raw AST saved â†’ {out_path}")
        return aggregated

    def parse_file(self, file_path: str) -> dict[str, Any]:
        """Parse a single .cs file and return structured extraction."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="replace")

        if TREE_SITTER_AVAILABLE:
            return self._parse_with_tree_sitter(source, file_path)
        else:
            return self._parse_with_regex_fallback(source, file_path)

    # ------------------------------------------------------------------
    # Tree-sitter extraction
    # ------------------------------------------------------------------

    def _parse_with_tree_sitter(self, source: str, file_path: str) -> dict[str, Any]:
        tree = self._parser.parse(source.encode("utf-8"))
        root_node = tree.root_node

        classes = self._extract_classes(root_node, source)
        interfaces = self._extract_interfaces(root_node, source)
        enums = self._extract_enums(root_node, source)
        usings = self._extract_usings(root_node, source)
        namespace = self._extract_namespace(root_node, source)

        return {
            "file": file_path,
            "namespace": namespace,
            "usings": usings,
            "classes": classes,
            "interfaces": interfaces,
            "enums": enums,
            "parse_method": "tree-sitter",
        }

    def _extract_classes(self, root, source: str) -> list[dict]:
        classes = []
        for node in self._walk(root, "class_declaration"):
            name = self._child_text(node, "identifier", source)
            base_list = self._extract_base_list(node, source)
            attributes = self._extract_attributes(node, source)
            properties = self._extract_properties(node, source)
            methods = self._extract_methods(node, source)
            classes.append({
                "name": name,
                "base_types": base_list,
                "attributes": attributes,
                "properties": properties,
                "methods": methods,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })
        return classes

    def _extract_interfaces(self, root, source: str) -> list[dict]:
        interfaces = []
        for node in self._walk(root, "interface_declaration"):
            name = self._child_text(node, "identifier", source)
            members = self._extract_interface_members(node, source)
            interfaces.append({
                "name": name,
                "members": members,
                "start_line": node.start_point[0] + 1,
            })
        return interfaces

    def _extract_enums(self, root, source: str) -> list[dict]:
        enums = []
        for node in self._walk(root, "enum_declaration"):
            name = self._child_text(node, "identifier", source)
            enums.append({"name": name, "start_line": node.start_point[0] + 1})
        return enums

    def _extract_usings(self, root, source: str) -> list[str]:
        usings = []
        for node in self._walk(root, "using_directive"):
            text = source[node.start_byte:node.end_byte].strip()
            usings.append(text)
        return usings

    def _extract_namespace(self, root, source: str) -> str | None:
        for node in self._walk(root, "namespace_declaration"):
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    return source[child.start_byte:child.end_byte]
        return None

    def _extract_base_list(self, class_node, source: str) -> list[str]:
        bases = []
        for node in self._walk(class_node, "base_list"):
            for child in node.children:
                if child.type not in (":", ","):
                    text = source[child.start_byte:child.end_byte].strip()
                    if text:
                        bases.append(text)
        return bases

    def _extract_attributes(self, class_node, source: str) -> list[str]:
        attrs = []
        for node in self._walk(class_node, "attribute"):
            text = source[node.start_byte:node.end_byte].strip()
            attrs.append(text)
        return attrs

    def _extract_properties(self, class_node, source: str) -> list[dict]:
        props = []
        for node in self._walk(class_node, "property_declaration"):
            type_text = ""
            name_text = ""
            for child in node.children:
                if child.type in ("predefined_type", "identifier", "generic_name", "nullable_type"):
                    if not type_text:
                        type_text = source[child.start_byte:child.end_byte]
                    else:
                        name_text = source[child.start_byte:child.end_byte]
            props.append({
                "name": name_text or "unknown",
                "type": type_text,
                "line": node.start_point[0] + 1,
            })
        return props

    def _extract_methods(self, class_node, source: str) -> list[dict]:
        methods = []
        for node in self._walk(class_node, "method_declaration"):
            name = self._child_text(node, "identifier", source)
            attrs = self._extract_attributes(node, source)
            methods.append({
                "name": name,
                "attributes": attrs,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })
        return methods

    def _extract_interface_members(self, interface_node, source: str) -> list[str]:
        members = []
        for node in interface_node.children:
            if node.type in ("method_declaration", "property_declaration"):
                text = source[node.start_byte:node.end_byte].strip().split("\n")[0]
                members.append(text)
        return members

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _walk(self, node, target_type: str):
        """Yield all descendant nodes of a given type (depth-first)."""
        if node.type == target_type:
            yield node
        for child in node.children:
            yield from self._walk(child, target_type)

    def _child_text(self, node, child_type: str, source: str) -> str:
        for child in node.children:
            if child.type == child_type:
                return source[child.start_byte:child.end_byte]
        return "unknown"

    # ------------------------------------------------------------------
    # Regex fallback (no tree-sitter)
    # ------------------------------------------------------------------

    def _parse_with_regex_fallback(self, source: str, file_path: str) -> dict[str, Any]:
        import re

        classes = []
        for m in re.finditer(r"(?:public|internal|private)?\s*(?:partial\s+)?class\s+(\w+)", source):
            classes.append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

        interfaces = []
        for m in re.finditer(r"(?:public|internal)?\s*interface\s+(\w+)", source):
            interfaces.append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

        ns_match = re.search(r"namespace\s+([\w.]+)", source)
        namespace = ns_match.group(1) if ns_match else None

        return {
            "file": file_path,
            "namespace": namespace,
            "classes": classes,
            "interfaces": interfaces,
            "enums": [],
            "usings": [],
            "parse_method": "regex-fallback",
        }

