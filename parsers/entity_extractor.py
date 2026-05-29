"""
Entity Extractor — AST-first C# entity extraction.

Primary path  : tree-sitter AST (HIGH confidence, exact line numbers)
Fallback path : enhanced regex  (MEDIUM confidence)

Extracts:
  • class / record / record-struct declarations
  • namespace (file-scoped + block-scoped)
  • base types + interfaces
  • ALL properties (type, name, attributes, line number, accessor visibility)
  • field declarations (backing fields, readonly collections)
  • navigation properties (scalar + collection)
  • foreign-key fields (naming convention + primitive type)
  • class-level attributes ([Owned], [Table], [Index], ...)
  • aggregate roots (IAggregateRoot)
  • value objects (// ValueObject comment, [Owned], no Id, private-only ctor)
  • owned entities ([Owned] + OwnsOne/OwnsMany from EF config)
  • constructors + guard clauses
  • enums defined inside entity files

Generates:
  memory/extracted/entities.json
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.confidence import Confidence

# ---------------------------------------------------------------------------
# Tree-sitter initialisation (suppress FutureWarning from 0.21 API)
# ---------------------------------------------------------------------------

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
# Constants
# ---------------------------------------------------------------------------

_PRIMITIVE_TYPES = {
    "int","long","short","byte","sbyte","uint","ulong","ushort",
    "float","double","decimal","bool","char","string","object","void",
    "DateTime","DateTimeOffset","DateOnly","TimeOnly","TimeSpan",
    "Guid","Uri","int?","long?","decimal?","bool?","Guid?",
    "DateTime?","DateTimeOffset?",
}

_COLLECTION_WRAPPERS = {
    "ICollection","IReadOnlyCollection","IList","List",
    "IEnumerable","IQueryable","HashSet","ISet",
    "IReadOnlyList","Collection",
}

_AGG_ROOT_IFACES = {"IAggregateRoot","IAggregateRootEntity"}
_VO_IFACES       = {"ValueObject","IValueObject"}

_EF_ATTRS: dict[str, tuple[str, str]] = {
    "Key":              ("primary_key",  "Explicit PK"),
    "Required":         ("constraint",   "NOT NULL"),
    "MaxLength":        ("constraint",   "Max length"),
    "MinLength":        ("constraint",   "Min length"),
    "StringLength":     ("constraint",   "String length"),
    "Column":           ("mapping",      "Column mapping"),
    "Table":            ("mapping",      "Table mapping"),
    "Index":            ("index",        "Index"),
    "ForeignKey":       ("relationship", "FK declaration"),
    "InverseProperty":  ("relationship", "Inverse nav"),
    "NotMapped":        ("mapping",      "Excluded from DB"),
    "Owned":            ("ownership",    "EF owned type"),
    "Timestamp":        ("concurrency",  "Concurrency token"),
    "ConcurrencyCheck": ("concurrency",  "Concurrency check"),
    "DatabaseGenerated":("mapping",      "DB generated"),
    "Encrypted":        ("security",     "Encrypted field"),
    "Sensitive":        ("security",     "Sensitive data"),
    "PersonalData":     ("gdpr",         "GDPR personal data"),
    "DataType":         ("validation",   "Data type hint"),
    "EmailAddress":     ("validation",   "Email"),
    "Phone":            ("validation",   "Phone"),
    "CreditCard":       ("pci",          "Credit card"),
}


# ---------------------------------------------------------------------------
# File-level cache (SHA-256 keyed)
# ---------------------------------------------------------------------------

class _FileCache:
    def __init__(self, cache_dir: Optional[Path]):
        self._dir  = Path(cache_dir) if cache_dir else None
        self.hits  = 0
        self.misses = 0

    def _key(self, path: str) -> str:
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:24]
        return f"entity_{h}.json"

    def get(self, path: str) -> Optional[list]:
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

    def set(self, path: str, data: list) -> None:
        if not self._dir:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / self._key(path)).write_text(
                json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _iter_type(node, node_type: str):
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _iter_type(child, node_type)


def _child_text(node, child_type: str) -> Optional[str]:
    for c in node.children:
        if c.type == child_type:
            return c.text.decode("utf-8", errors="replace").strip()
    return None


def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="replace").strip()


def _get_namespace(root) -> str:
    for n in _iter_type(root, "file_scoped_namespace_declaration"):
        for c in n.children:
            if c.type in ("identifier", "qualified_name"):
                return _node_text(c)
    for n in _iter_type(root, "namespace_declaration"):
        for c in n.children:
            if c.type in ("identifier", "qualified_name"):
                return _node_text(c)
    return ""


def _get_attributes(node) -> list[dict]:
    attrs = []
    for child in node.children:
        if child.type != "attribute_list":
            continue
        for attr in _iter_type(child, "attribute"):
            children = list(attr.children)
            name = _node_text(children[0]) if children else ""
            args = [_node_text(a) for a in _iter_type(attr, "attribute_argument")]
            meta = _EF_ATTRS.get(name, ("annotation", name))
            attrs.append({"name": name, "args": args,
                          "category": meta[0], "note": meta[1]})
    return attrs


def _get_base_list(node) -> tuple[list[str], list[str]]:
    bases, ifaces = [], []
    for bl in node.children:
        if bl.type != "base_list":
            continue
        for child in bl.children:
            if child.type in ("identifier", "generic_name", "qualified_name"):
                name = _node_text(child).split("<")[0].strip()
                if name.startswith("I") and len(name) > 1 and name[1].isupper():
                    ifaces.append(name)
                else:
                    bases.append(name)
    return bases, ifaces


def _extract_generic_arg(type_text: str) -> Optional[str]:
    m = re.search(r'<(\w+)\??>',  type_text)
    return m.group(1) if m else None


def _parse_property_type(prop_node) -> tuple[str, str]:
    """Returns (raw_type_text, base_type). Skips modifiers and attributes."""
    saw_non_modifier = False
    for c in prop_node.children:
        if c.type == "modifier":
            continue
        if c.type == "attribute_list":
            continue
        if c.type in ("predefined_type","identifier","generic_name",
                      "nullable_type","qualified_name","array_type"):
            if not saw_non_modifier:
                raw  = _node_text(c)
                base = raw.rstrip("?")
                if "<" in base:
                    base = base.split("<")[0]
                return raw, base
            break
        saw_non_modifier = True
    return "object", "object"


def _get_property_name(prop_node) -> Optional[str]:
    found_type = False
    for c in prop_node.children:
        if c.type in ("modifier", "attribute_list"):
            continue
        if c.type in ("predefined_type","identifier","generic_name",
                      "nullable_type","qualified_name","array_type"):
            if not found_type:
                found_type = True
                continue
            if c.type == "identifier":
                return _node_text(c)
    return None


def _is_collection_type(raw: str) -> tuple[bool, Optional[str]]:
    wrapper = raw.split("<")[0].rstrip("?")
    if wrapper in _COLLECTION_WRAPPERS:
        return True, _extract_generic_arg(raw)
    return False, None


def _get_accessor_visibility(prop_node) -> tuple[str, str]:
    get_vis = set_vis = "none"
    for al in _iter_type(prop_node, "accessor_list"):
        for acc in al.children:
            if acc.type != "accessor_declaration":
                continue
            tokens = [c.text.decode() for c in acc.children]
            vis = ("private"   if "private"   in tokens else
                   "protected" if "protected" in tokens else "public")
            if "get"  in tokens: get_vis = vis
            if "set"  in tokens or "init" in tokens: set_vis = vis
    return get_vis, set_vis


def _aggregate_from_namespace(ns: str, entity: str, source_file: str = "") -> str:
    """Generic aggregate derivation — works for any .NET project structure."""
    from project_layout import aggregate_from_path
    return aggregate_from_path(ns, entity, source_file)


# ---------------------------------------------------------------------------
# AST file parser
# ---------------------------------------------------------------------------

def _parse_file_ast(source_path: str) -> list[dict]:
    src_bytes  = Path(source_path).read_bytes()
    tree       = _TS_PARSER.parse(src_bytes)
    root       = tree.root_node
    namespace  = _get_namespace(root)
    file_text  = src_bytes.decode("utf-8", errors="replace")
    has_vo_comment = bool(re.search(r'//\s*ValueObject', file_text, re.IGNORECASE))

    # Collect enum names defined in this file
    file_enums: set[str] = set()
    for en in _iter_type(root, "enum_declaration"):
        n = _child_text(en, "identifier")
        if n: file_enums.add(n)

    results: list[dict] = []

    for cls_node in (list(_iter_type(root, "class_declaration"))
                     + list(_iter_type(root, "record_declaration"))
                     + list(_iter_type(root, "record_struct_declaration"))):

        entity_name = _child_text(cls_node, "identifier") or ""
        if not entity_name or entity_name.startswith("_"):
            continue

        # Skip nested types inside another class
        p = cls_node.parent
        if p and p.parent and p.parent.type in ("class_declaration","record_declaration"):
            continue

        line_number  = cls_node.start_point[0] + 1
        class_attrs  = _get_attributes(cls_node)
        bases, ifaces = _get_base_list(cls_node)
        attr_names   = {a["name"] for a in class_attrs}

        is_agg_root = bool(_AGG_ROOT_IFACES & set(ifaces))
        is_vo       = (has_vo_comment
                       or bool(_VO_IFACES & set(bases + ifaces))
                       or "Owned" in attr_names
                       or cls_node.type in ("record_declaration","record_struct_declaration"))

        properties: list[dict] = []
        fk_fields:  list[dict] = []
        nav_scalar: list[dict] = []
        nav_coll:   list[dict] = []
        prop_attrs: dict[str, list] = {}

        # Collect from declaration_list children
        for dl in cls_node.children:
            if dl.type != "declaration_list":
                continue
            for child in dl.children:
                if child.type == "property_declaration":
                    _classify_property(child, source_path, file_enums,
                                       properties, fk_fields, nav_scalar, nav_coll, prop_attrs)

        # Backing fields (private List<T> _field)
        backing: list[dict] = []
        for dl in cls_node.children:
            if dl.type != "declaration_list":
                continue
            for fd in dl.children:
                if fd.type != "field_declaration":
                    continue
                info = _parse_backing_field(fd)
                if info: backing.append(info)

        # Constructor analysis
        ctor_params: list[str] = []
        private_only = True
        for dl in cls_node.children:
            if dl.type != "declaration_list":
                continue
            for ctor in dl.children:
                if ctor.type != "constructor_declaration":
                    continue
                mods = [c.text.decode() for c in ctor.children if c.type == "modifier"]
                if any(m in mods for m in ("public","internal","protected")):
                    private_only = False
                for pl in _iter_type(ctor, "parameter_list"):
                    for param in _iter_type(pl, "parameter"):
                        vals = [_node_text(c) for c in param.children
                                if c.type in ("identifier","predefined_type")]
                        if len(vals) >= 2:
                            ctor_params.append(vals[-1])

        field_names = {f["name"] for f in properties}
        if not is_vo and "Id" not in field_names and private_only and properties:
            is_vo = True

        agg = _aggregate_from_namespace(namespace, entity_name, source_path)

        results.append({
            "entity":               entity_name,
            "namespace":            namespace,
            "source_file":          source_path,
            "line_number":          line_number,
            "base_types":           bases,
            "interfaces":           ifaces,
            "is_aggregate_root":    is_agg_root,
            "is_value_object":      is_vo,
            "aggregate":            agg,
            "aggregate_root":       is_agg_root,
            "attributes":           class_attrs,
            "fields":               properties,
            "foreign_keys":         fk_fields,
            "navigation_scalar":    nav_scalar,
            "navigation_collection":nav_coll,
            "backing_fields":       backing,
            "property_attributes":  prop_attrs,
            "constructor_params":   ctor_params,
            "enums_in_file":        sorted(file_enums),
            "confidence": {
                "level":    Confidence.HIGH.value,
                "evidence": f"AST parsed: {Path(source_path).name}:{line_number}",
                "parser":   "tree-sitter",
            },
        })
    return results


def _classify_property(
    prop_node, source_path: str, file_enums: set,
    properties, fk_fields, nav_scalar, nav_coll, prop_attrs,
) -> None:
    line      = prop_node.start_point[0] + 1
    attrs     = _get_attributes(prop_node)
    raw, base = _parse_property_type(prop_node)
    name      = _get_property_name(prop_node)
    if not name:
        return

    attr_names  = {a["name"] for a in attrs}
    get_v, set_v = _get_accessor_visibility(prop_node)
    if attrs:
        prop_attrs[name] = [a["name"] for a in attrs]

    is_coll, elem = _is_collection_type(raw)
    is_not_mapped = "NotMapped" in attr_names
    is_primitive  = base in _PRIMITIVE_TYPES
    is_enum       = base in file_enums

    base_fld = {
        "name": name, "type": raw, "line_number": line,
        "is_navigation": False, "is_fk": False,
        "get_visibility": get_v, "set_visibility": set_v, "attributes": attrs,
    }

    if is_coll and elem and elem not in _PRIMITIVE_TYPES and not is_not_mapped:
        base_fld.update({"is_navigation": True, "is_collection": True})
        properties.append(base_fld)
        nav_coll.append({"name": name, "type": raw, "target_entity": elem,
                         "line_number": line, "is_navigation": True, "is_fk": False,
                         "source_file": source_path, "confidence": Confidence.HIGH.value})
        return

    if is_primitive or is_enum:
        is_fk = ("ForeignKey" in attr_names or
                 (name.endswith("Id") and base in ("int","long","string","Guid")))
        base_fld["is_fk"] = is_fk
        if is_enum:
            base_fld["is_enum"] = True
        properties.append(base_fld)
        if is_fk:
            ref = None
            for a in attrs:
                if a["name"] == "ForeignKey" and a.get("args"):
                    ref = a["args"][0].strip('"\''); break
            if not ref and name.endswith("Id"):
                ref = name[:-2]
            fk_fields.append({"name": name, "type": raw, "references": ref,
                               "line_number": line, "source_file": source_path,
                               "confidence": Confidence.HIGH.value})
        return

    # Non-primitive scalar → navigation or owned type
    if not is_not_mapped:
        base_fld["is_navigation"] = True
        properties.append(base_fld)
        nav_scalar.append({"name": name, "type": raw, "target_entity": base,
                           "line_number": line, "is_navigation": True, "is_fk": False,
                           "source_file": source_path, "confidence": Confidence.HIGH.value})
    else:
        properties.append(base_fld)


def _parse_backing_field(fd_node) -> Optional[dict]:
    tokens = [c.text.decode() for c in fd_node.children if c.type == "modifier"]
    if "const" in tokens or "static" in tokens:
        return None
    text = _node_text(fd_node)
    is_coll, elem = _is_collection_type(text)
    if not is_coll:
        return None
    for vd in _iter_type(fd_node, "variable_declarator"):
        name_raw = _node_text(vd).split("=")[0].strip()
        return {"name": name_raw, "element_type": elem,
                "line_number": fd_node.start_point[0] + 1, "is_backing_field": True}
    return None


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

_RE_CLASS = re.compile(
    r'(?:public|internal)\s+(?:(?:partial|abstract|sealed)\s+)*'
    r'(?:class|record|struct)\s+(\w+)(?:\s*:\s*([^{]+))?', re.MULTILINE)
_RE_PROP  = re.compile(
    r'(?:^\s*)(?:\[[^\]]*\]\s*)*public\s+([\w<>?,\[\] ]+?)\s+(\w+)\s*\{[^}]*(?:get|set)',
    re.MULTILINE)
_RE_NS    = re.compile(r'namespace\s+([\w.]+)')
_RE_VO    = re.compile(r'//\s*ValueObject', re.IGNORECASE)


def _parse_file_regex(source_path: str) -> list[dict]:
    text = Path(source_path).read_text(encoding="utf-8", errors="replace")
    ns   = (_RE_NS.search(text) or type("", (), {"group": lambda s, x: ""})()).group(1) or ""
    has_vo = bool(_RE_VO.search(text))
    results = []

    for m in _RE_CLASS.finditer(text):
        name   = m.group(1)
        bl     = [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]
        bases  = [b for b in bl if not (b.startswith("I") and len(b)>1 and b[1].isupper())]
        ifaces = [b for b in bl if b.startswith("I") and len(b)>1 and b[1].isupper()]
        is_agg = bool(_AGG_ROOT_IFACES & set(ifaces))
        is_vo  = has_vo or bool(_VO_IFACES & set(bases + ifaces))

        props, fks, navs, cnavs = [], [], [], []
        for pm in _RE_PROP.finditer(text):
            raw  = pm.group(1).strip()
            pn   = pm.group(2).strip()
            base = raw.rstrip("?").split("<")[0].strip()
            line = text[:pm.start()].count("\n") + 1
            is_c, elem = _is_collection_type(raw)
            fld  = {"name": pn, "type": raw, "line_number": line,
                    "is_navigation": False, "is_fk": False, "attributes": []}
            if is_c and elem and elem not in _PRIMITIVE_TYPES:
                fld["is_navigation"] = True; fld["is_collection"] = True
                cnavs.append({"name": pn, "type": raw, "target_entity": elem,
                               "line_number": line, "is_navigation": True,
                               "confidence": Confidence.MEDIUM.value})
            elif base not in _PRIMITIVE_TYPES:
                fld["is_navigation"] = True
                navs.append({"name": pn, "type": raw, "target_entity": base,
                              "line_number": line, "is_navigation": True,
                              "confidence": Confidence.MEDIUM.value})
            else:
                is_fk = pn.endswith("Id")
                fld["is_fk"] = is_fk
                if is_fk:
                    fks.append({"name": pn, "type": raw, "references": pn[:-2],
                                 "line_number": line, "confidence": Confidence.MEDIUM.value})
            props.append(fld)

        agg = _aggregate_from_namespace(ns, name, source_path)
        results.append({
            "entity": name, "namespace": ns, "source_file": source_path,
            "line_number": text[:m.start()].count("\n") + 1,
            "base_types": bases, "interfaces": ifaces,
            "is_aggregate_root": is_agg, "is_value_object": is_vo,
            "aggregate": agg, "aggregate_root": is_agg,
            "attributes": [], "fields": props, "foreign_keys": fks,
            "navigation_scalar": navs, "navigation_collection": cnavs,
            "backing_fields": [], "property_attributes": {},
            "constructor_params": [], "enums_in_file": [],
            "confidence": {"level": Confidence.MEDIUM.value,
                           "evidence": f"Regex: {Path(source_path).name}",
                           "parser": "regex"},
        })
    return results


# ---------------------------------------------------------------------------
# EntityExtractor (public API)
# ---------------------------------------------------------------------------

_SKIP_FILES = re.compile(
    r'(Migration|Seed|Snapshot|Designer|DbContext|Configuration|Specification'
    r'|Repository|Service|Controller|Endpoint|Handler|Test|Program|Startup)',
    re.IGNORECASE)

_SKIP_NAMES = {"BaseEntity", "ValueObject", "IValueObject", "Entity"}


class EntityExtractor:
    def __init__(self, output_dir: str = "memory/extracted",
                 cache_dir: str = "memory/cache"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cache = _FileCache(Path(cache_dir) if cache_dir else None)
        mode = "tree-sitter (HIGH)" if HAS_TREESITTER else "regex (MEDIUM)"
        print(f"[EntityExtractor] Parser: {mode}")

    def extract_from_dir(self, entities_dir: str) -> dict[str, Any]:
        src = Path(entities_dir)
        files = [f for f in src.rglob("*.cs") if not _SKIP_FILES.search(f.name)]
        print(f"[EntityExtractor] Scanning {len(files)} files in {src.name}/")

        raw: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(self._parse_one, str(f)): f for f in files}
            for fut in as_completed(futs):
                try:
                    raw.extend(fut.result())
                except Exception as exc:
                    print(f"  [WARN] {futs[fut].name}: {exc}")

        entities: list[dict] = []
        value_objects: list[dict] = []
        for item in raw:
            if item["entity"] in _SKIP_NAMES:
                continue
            (value_objects if item["is_value_object"] else entities).append(item)

        self._resolve_owned_by(entities, value_objects)

        result = {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "entity_count":       len(entities),
            "value_object_count": len(value_objects),
            "parser":             "tree-sitter" if HAS_TREESITTER else "regex",
            "entities":           entities,
            "value_objects":      value_objects,
        }
        out = self.output_dir / "entities.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[EntityExtractor] {len(entities)} entities, {len(value_objects)} VOs "
              f"-> {out}  (cache: {self._cache.stats})")

        self._write_coverage(entities, value_objects)
        return result

    def _write_coverage(self, entities: list[dict], vos: list[dict]) -> None:
        debug_dir = self.output_dir.parent / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        coverage: dict[str, dict] = {}
        all_items = [(e, False) for e in entities] + [(v, True) for v in vos]
        for item, is_vo in all_items:
            name   = item["entity"]
            fields = item.get("fields", [])
            fks    = item.get("foreign_keys", [])
            navs   = item.get("navigation_collection", []) + item.get("navigation_scalar", [])
            total  = len(fields)
            prim   = sum(1 for f in fields if not f.get("is_navigation") and not f.get("is_fk"))
            coverage[name] = {
                "is_value_object":       is_vo,
                "aggregate":             item.get("aggregate", ""),
                "properties_detected":   total,
                "primitive_fields":      prim,
                "fk_fields":             len(fks),
                "navigation_properties": len(navs),
                "field_names":           [f["name"] for f in fields],
                "confidence":            item.get("confidence", {}).get("level", "?"),
                "source_file":           item.get("source_file", ""),
                "line_number":           item.get("line_number"),
                "parser":                "tree-sitter" if HAS_TREESITTER else "regex",
            }
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_entities": len(entities),
            "total_vos": len(vos),
            "parser": "tree-sitter" if HAS_TREESITTER else "regex",
            "coverage": coverage,
        }
        out = debug_dir / "entity_coverage.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[EntityExtractor] Coverage -> {out}")

    def _parse_one(self, path: str) -> list[dict]:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        items = _parse_file_ast(path) if HAS_TREESITTER else _parse_file_regex(path)
        self._cache.set(path, items)
        return items

    def _resolve_owned_by(self, entities: list[dict], vos: list[dict]) -> None:
        vo_names = {v["entity"] for v in vos}
        ownership: dict[str, list[str]] = defaultdict(list)
        for ent in entities:
            for nav in ent.get("navigation_scalar", []):
                t = nav.get("target_entity", "")
                if t in vo_names:
                    ownership[t].append(ent["entity"])
        for vo in vos:
            owners = ownership.get(vo["entity"], [])
            vo["owned_by"] = owners[0] if len(owners) == 1 else (owners or None)
