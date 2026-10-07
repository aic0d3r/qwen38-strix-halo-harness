#!/usr/bin/env python3
"""Optional tree-sitter backend for structure_map.

This module is imported LAZILY by ``structure_map.summarize_code_source`` only
when ``is_available()`` is true and the file's language is in the supported
set. It never imports ``tree_sitter`` at module load time, so importing
``structure_map`` (and therefore the hot read-cache path) never requires
tree-sitter to be installed.

Two tree-sitter packaging eras are supported:
  * ``tree_sitter_languages`` -- the older bundled-grammars package.
  * ``tree_sitter`` + ``tree_sitter_<lang>`` -- the 0.22+ per-language
    grammar packages with the new ``Language`` / ``Parser`` API.

Both paths are probed at runtime; whichever is present wins. When neither is
present, ``is_available()`` returns False and ``summarize_code_source`` falls
through to the existing ast/regex/digest path unchanged.

This module is stdlib-only at import time (no top-level tree_sitter import),
uses no subprocess, no network, and no resident state. It produces
``StructureMapResult`` instances with the same keys as the stdlib path so
``read_cache.py`` consumes them unchanged.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import List, Optional, Tuple

# Re-use the canonical result shape and helpers so the output is byte-for-byte
# compatible with the stdlib path. Imported at module load (these are stdlib
# dataclasses / pure functions, NOT tree-sitter).
from structure_map import (
    MAX_REPLACEMENT_CHARS,
    MAX_SKELETON_CLASSES,
    MAX_SKELETON_METHODS_PER_CLASS,
    MAX_SKELETON_TOP_LEVEL_FUNCTIONS,
    StructureMapResult,
    detect_structure_language,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FLAG_ENV = "TOKEN_OPTIMIZER_STRUCTURE_MAP_TREESITTER"

# Suffix -> tree-sitter language name. These are the languages the backend
# can produce real structure for (over and above the stdlib Python/JS-TS path).
# The stdlib path remains the default for .py / .js / .ts; tree-sitter is
# additive for the rest.
SUFFIX_TO_TS_LANG = {
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".cs": "c_sharp",
    ".dart": "dart",
    ".elixir": "elixir",
    ".ex": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".cljc": "clojure",
    ".edn": "clojure",
    ".zig": "zig",
    ".nim": "nim",
    ".sql": "sql",
    ".graphql": "graphql",
    ".proto": "proto",
    ".hcl": "hcl",
    ".tf": "hcl",
}

# Node types that represent a function/method definition, per language family.
# We keep this conservative: only unambiguous definition nodes.
_FUNCTION_NODE_TYPES = {
    "function_definition",        # Python, Go, Ruby, Bash, PHP, Lua, Zig, Nim
    "function_declaration",       # C, Java, Kotlin, Swift, Dart, JS/TS
    "function_item",              # Rust
    "method_definition",          # Ruby, PHP, Python (class methods)
    "method_declaration",         # Java, Kotlin, Swift, Dart
    "constructor_declaration",    # Java, C#, Dart
    "function_signature",         # C++
    "function_declarator",        # C++
    "func_literal",               # Go (anonymous)
    "arrow_function",             # JS/TS
    "function_expression",        # JS/TS
    "generator_function_declaration",  # JS/TS
    "function_specification",     # Ada
    "proc_specification",         # Ada
}

_CLASS_NODE_TYPES = {
    "class_definition",           # Python, Ruby, PHP, Lua
    "class_declaration",          # Java, Kotlin, Swift, Dart, JS/TS, C#
    "class_specifier",            # C++
    "struct_specifier",           # C, C++
    "struct_item",                # Rust
    "union_specifier",            # C, C++
    "enum_specifier",             # C, C++
    "enum_item",                  # Rust
    "interface_declaration",      # Java, Kotlin, Swift, Dart, JS/TS, C#
    "interface_definition",       # PHP, Ruby
    "trait_declaration",          # Kotlin, PHP, Dart
    "impl_item",                  # Rust
    "object",                     # Scala
    "record_declaration",         # Kotlin data class, Swift
    "extension_declaration",      # Swift
    "package_declaration",        # Java, Kotlin
    # namespace_declaration is intentionally NOT a class type: it is a CONTAINER
    # of classes (see _NAMESPACE_CONTAINER_NODE_TYPES). Treating it as a leaf
    # class would drop every class nested inside a C#/C++ namespace (fix-5b).
}

# Brace-body languages nest a class's members under an intermediate body node,
# so methods are GRANDCHILDREN of the class node, not direct children. The
# method collector descends exactly one such body level (fix-5a).
_CLASS_BODY_NODE_TYPES = {
    "class_body",              # Java, Kotlin, C#, Groovy
    "declaration_list",        # C++, C#, namespaces
    "field_declaration_list",  # C / C++ struct / union
    "enum_body",               # Java, C#
    "interface_body",          # Java, C#
    "struct_body",             # some grammars
    "enum_class_body",         # Java enum constants with bodies
}

# Namespace containers hold classes/functions (directly or under a body node)
# rather than being definitions themselves. The walker descends THROUGH them
# after emitting a compact label, so classes inside a C#/C++ namespace survive.
_NAMESPACE_CONTAINER_NODE_TYPES = {
    "namespace_declaration",   # C#, C++
}

_IMPORT_NODE_TYPES = {
    "import_statement",           # Python, Go, Java, Kotlin
    "import_declaration",         # JS/TS, Java, C, C++, Swift, Dart
    "import_from_statement",      # Python
    "import_spec",                # Go
    "use_declaration",            # Rust
    "use_statement",              # Zig, Clojure
    "require_statement",          # Ruby, Lua
    "include_directive",          # C, C++
    "package_clause",             # Go
    "extern_import",              # Zig
    "using_directive",            # C# (the actual `using` import node)
    # NOTE: namespace_declaration is deliberately NOT here. It also appears in
    # _CLASS_NODE_TYPES (C#/C++ namespaces), and imports are matched first; if
    # it were treated as an import the walker would `return` without descending
    # and every C#/C++ namespace (and the classes inside it) would be dropped
    # (fix-5b). C#'s import is `using_directive`, not `namespace_declaration`.
}

# Per-language import-node text prefixes to extract a compact import label.
_IMPORT_TEXT_PREFIXES = {
    "import_statement": "import",
    "import_from_statement": "from",
    "import_declaration": "import",
    "import_spec": "import",
    "use_declaration": "use",
    "use_statement": "use",
    "require_statement": "require",
    "include_directive": "include",
    "package_clause": "package",
    "extern_import": "extern",
    "using_directive": "using",
}

_MAX_IMPORTS = 8
_MAX_CLASSES = MAX_SKELETON_CLASSES
_MAX_METHODS_PER_CLASS = MAX_SKELETON_METHODS_PER_CLASS
_MAX_FUNCTIONS = MAX_SKELETON_TOP_LEVEL_FUNCTIONS


# ---------------------------------------------------------------------------
# Availability probe (lazy, cached on first call)
# ---------------------------------------------------------------------------

_avail_cache: Optional[bool] = None
_avail_reason: str = ""


def _flag_enabled() -> bool:
    """The backend is opt-in: the env flag must be set to a truthy value
    (default off) so the hot path never attempts tree-sitter unless a user
    explicitly enables it."""
    raw = os.environ.get(FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _probe_runtime() -> Tuple[bool, str]:
    """Probe for a usable tree-sitter runtime + at least one grammar.

    Returns (ok, reason). Tries ``tree_sitter_languages`` first (bundled),
    then the per-language ``tree_sitter_<lang>`` packages. Never raises.
    """
    try:
        import tree_sitter  # noqa: F401
    except Exception:
        return False, "tree_sitter package not installed"
    # Try the bundled-grammars package first.
    try:
        import tree_sitter_languages  # noqa: F401
        return True, "tree_sitter_languages"
    except Exception:
        pass
    # Fall back to any per-language grammar package.
    for lang in SUFFIX_TO_TS_LANG.values():
        pkg = f"tree_sitter_{lang}"
        try:
            __import__(pkg)
            return True, f"per-language:{pkg}"
        except Exception:
            continue
    return False, "no grammar packages installed"


def is_available() -> bool:
    """Return True when the tree-sitter backend is enabled and usable.

    Cached after the first call. The flag default-off means the hot path
    never attempts tree-sitter unless opted in via ``TOKEN_OPTIMIZER_
    STRUCTURE_MAP_TREESITTER=1``.
    """
    global _avail_cache, _avail_reason
    if _avail_cache is not None:
        return _avail_cache
    if not _flag_enabled():
        _avail_cache = False
        _avail_reason = "flag disabled (default off)"
        return False
    ok, reason = _probe_runtime()
    _avail_cache = ok
    _avail_reason = reason
    return ok


def supported_suffixes() -> Tuple[str, ...]:
    """Return the file suffixes the backend can produce real structure for."""
    return tuple(SUFFIX_TO_TS_LANG.keys())


# ---------------------------------------------------------------------------
# Grammar / parser construction (lazy, per-language)
# ---------------------------------------------------------------------------

def _load_language(lang: str) -> Optional[object]:
    """Load a tree-sitter Language for ``lang`` from whichever packaging era
    is present. Returns None on any failure (caller falls through to digest)."""
    # Era 1: tree_sitter_languages (bundled). Older API: tsl.get_language(lang).
    try:
        import tree_sitter_languages as tsl
        get_lang = getattr(tsl, "get_language", None)
        if get_lang is not None:
            try:
                return get_lang(lang)
            except Exception:
                pass
    except Exception:
        pass
    # Era 2: per-language package tree_sitter_<lang>.
    pkg = f"tree_sitter_{lang}"
    try:
        mod = __import__(pkg)
    except Exception:
        return None
    # 0.22+ API: mod.language() returns a Language capsule; Language(mod.language())
    # for the bindings-to-capsule bridge.
    try:
        from tree_sitter import Language
        lang_fn = getattr(mod, "language", None)
        if lang_fn is not None:
            try:
                return Language(lang_fn())
            except Exception:
                # Some bindings return a Language directly from language().
                candidate = lang_fn()
                if isinstance(candidate, Language):
                    return candidate
    except Exception:
        pass
    return None


def _make_parser(language: object) -> Optional[object]:
    """Construct a Parser bound to ``language``. Returns None on failure."""
    try:
        from tree_sitter import Parser
        # 0.22+ API: Parser(language)
        try:
            return Parser(language)
        except Exception:
            pass
        # 0.20-era API: Parser(); parser.set_language(language)
        try:
            parser = Parser()
            set_lang = getattr(parser, "set_language", None)
            if set_lang is not None:
                set_lang(language)
                return parser
        except Exception:
            pass
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Tree walking
# ---------------------------------------------------------------------------

def _node_text(node: object, source_bytes: bytes) -> str:
    try:
        start = node.start_byte
        end = node.end_byte
        return source_bytes[start:end].decode("utf-8", errors="replace")
    except Exception:
        return ""


def _node_name(node: object, source_bytes: bytes) -> str:
    """Extract a definition's name from a tree-sitter node.

    Most grammar node types carry the name as the first named child of type
    ``identifier`` / ``type_identifier`` / ``property_identifier``. Fall back
    to the first line of the node text, truncated.
    """
    try:
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "property_identifier",
                              "field_identifier", "type_name", "name"):
                return _node_text(child, source_bytes)
    except Exception:
        pass
    text = _node_text(node, source_bytes)
    first_line = text.split("\n", 1)[0].strip()
    return first_line[:80] if first_line else "<anonymous>"


def _node_lineno(node: object) -> int:
    try:
        return int(node.start_point[0]) + 1
    except Exception:
        return 0


def _is_definition(node: object, kind_set: frozenset) -> bool:
    try:
        return node.type in kind_set
    except Exception:
        return False


def _walk_tree(
    root: object,
    source_bytes: bytes,
) -> Tuple[List[str], List[dict], List[dict]]:
    """Walk the tree-sitter tree collecting imports, classes, functions.

    Returns (imports, classes, functions) where each class/function is a dict
    with keys: name, signature, lineno, methods (classes only). Methods are
    collected by descending one level into a class body.
    """
    imports: List[str] = []
    classes: List[dict] = []
    functions: List[dict] = []

    def _shorten_import(text: str) -> str:
        first_line = text.split("\n", 1)[0].strip()
        return first_line[:120] if first_line else ""

    def _method_entry(node: object) -> dict:
        m_name = _node_name(node, source_bytes)
        return {
            "name": m_name,
            "signature": _short_signature(m_name, _node_text(node, source_bytes)),
            "lineno": _node_lineno(node),
        }

    def _collect_methods(class_node: object) -> List[dict]:
        """Collect method definitions of a class, handling both grammar shapes:
        direct children (Python-family) AND grandchildren nested under an
        intermediate body node (Java/C#/C++/Kotlin/Swift brace bodies, fix-5a).
        """
        methods: List[dict] = []
        try:
            for child in class_node.children:
                if _is_definition(child, _FUNCTION_NODE_TYPES):
                    methods.append(_method_entry(child))
                elif child.type in _CLASS_BODY_NODE_TYPES:
                    for gchild in child.children:
                        if _is_definition(gchild, _FUNCTION_NODE_TYPES):
                            methods.append(_method_entry(gchild))
        except Exception:
            pass
        return methods

    def _visit(node: object) -> None:
        try:
            ntype = node.type
        except Exception:
            ntype = ""
        if ntype in _NAMESPACE_CONTAINER_NODE_TYPES:
            # A namespace is a container, not a definition: emit a compact label
            # then DESCEND so the classes/functions inside it are not dropped.
            name = _node_name(node, source_bytes).split("{", 1)[0].strip()
            if name:
                imports.append(f"namespace {name}")
            try:
                for child in node.children:
                    _visit(child)
            except Exception:
                pass
            return
        if ntype in _IMPORT_NODE_TYPES:
            label = _IMPORT_TEXT_PREFIXES.get(ntype, ntype)
            text = _shorten_import(_node_text(node, source_bytes))
            if text:
                imports.append(f"{label} {text}" if not text.startswith(label) else text)
            # Don't descend into imports.
            return
        if ntype in _CLASS_NODE_TYPES:
            name = _node_name(node, source_bytes)
            lineno = _node_lineno(node)
            methods = _collect_methods(node)
            classes.append({
                "name": name,
                "signature": f"class {name}",
                "lineno": lineno,
                "methods": methods,
            })
            # Do NOT descend further (no nested classes for now).
            return
        if ntype in _FUNCTION_NODE_TYPES:
            name = _node_name(node, source_bytes)
            lineno = _node_lineno(node)
            sig = _short_signature(name, _node_text(node, source_bytes))
            functions.append({"name": name, "signature": sig, "lineno": lineno})
            return
        # Recurse into unnamed children to find definitions at any depth.
        try:
            for child in node.children:
                _visit(child)
        except Exception:
            pass

    try:
        _visit(root)
    except Exception:
        pass
    return imports, classes, functions


def _short_signature(name: str, node_text: str) -> str:
    """Build a compact one-line signature from a definition node's text."""
    first_line = node_text.split("\n", 1)[0].strip()
    if not first_line:
        return name
    # Truncate at the first ``{`` or ``:`` to drop the body opener.
    for sep in ("{", ":", "=>"):
        idx = first_line.find(sep)
        if idx > 0:
            first_line = first_line[:idx].rstrip()
    if len(first_line) > 120:
        first_line = first_line[:117] + "..."
    return first_line


# ---------------------------------------------------------------------------
# Rendering (mirrors structure_map._render_skeleton shape)
# ---------------------------------------------------------------------------

def _render_skeleton(
    *,
    language: str,
    line_count: int,
    imports: List[str],
    classes: List[dict],
    functions: List[dict],
) -> str:
    sections: List[str] = [f"{language} skeleton", f"lines: {line_count}"]

    if imports:
        sections.append(f"imports ({len(imports)}):")
        for item in imports[:_MAX_IMPORTS]:
            sections.append(f"  - {item}")

    if classes:
        sections.append(f"classes ({len(classes)}):")
        for cls in classes[:_MAX_CLASSES]:
            sections.append(f"  - {cls['signature']} @ L{cls['lineno']}")
            if cls["methods"]:
                sections.append("    methods:")
                for method in cls["methods"][:_MAX_METHODS_PER_CLASS]:
                    sections.append(f"      - {method['signature']} @ L{method['lineno']}")

    if functions:
        sections.append(f"functions ({len(functions)}):")
        for func in functions[:_MAX_FUNCTIONS]:
            sections.append(f"  - {func['signature']} @ L{func['lineno']}")

    return "\n".join(section for section in sections if section)


def _fingerprint(
    *,
    path: str,
    replacement_type: str,
    rendered: str,
    line_count: int,
    file_size_bytes: Optional[int],
) -> str:
    payload = "\n".join(
        [
            "structure-map-ts-v1",
            replacement_type,
            path,
            str(line_count),
            str(file_size_bytes or 0),
            rendered,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def summarize_with_tree_sitter(
    source: str,
    file_path: str,
    *,
    file_tokens_est: Optional[int] = None,
    file_size_bytes: Optional[int] = None,
) -> Optional[StructureMapResult]:
    """Summarize ``source`` using tree-sitter; return a ``StructureMapResult``
    or ``None`` when the backend cannot handle the file (caller falls through
    to the stdlib digest path). Never raises.

    The result has the same shape as the stdlib path: ``replacement_type`` is
    ``"skeleton"`` when structure was extracted (capped at
    ``MAX_REPLACEMENT_CHARS["skeleton"]``), ``parse_ok=True``, and ``eligible``
    consistent with the existing gate (``replacement_type != "digest"``).
    """
    if not is_available():
        return None
    suffix = Path(file_path).suffix.lower()
    lang_name = SUFFIX_TO_TS_LANG.get(suffix)
    if lang_name is None:
        return None
    if file_size_bytes is None:
        file_size_bytes = len(source.encode("utf-8", errors="ignore"))
    if file_tokens_est is None:
        file_tokens_est = estimate_tokens(source)
    # Guard against oversized inputs (mirrors MAX_AST_BYTES).
    if file_size_bytes > 800 * 1024:
        return None
    language_obj = _load_language(lang_name)
    if language_obj is None:
        return None
    parser = _make_parser(language_obj)
    if parser is None:
        return None
    source_bytes = source.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return None
    if tree is None:
        return None
    try:
        root = tree.root_node
    except Exception:
        return None
    if root is None:
        return None
    imports, classes, functions = _walk_tree(root, source_bytes)
    # If the parse produced no recognizable structure, fall through to digest
    # rather than emitting an empty skeleton.
    if not imports and not classes and not functions:
        return None
    line_count = source.count("\n") + (1 if source else 0)
    rendered = _render_skeleton(
        language=lang_name,
        line_count=line_count,
        imports=imports,
        classes=classes,
        functions=functions,
    )
    # Cap at the skeleton budget; if over, drop to a top_level-style truncation
    # then to digest, mirroring _shrink_or_fallback's order.
    replacement_type = "skeleton"
    if len(rendered) > MAX_REPLACEMENT_CHARS["skeleton"]:
        # Truncate functions/classes to fit the top_level budget.
        rendered = _render_skeleton(
            language=lang_name,
            line_count=line_count,
            imports=imports[:_MAX_IMPORTS],
            classes=classes[:_MAX_CLASSES],
            functions=functions[:_MAX_FUNCTIONS],
        )
        replacement_type = "top_level"
    if len(rendered) > MAX_REPLACEMENT_CHARS["top_level"]:
        # Last resort: a compact signatures-only digest.
        sig_lines = [f"{lang_name} signatures", f"lines: {line_count}"]
        items: List[str] = []
        for cls in classes[:_MAX_CLASSES]:
            items.append(f"{cls['signature']} @ L{cls['lineno']}")
        for func in functions[:_MAX_FUNCTIONS]:
            items.append(f"{func['signature']} @ L{func['lineno']}")
        if items:
            sig_lines.append(f"signatures ({len(items)}):")
            sig_lines.extend(f"  - {it}" for it in items)
        rendered = "\n".join(sig_lines)
        replacement_type = "signatures"
    if len(rendered) > MAX_REPLACEMENT_CHARS["signatures"]:
        return None  # caller falls through to digest
    eligible = replacement_type != "digest"
    fingerprint = _fingerprint(
        path=file_path,
        replacement_type=replacement_type,
        rendered=rendered,
        line_count=line_count,
        file_size_bytes=file_size_bytes,
    )
    # Confidence mirrors the stdlib skeleton baseline (0.90) with a small
    # discount because tree-sitter structure is less curated than the ast path.
    confidence = 0.86 if replacement_type == "skeleton" else (
        0.82 if replacement_type == "top_level" else 0.78
    )
    return StructureMapResult(
        file_path=file_path,
        language=detect_structure_language(file_path) or lang_name,
        replacement_type=replacement_type,
        replacement_text=rendered,
        replacement_tokens_est=estimate_tokens(rendered),
        confidence=confidence,
        fingerprint=fingerprint,
        eligible=eligible,
        reason="ok" if eligible else "fallback_digest",
        generated_like=False,
        parse_ok=True,
        line_count=line_count,
        file_tokens_est=file_tokens_est,
        file_size_bytes=file_size_bytes,
    )


__all__ = [
    "FLAG_ENV",
    "SUFFIX_TO_TS_LANG",
    "is_available",
    "summarize_with_tree_sitter",
    "supported_suffixes",
]
