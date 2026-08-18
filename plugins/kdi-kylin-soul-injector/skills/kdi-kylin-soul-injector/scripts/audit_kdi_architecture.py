#!/usr/bin/env python3
"""Read-only, dependency-free structural audit for KDI Unity C# code."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


TOOL_VERSION = "2.0.0"
CONTRACT_VERSION = "2.0-preview.1"
AUTHORITY = "ADVISORY"

DIAGNOSTIC_IDS = {
    "kdi_usage_unknown": "KSI001",
    "multiple_layers": "KSI002",
    "self_injection": "KSI003",
    "locator_outside_boundary": "KSI004",
    "resolver_injection": "KSI005",
    "non_injectable_host": "KSI006",
    "layer_violation": "KSI007",
    "missing_lifetime": "KSI008",
    "transient_entry_point": "KSI009",
    "transient_update_loop": "KSI010",
    "direct_managed_construction": "KSI011",
    "transient_unity_object": "KSI012",
    "layer_identity_unknown": "KSI101",
    "inject_host_unknown": "KSI102",
    "injectable_status_unknown": "KSI103",
    "unclassified_local_contract": "KSI104",
    "factory_resolve": "KSI105",
    "direct_object_instantiate": "KSI106",
    "semantic_resolution_unknown": "KSI107",
    "heuristic_boundary_unknown": "KSI108",
    "retained_transient": "KSI109",
    "externally_owned_instance": "KSI110",
}

if len(DIAGNOSTIC_IDS) != len(set(DIAGNOSTIC_IDS.values())):
    raise RuntimeError("KDI audit diagnostic IDs must be unique per rule.")

EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".vs",
    ".vscode",
    "Library",
    "Logs",
    "Temp",
    "UserSettings",
    "bin",
    "obj",
}

BOUNDARY_FILE_RE = re.compile(
    r"(?:Scope|ScopeHost|ScopeHandle|Bootstrap|Composition|CompositionRoot|Installer|Registry|Bindings?|Starter|Tests?)\.cs$",
    re.IGNORECASE,
)

TYPE_RE = re.compile(
    r"\b(?P<partial>partial\s+)?(?P<kind>class|interface|record(?:\s+class)?)\s+"
    r"(?P<name>[A-Za-z_]\w*)(?:\s*<[^>{};]*>)?\s*"
    r"(?:\:\s*(?P<bases>[^\{]+?))?\s*\{",
    re.MULTILINE,
)

NAMESPACE_RE = re.compile(
    r"\bnamespace\s+(?P<name>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?P<delimiter>[;{])",
    re.MULTILINE,
)

USING_ALIAS_RE = re.compile(
    r"^\s*(?:global\s+)?using\s+(?P<alias>[A-Za-z_]\w*)\s*=\s*"
    r"(?:global::)?(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;",
    re.MULTILINE,
)

USING_NAMESPACE_RE = re.compile(
    r"^\s*(?:global\s+)?using\s+(?!static\b)(?![A-Za-z_]\w*\s*=)"
    r"(?:global::)?(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;",
    re.MULTILINE,
)

GLOBAL_USING_ALIAS_RE = re.compile(
    r"^\s*global\s+using\s+(?P<alias>[A-Za-z_]\w*)\s*=\s*"
    r"(?:global::)?(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;",
    re.MULTILINE,
)

GLOBAL_USING_NAMESPACE_RE = re.compile(
    r"^\s*global\s+using\s+(?!static\b)(?![A-Za-z_]\w*\s*=)"
    r"(?:global::)?(?P<target>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;",
    re.MULTILINE,
)

INJECT_FIELD_RE = re.compile(
    r"\[(?=[^\]]*\bInject(?:Attribute)?\b)[^\]]+\]\s*"
    r"(?:\[[^\]]+\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|readonly|volatile|new)\s+)*"
    r"(?P<type>(?:global::)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
    r"(?:\s*<[^;=\n]+>)?(?:\s*\[\s*\])?\??)\s+"
    r"(?P<name>@?[A-Za-z_]\w*)\s*(?:=[^;]*)?;",
    re.MULTILINE,
)

BIND_START_RE = re.compile(
    r"\b[A-Za-z_]\w*\s*\.\s*Bind\s*<(?P<service>[^;{}]+?)>\s*\(\s*\)",
    re.MULTILINE,
)

LAYER_LEVELS = {
    "Kylin.DI.Layered.IViewLayer": 1,
    "Kylin.DI.Layered.IViewModelLayer": 2,
    "Kylin.DI.Layered.IApplicationServiceLayer": 3,
    "Kylin.DI.Layered.IDomainServiceLayer": 4,
    "Kylin.DI.Layered.IDataLayer": 5,
}

LAYER_NAMES = {
    1: "View",
    2: "ViewModel",
    3: "ApplicationService",
    4: "DomainService",
    5: "Data",
}

IMPLICIT_BASES = {
    "Kylin.DI.DIBehaviour": {"UnityEngine.MonoBehaviour", "Kylin.DI.IInjectable"},
    "UnityEngine.MonoBehaviour": {"UnityEngine.Component", "UnityEngine.Object"},
    "UnityEngine.Component": {"UnityEngine.Object"},
    "UnityEngine.GameObject": {"UnityEngine.Object"},
    "UnityEngine.ScriptableObject": {"UnityEngine.Object"},
    "Kylin.DI.Layered.IViewLayer": {"Kylin.DI.IInjectable"},
    "Kylin.DI.Layered.IViewModelLayer": {"Kylin.DI.IDependencyObject", "Kylin.DI.IInjectable"},
    "Kylin.DI.Layered.IApplicationServiceLayer": {"Kylin.DI.IDependencyObject", "Kylin.DI.IInjectable"},
    "Kylin.DI.Layered.IDomainServiceLayer": {"Kylin.DI.IDependencyObject", "Kylin.DI.IInjectable"},
    "Kylin.DI.Layered.IDataLayer": {"Kylin.DI.IDependencyObject"},
}

REJECTED_TRANSIENT_BASES = {
    "Kylin.DI.IUpdatable",
    "Kylin.DI.IFixedUpdatable",
    "Kylin.DI.ILateUpdatable",
}

RETAINED_TRANSIENT_BASES = {
    "System.IDisposable",
    "Kylin.DI.IInjectable",
}

KNOWN_TYPES = {
    *LAYER_LEVELS,
    *IMPLICIT_BASES,
    *(item for values in IMPLICIT_BASES.values() for item in values),
    *REJECTED_TRANSIENT_BASES,
    *RETAINED_TRANSIENT_BASES,
    "Kylin.DI.IScope",
    "Kylin.DI.IInstantiator",
    "UnityEngine.ScriptableObject",
    "System.Object",
}


def implicit_base_closure(identity: str) -> set[str]:
    """Return every transitive implicit base without revisiting cycles."""
    visited = {identity}
    result: set[str] = set()

    def collect(current: str) -> None:
        for base in IMPLICIT_BASES.get(current, set()):
            if base in visited:
                continue
            visited.add(base)
            result.add(base)
            collect(base)

    collect(identity)
    return result


@dataclass
class Source:
    path: Path
    text: str
    code: str
    usings: tuple[str, ...]
    aliases: dict[str, str]
    global_usings: tuple[str, ...]
    global_aliases: dict[str, str]


@dataclass
class TypeDecl:
    source: Source
    kind: str
    name: str
    namespace: str
    full_name: str
    is_partial: bool
    bases: tuple[str, ...]
    start: int
    end: int


@dataclass(order=True)
class Finding:
    path: str
    line: int
    severity: str
    code: str
    message: str
    confidence: str
    authority: str
    state: str


@dataclass(frozen=True)
class AuditConfig:
    boundary_globs: tuple[str, ...] = ()
    framework_namespace_prefixes: tuple[str, ...] = ("Kylin.DI",)
    fail_on: str = "never"


@dataclass(frozen=True)
class Resolution:
    identities: tuple[str, ...]
    confidence: str
    reason: str = ""


def mask_non_code(text: str) -> str:
    chars = list(text)
    i = 0
    length = len(chars)

    def blank(index: int) -> None:
        if chars[index] not in "\r\n":
            chars[index] = " "

    while i < length:
        if i + 1 < length and chars[i] == "/" and chars[i + 1] == "/":
            blank(i)
            blank(i + 1)
            i += 2
            while i < length and chars[i] not in "\r\n":
                blank(i)
                i += 1
            continue

        if i + 1 < length and chars[i] == "/" and chars[i + 1] == "*":
            blank(i)
            blank(i + 1)
            i += 2
            while i + 1 < length and not (chars[i] == "*" and chars[i + 1] == "/"):
                blank(i)
                i += 1
            if i + 1 < length:
                blank(i)
                blank(i + 1)
                i += 2
            continue

        if text.startswith('"""', i):
            for offset in range(3):
                blank(i + offset)
            i += 3
            while i + 2 < length and not text.startswith('"""', i):
                blank(i)
                i += 1
            for offset in range(3):
                if i + offset < length:
                    blank(i + offset)
            i += 3
            continue

        if chars[i] == '"':
            verbatim = i > 0 and text[i - 1] == "@"
            blank(i)
            i += 1
            while i < length:
                if verbatim and chars[i] == '"' and i + 1 < length and chars[i + 1] == '"':
                    blank(i)
                    blank(i + 1)
                    i += 2
                    continue
                if chars[i] == '"':
                    blank(i)
                    i += 1
                    break
                if not verbatim and chars[i] == "\\" and i + 1 < length:
                    blank(i)
                    blank(i + 1)
                    i += 2
                    continue
                blank(i)
                i += 1
            continue

        if chars[i] == "'":
            blank(i)
            i += 1
            while i < length:
                if chars[i] == "\\" and i + 1 < length:
                    blank(i)
                    blank(i + 1)
                    i += 2
                    continue
                if chars[i] == "'":
                    blank(i)
                    i += 1
                    break
                blank(i)
                i += 1
            continue

        i += 1

    return "".join(chars)


def split_top_level(value: str) -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:index])
            start = index + 1
    result.append(value[start:])
    return result


def type_name(value: str) -> str:
    value = value.strip().replace("global::", "")
    value = re.split(r"\bwhere\b", value, maxsplit=1)[0].strip()
    value = value.rstrip("? ")
    while value.endswith("[]"):
        value = value[:-2].rstrip()
    if "<" in value:
        value = value.split("<", 1)[0].strip()
    return re.sub(r"\s+", "", value)


def simple_type(value: str) -> str:
    return type_name(value).rsplit(".", 1)[-1]


def matching_brace(code: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(code)


def namespace_at(code: str, offset: int) -> str:
    containing: list[tuple[int, str]] = []
    file_scoped: tuple[int, str] | None = None
    for match in NAMESPACE_RE.finditer(code, 0, offset):
        if match.group("delimiter") == ";":
            file_scoped = (match.start(), match.group("name"))
            continue
        opening = code.find("{", match.start(), match.end())
        if opening >= 0 and offset < matching_brace(code, opening):
            containing.append((match.start(), match.group("name")))

    if file_scoped is not None:
        containing.append(file_scoped)
    return ".".join(name for _, name in sorted(containing))


def parse_types(source: Source) -> list[TypeDecl]:
    declarations: list[TypeDecl] = []
    for match in TYPE_RE.finditer(source.code):
        raw_bases = match.group("bases") or ""
        raw_bases = re.split(r"\bwhere\b", raw_bases, maxsplit=1)[0]
        bases = tuple(
            type_name(part)
            for part in split_top_level(raw_bases)
            if type_name(part)
        )
        opening = source.code.find("{", match.start(), match.end())
        namespace = namespace_at(source.code, match.start())
        name = match.group("name")
        declarations.append(
            TypeDecl(
                source=source,
                kind=match.group("kind"),
                name=name,
                namespace=namespace,
                full_name=f"{namespace}.{name}" if namespace else name,
                is_partial=bool(match.group("partial")),
                bases=bases,
                start=match.start(),
                end=matching_brace(source.code, opening),
            )
        )
    return declarations


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def boundary_kind(path: Path, config: AuditConfig) -> str | None:
    normalized = str(path).replace("\\", "/")
    if any(fnmatch.fnmatch(normalized, pattern.replace("\\", "/")) for pattern in config.boundary_globs):
        return "explicit"
    if any(part.lower() in {"test", "tests", "tests~", "editmode", "playmode"} for part in path.parts):
        return "test"
    if BOUNDARY_FILE_RE.search(path.name):
        return "heuristic"
    return None


def find_statement_end(code: str, start: int) -> int:
    paren = brace = bracket = 0
    for index in range(start, len(code)):
        char = code[index]
        if char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == ";" and paren == brace == bracket == 0:
            return index + 1
    return len(code)


def discover_scan_roots(project: Path, includes: list[str]) -> list[Path]:
    if includes:
        roots = [(project / item).resolve() for item in includes]
    elif (project / "Assets").is_dir():
        roots = [(project / "Assets").resolve()]
    elif (project / "package.json").is_file():
        candidates = ["Runtime", "Editor", "Tests", "Tests~", "Samples~"]
        roots = [(project / item).resolve() for item in candidates if (project / item).exists()]
    else:
        roots = [project.resolve()]

    missing = [root for root in roots if not root.exists()]
    if missing:
        raise ValueError("scan path does not exist: " + ", ".join(str(path) for path in missing))
    return roots


def iter_cs_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        scan_base = root if root.is_dir() else root.parent
        candidates = [root] if root.is_file() else root.rglob("*.cs")
        for path in candidates:
            if path in seen or path.suffix.lower() != ".cs":
                continue
            relative_parts = path.relative_to(scan_base).parts
            if any(part in EXCLUDED_DIRS for part in relative_parts[:-1]):
                continue
            if path.name.endswith(".g.cs"):
                continue
            seen.add(path)
            yield path


def read_sources(project: Path, roots: list[Path]) -> list[Source]:
    sources: list[Source] = []
    for path in sorted(iter_cs_files(roots), key=lambda item: str(item).lower()):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        if "<auto-generated" in text[:1000].lower():
            continue
        code = mask_non_code(text)
        aliases = {
            match.group("alias"): match.group("target")
            for match in USING_ALIAS_RE.finditer(code)
        }
        usings = tuple(match.group("target") for match in USING_NAMESPACE_RE.finditer(code))
        global_aliases = {
            match.group("alias"): match.group("target")
            for match in GLOBAL_USING_ALIAS_RE.finditer(code)
        }
        global_usings = tuple(
            match.group("target") for match in GLOBAL_USING_NAMESPACE_RE.finditer(code)
        )
        sources.append(
            Source(
                path=path.relative_to(project),
                text=text,
                code=code,
                usings=usings,
                aliases=aliases,
                global_usings=global_usings,
                global_aliases=global_aliases,
            )
        )
    return sources


def load_config(project: Path, config_path: Path | None) -> AuditConfig:
    path = config_path or (project / ".kdi-audit.json")
    if not path.is_file():
        return AuditConfig()

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"audit config must contain a JSON object: {path}")

    version = data.get("contract_version", CONTRACT_VERSION)
    if version != CONTRACT_VERSION:
        raise ValueError(
            f"audit config contract_version is {version!r}; expected {CONTRACT_VERSION!r}"
        )

    boundary_globs = data.get("boundary_globs", [])
    framework_prefixes = data.get("framework_namespace_prefixes", ["Kylin.DI"])
    fail_on = data.get("fail_on", "never")
    if not isinstance(boundary_globs, list) or not all(isinstance(item, str) for item in boundary_globs):
        raise ValueError("boundary_globs must be an array of strings")
    if not isinstance(framework_prefixes, list) or not all(isinstance(item, str) for item in framework_prefixes):
        raise ValueError("framework_namespace_prefixes must be an array of strings")
    if fail_on not in {"never", "error", "warning"}:
        raise ValueError("fail_on must be one of: never, error, warning")

    return AuditConfig(
        boundary_globs=tuple(boundary_globs),
        framework_namespace_prefixes=tuple(framework_prefixes),
        fail_on=fail_on,
    )


def package_versions(project: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for relative in ("Packages/packages-lock.json", "Packages/manifest.json", "package.json"):
        path = project / relative
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue

        if relative.endswith("packages-lock.json"):
            dependencies = data.get("dependencies", {})
            for name in ("com.kylin.di", "com.kylin.di.layered", "com.kylin.subscribable"):
                entry = dependencies.get(name)
                if isinstance(entry, dict) and entry.get("version"):
                    versions[name] = str(entry["version"])
        elif relative.endswith("manifest.json"):
            dependencies = data.get("dependencies", {})
            for name in ("com.kylin.di", "com.kylin.di.layered", "com.kylin.subscribable"):
                if name in dependencies and name not in versions:
                    versions[name] = str(dependencies[name])
        elif data.get("name") in {"com.kylin.di", "com.kylin.di.layered", "com.kylin.subscribable"}:
            versions[str(data["name"])] = str(data.get("version", "unknown"))
    return versions


def audit(project: Path, sources: list[Source], config: AuditConfig | None = None) -> list[Finding]:
    config = config or AuditConfig()
    findings: list[Finding] = []
    declarations = [decl for source in sources for decl in parse_types(source)]
    by_name: dict[str, list[TypeDecl]] = {}
    by_full_name: dict[str, list[TypeDecl]] = {}
    for declaration in declarations:
        by_name.setdefault(declaration.name, []).append(declaration)
        by_full_name.setdefault(declaration.full_name, []).append(declaration)

    global_usings = {namespace for source in sources for namespace in source.global_usings}
    global_alias_targets: dict[str, set[str]] = {}
    for source in sources:
        for alias, target in source.global_aliases.items():
            global_alias_targets.setdefault(alias, set()).add(target)

    def source_of(context: TypeDecl | Source) -> Source:
        return context.source if isinstance(context, TypeDecl) else context

    def namespace_of(context: TypeDecl | Source) -> str:
        return context.namespace if isinstance(context, TypeDecl) else ""

    def resolve(reference: str, context: TypeDecl | Source) -> Resolution:
        source = source_of(context)
        name = type_name(reference)
        if not name:
            return Resolution((), "UNKNOWN", "empty type reference")

        first, separator, remainder = name.partition(".")
        if first in global_alias_targets and len(global_alias_targets[first]) > 1:
            targets = ", ".join(sorted(global_alias_targets[first]))
            return Resolution((), "UNKNOWN", f"conflicting global alias {first}: {targets}")
        if first in source.aliases:
            target = source.aliases[first]
            name = target + (separator + remainder if separator else "")
        elif first in global_alias_targets:
            target = next(iter(global_alias_targets[first]))
            name = target + (separator + remainder if separator else "")

        if name == "object":
            return Resolution(("System.Object",), "HIGH")

        if "." in name:
            return Resolution((name,), "HIGH")

        # C# simple-name lookup walks the current namespace and then each
        # enclosing namespace before considering using directives. Preserve
        # that precedence so Company.Feature can resolve Company.Service
        # without inventing an ambiguity with an imported Service.
        namespace_parts = namespace_of(context).split(".") if namespace_of(context) else []
        enclosing_names = [
            ".".join((*namespace_parts[:depth], name))
            for depth in range(len(namespace_parts), 0, -1)
        ]
        enclosing_names.append(name)
        for local_name in enclosing_names:
            if local_name not in by_full_name and local_name not in KNOWN_TYPES:
                continue
            declarations_for_name = by_full_name.get(local_name, [])
            if len(declarations_for_name) > 1 and not all(item.is_partial for item in declarations_for_name):
                return Resolution((local_name,), "UNKNOWN", f"conflicting non-partial declarations for {local_name}")
            return Resolution((local_name,), "HIGH")

        candidates: set[str] = set()
        for imported_namespace in {*source.usings, *global_usings}:
            candidate = f"{imported_namespace}.{name}"
            if candidate in by_full_name or candidate in KNOWN_TYPES:
                candidates.add(candidate)
        if len(candidates) == 1:
            return Resolution(tuple(candidates), "MEDIUM")
        if len(candidates) > 1:
            return Resolution(tuple(sorted(candidates)), "UNKNOWN", f"ambiguous simple name {name}")
        return Resolution((), "UNKNOWN", f"definition for {name} is outside the scan or not imported")

    closure_cache: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {}

    def closure_identity(identity: str, active: set[str] | None = None) -> tuple[set[str], set[str]]:
        if identity in closure_cache:
            names, reasons = closure_cache[identity]
            return set(names), set(reasons)

        active = set() if active is None else active
        if identity in active:
            return {identity}, set()
        active.add(identity)
        names = {identity}
        reasons: set[str] = set()
        names.update(implicit_base_closure(identity))

        matching_declarations = by_full_name.get(identity, [])
        if len(matching_declarations) > 1 and not all(item.is_partial for item in matching_declarations):
            reasons.add(f"conflicting non-partial declarations for {identity}")
        if matching_declarations:
            for declaration in matching_declarations:
                for base in declaration.bases:
                    resolution = resolve(base, declaration)
                    if len(resolution.identities) != 1:
                        reasons.add(resolution.reason or f"could not resolve {base}")
                        continue
                    base_names, base_reasons = closure_identity(resolution.identities[0], active)
                    names.update(base_names)
                    reasons.update(base_reasons)
        elif identity not in KNOWN_TYPES:
            reasons.add(f"definition for {identity} is outside the selected scan paths")

        active.remove(identity)
        closure_cache[identity] = (frozenset(names), tuple(sorted(reasons)))
        return names, reasons

    def closure(reference: str, context: TypeDecl | Source) -> tuple[set[str], set[str]]:
        resolution = resolve(reference, context)
        if len(resolution.identities) != 1:
            return set(), {resolution.reason or f"could not resolve {reference}"}
        names, reasons = closure_identity(resolution.identities[0])
        if resolution.confidence == "UNKNOWN":
            reasons.add(resolution.reason or f"could not resolve {reference}")
        return names, reasons

    def layers(reference: str, context: TypeDecl | Source) -> tuple[set[int], set[str]]:
        ancestors, reasons = closure(reference, context)
        return ({level for marker, level in LAYER_LEVELS.items() if marker in ancestors}, reasons)

    def declaration_layers(declaration: TypeDecl) -> tuple[set[int], set[str]]:
        ancestors, reasons = closure_identity(declaration.full_name)
        return ({level for marker, level in LAYER_LEVELS.items() if marker in ancestors}, reasons)

    def add(
        source: Source,
        offset: int,
        severity: str,
        code: str,
        message: str,
        confidence: str = "MEDIUM",
        state: str = "ADVISORY",
    ) -> None:
        findings.append(
            Finding(
                path=str(source.path).replace("\\", "/"),
                line=line_number(source.text, offset),
                severity=severity,
                code=code,
                message=message,
                confidence=confidence,
                authority=AUTHORITY,
                state=state,
            )
        )

    def add_unknown(source: Source, offset: int, code: str, message: str) -> None:
        add(source, offset, "WARN", code, message, confidence="UNKNOWN", state="UNKNOWN")

    uses_kdi = any(
        "Kylin.DI" in source.code
        or any(namespace == "Kylin.DI" or namespace.startswith("Kylin.DI.") for namespace in source.usings)
        or any(target == "Kylin.DI" or target.startswith("Kylin.DI.") for target in source.aliases.values())
        for source in sources
    )
    if not uses_kdi:
        findings.append(
            Finding(
                path=".",
                line=1,
                severity="WARN",
                code=DIAGNOSTIC_IDS["kdi_usage_unknown"],
                message="KDI usage could not be established in the selected scan paths.",
                confidence="UNKNOWN",
                authority=AUTHORITY,
                state="UNKNOWN",
            )
        )
        return findings

    layered_declarations = [
        declaration for declaration in declarations if declaration_layers(declaration)[0]
    ]
    if not layered_declarations and not any("Kylin.DI.Layered" in source.usings for source in sources):
        findings.append(
            Finding(
                path=".",
                line=1,
                severity="WARN",
                code=DIAGNOSTIC_IDS["layer_identity_unknown"],
                message="KDI Layered marker identity was not established; layer checks are limited.",
                confidence="UNKNOWN",
                authority=AUTHORITY,
                state="UNKNOWN",
            )
        )

    reported_declarations: set[str] = set()
    for declaration in declarations:
        if declaration.full_name in reported_declarations:
            continue
        reported_declarations.add(declaration.full_name)
        declared_layers, unknown_reasons = declaration_layers(declaration)
        if unknown_reasons and len(declared_layers) > 1:
            add_unknown(
                declaration.source,
                declaration.start,
                DIAGNOSTIC_IDS["semantic_resolution_unknown"],
                f"Could not prove one layer for {declaration.full_name}: {'; '.join(sorted(unknown_reasons))}.",
            )
        elif len(declared_layers) > 1:
            names = ", ".join(LAYER_NAMES[level] for level in sorted(declared_layers))
            add(
                declaration.source,
                declaration.start,
                "ERROR",
                DIAGNOSTIC_IDS["multiple_layers"],
                f"{declaration.full_name} belongs to multiple KDI layers: {names}.",
                confidence="HIGH",
            )

    declarations_by_source: dict[Path, list[TypeDecl]] = {}
    for declaration in declarations:
        declarations_by_source.setdefault(declaration.source.path, []).append(declaration)

    managed_identities: set[str] = set()
    for declaration in declarations:
        ancestors, _ = closure_identity(declaration.full_name)
        declaration_layer_set = {level for marker, level in LAYER_LEVELS.items() if marker in ancestors}
        if declaration_layer_set or "Kylin.DI.IDependencyObject" in ancestors:
            managed_identities.add(declaration.full_name)

    for source in sources:
        source_declarations = declarations_by_source.get(source.path, [])
        namespaces = {declaration.namespace for declaration in source_declarations}
        namespaces.update(match.group("name") for match in NAMESPACE_RE.finditer(source.code))
        framework_source = any(
            namespace == prefix or namespace.startswith(prefix + ".")
            for namespace in namespaces
            for prefix in config.framework_namespace_prefixes
        )
        source_boundary = boundary_kind(source.path, config)

        def host_at(offset: int) -> TypeDecl | None:
            candidates = [decl for decl in source_declarations if decl.start <= offset < decl.end]
            return min(candidates, key=lambda decl: decl.end - decl.start) if candidates else None

        self_inject_patterns = (
            re.compile(r"\bthis\s*\.\s*Inject\s*\("),
            re.compile(r"\bDependencyInjector\s*\.\s*Inject\s*\(\s*this\b"),
        )
        for pattern in self_inject_patterns:
            for match in pattern.finditer(source.code):
                add(
                    source,
                    match.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["self_injection"],
                    "Self-injection is forbidden; the creator must inject the object.",
                    confidence="HIGH",
                )

        locator_patterns = (
            (re.compile(r"\bLifetimeScope\s*\.\s*Find\w*\s*\("), "LifetimeScope.Find"),
            (re.compile(r"\bKDI\s*\.\s*RootScope\b"), "KDI.RootScope"),
            (re.compile(r"\b_?[A-Za-z_]*scope\s*\.\s*Resolve\s*(?:<|\()", re.IGNORECASE), "Scope.Resolve"),
            (re.compile(r"\bScope\s*\.\s*Resolve\s*(?:<|\()"), "DIBehaviour.Scope.Resolve"),
        )
        seen_offsets: set[int] = set()
        heuristic_boundary_reported = False
        if source_boundary not in {"explicit", "test"} and not framework_source:
            for pattern, label in locator_patterns:
                for match in pattern.finditer(source.code):
                    if match.start() in seen_offsets:
                        continue
                    seen_offsets.add(match.start())
                    if source_boundary == "heuristic":
                        if not heuristic_boundary_reported:
                            add_unknown(
                                source,
                                match.start(),
                                DIAGNOSTIC_IDS["heuristic_boundary_unknown"],
                                "Composition-boundary authority is inferred only from the filename; add an explicit boundary_globs entry.",
                            )
                            heuristic_boundary_reported = True
                        continue
                    add(
                        source,
                        match.start(),
                        "ERROR",
                        DIAGNOSTIC_IDS["locator_outside_boundary"],
                        f"{label} appears outside an explicit composition boundary.",
                        confidence="MEDIUM",
                    )

        for match in INJECT_FIELD_RE.finditer(source.code):
            host = host_at(match.start())
            dependency_reference = type_name(match.group("type"))
            dependency_display = simple_type(dependency_reference)

            dependency_resolution = resolve(dependency_reference, host or source)
            if dependency_resolution.identities in {
                ("Kylin.DI.IScope",),
                ("Kylin.DI.IResolver",),
            }:
                add(
                    source,
                    match.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["resolver_injection"],
                    f"Resolver authority {dependency_display} must not be injected.",
                    confidence=dependency_resolution.confidence,
                )
            elif dependency_display in {"IScope", "IResolver", "Scope"} and not dependency_resolution.identities:
                add_unknown(
                    source,
                    match.start(),
                    DIAGNOSTIC_IDS["semantic_resolution_unknown"],
                    f"Resolver-like injected type {dependency_display} could not be resolved to a namespace.",
                )

            if host is None:
                add_unknown(
                    source,
                    match.start(),
                    DIAGNOSTIC_IDS["inject_host_unknown"],
                    f"Could not determine the host type for injected field {match.group('name')}.",
                )
                continue

            host_ancestors, host_unknown = closure_identity(host.full_name)
            if "Kylin.DI.IInjectable" not in host_ancestors:
                if host_unknown:
                    add_unknown(
                        source,
                        match.start(),
                        DIAGNOSTIC_IDS["injectable_status_unknown"],
                        f"Could not prove that {host.full_name} is IInjectable: {'; '.join(sorted(host_unknown))}.",
                    )
                else:
                    add(
                        source,
                        match.start(),
                        "ERROR",
                        DIAGNOSTIC_IDS["non_injectable_host"],
                        f"{host.full_name} has [Inject] fields but no IInjectable marker.",
                        confidence="HIGH",
                    )

            host_layers, host_layer_unknown = declaration_layers(host)
            dependency_layers, dependency_unknown = layers(dependency_reference, host)
            if host_layer_unknown or dependency_unknown:
                if dependency_resolution.identities or dependency_display in by_name:
                    reasons = sorted(host_layer_unknown | dependency_unknown)
                    add_unknown(
                        source,
                        match.start(),
                        DIAGNOSTIC_IDS["semantic_resolution_unknown"],
                        f"Could not prove the layer relation for {host.full_name}.{match.group('name')}: {'; '.join(reasons)}.",
                    )
            elif len(host_layers) == 1 and len(dependency_layers) == 1:
                host_level = next(iter(host_layers))
                dependency_level = next(iter(dependency_layers))
                if dependency_level <= host_level:
                    relation = "same-layer" if dependency_level == host_level else "upward"
                    add(
                        source,
                        match.start(),
                        "ERROR",
                        DIAGNOSTIC_IDS["layer_violation"],
                        f"{host.full_name} ({LAYER_NAMES[host_level]}) has {relation} injection of "
                        f"{dependency_display} ({LAYER_NAMES[dependency_level]}).",
                        confidence="HIGH",
                    )
            elif len(dependency_resolution.identities) == 1:
                dependency_identity = dependency_resolution.identities[0]
                if dependency_identity in by_full_name and not dependency_layers and dependency_display != "IInstantiator":
                    add(
                        source,
                        match.start(),
                        "WARN",
                        DIAGNOSTIC_IDS["unclassified_local_contract"],
                        f"Injected local contract {dependency_identity} is unclassified; verify it is a narrow infrastructure/config boundary.",
                        confidence="HIGH",
                    )

        for bind in BIND_START_RE.finditer(source.code):
            end = find_statement_end(source.code, bind.end())
            chain = source.code[bind.end():end]
            service_reference = type_name(bind.group("service"))
            service = simple_type(service_reference)
            has_lifetime = bool(re.search(r"\.\s*As(?:Singleton|Scoped|Transient)\s*\(", chain))
            has_instance_terminal = bool(re.search(r"\.\s*FromInstance\s*\(", chain))
            if not has_lifetime and not has_instance_terminal:
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["missing_lifetime"],
                    f"Bind<{service}> has no lifetime or FromInstance terminator.",
                    confidence="HIGH",
                )

            is_transient = bool(re.search(r"\.\s*AsTransient\s*\(", chain))
            is_entry_point = bool(re.search(r"\.\s*AsEntryPoint\s*\(", chain))
            if is_transient and is_entry_point:
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["transient_entry_point"],
                    f"Bind<{service}> combines Transient with EntryPoint.",
                    confidence="HIGH",
                )

            implementation_match = re.search(r"\.\s*To\s*<(?P<type>[^>]+)>", chain)
            implementation = type_name(implementation_match.group("type")) if implementation_match else service_reference
            implementation_host = host_at(bind.start()) or source
            implementation_ancestors, implementation_unknown = closure(implementation, implementation_host)
            if has_instance_terminal and "System.IDisposable" in implementation_ancestors and not implementation_unknown:
                add(
                    source,
                    bind.start(),
                    "WARN",
                    DIAGNOSTIC_IDS["externally_owned_instance"],
                    f"FromInstance<{service}> remains externally owned in KDI 2.0. Prove that the supplier disposes it after Scope injection is revoked.",
                    confidence="HIGH",
                )
            rejected_interfaces = REJECTED_TRANSIENT_BASES.intersection(implementation_ancestors)
            retained_interfaces = RETAINED_TRANSIENT_BASES.intersection(implementation_ancestors)
            is_unity_object = "UnityEngine.Object" in implementation_ancestors
            if is_transient and rejected_interfaces and not implementation_unknown:
                rejected = ", ".join(sorted(item.rsplit(".", 1)[-1] for item in rejected_interfaces))
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["transient_update_loop"],
                    f"Transient {simple_type(implementation)} implements update interfaces rejected by KDI 2.0: {rejected}.",
                    confidence="HIGH",
                )
            if is_transient and is_unity_object and not implementation_unknown:
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["transient_unity_object"],
                    f"Transient {simple_type(implementation)} is a UnityEngine.Object rejected by KDI 2.0.",
                    confidence="HIGH",
                )
            if is_transient and retained_interfaces and not implementation_unknown:
                retained = ", ".join(sorted(item.rsplit(".", 1)[-1] for item in retained_interfaces))
                add(
                    source,
                    bind.start(),
                    "WARN",
                    DIAGNOSTIC_IDS["retained_transient"],
                    f"Transient {simple_type(implementation)} is retained by the Scope for injection/disposal until shutdown: {retained}. Use a shorter Scope for high-volume resolves.",
                    confidence="HIGH",
                )

            if re.search(r"\.\s*FromFactory\s*\(", chain) and re.search(r"\.\s*Resolve\s*(?:<|\()", chain):
                add(
                    source,
                    bind.start(),
                    "WARN",
                    DIAGNOSTIC_IDS["factory_resolve"],
                    f"Bind<{service}> captures a resolver inside zero-argument FromFactory; analyzer visibility is bypassed.",
                    confidence="HIGH",
                )

        if source_boundary not in {"explicit", "test"}:
            creation_pattern = re.compile(
                r"\bnew\s+(?P<type>(?:global::)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
                r"\s*(?:<[^;()]+>)?\s*\("
            )
            for match in creation_pattern.finditer(source.code):
                created_reference = type_name(match.group("type"))
                creation_host = host_at(match.start()) or source
                resolution = resolve(created_reference, creation_host)
                candidate_managed = managed_identities.intersection(resolution.identities)
                if len(resolution.identities) != 1:
                    if candidate_managed:
                        add_unknown(
                            source,
                            match.start(),
                            DIAGNOSTIC_IDS["semantic_resolution_unknown"],
                            f"Direct construction target {created_reference} is ambiguous; managed candidates: {', '.join(sorted(candidate_managed))}.",
                        )
                    continue
                if resolution.identities[0] not in managed_identities:
                    continue
                if source_boundary == "heuristic":
                    if not heuristic_boundary_reported:
                        add_unknown(
                            source,
                            match.start(),
                            DIAGNOSTIC_IDS["heuristic_boundary_unknown"],
                            "Composition-boundary authority is inferred only from the filename; add an explicit boundary_globs entry.",
                        )
                        heuristic_boundary_reported = True
                    continue
                add(
                    source,
                    match.start(),
                    "ERROR",
                    DIAGNOSTIC_IDS["direct_managed_construction"],
                    f"Direct construction of KDI-managed type {resolution.identities[0]} appears outside a composition boundary.",
                    confidence="HIGH",
                )

        if not framework_source:
            for match in re.finditer(r"\b(?:UnityEngine\s*\.\s*)?Object\s*\.\s*Instantiate\s*\(", source.code):
                add(
                    source,
                    match.start(),
                    "WARN",
                    DIAGNOSTIC_IDS["direct_object_instantiate"],
                    "Direct Object.Instantiate detected; use IInstantiator when the hierarchy contains IInjectable components.",
                    confidence="MEDIUM",
                )

    return sorted(findings)


def should_fail(findings: list[Finding], fail_on: str) -> bool:
    if fail_on == "never":
        return False
    if fail_on == "error":
        return any(finding.severity == "ERROR" for finding in findings)
    return bool(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="Unity project or UPM package root")
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Relative file/directory to scan; repeat for multiple roots",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--config",
        type=Path,
        help="Audit config path (default: <project>/.kdi-audit.json when present)",
    )
    parser.add_argument(
        "--fail-on",
        choices=("never", "error", "warning"),
        help="Optional advisory gate; default is config value or never",
    )
    parser.add_argument(
        "--legacy-blocking",
        action="store_true",
        help="Compatibility alias for --fail-on error",
    )
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"error: project root does not exist: {project}", file=sys.stderr)
        return 2

    try:
        config_path = args.config.expanduser().resolve() if args.config else None
        config = load_config(project, config_path)
        roots = discover_scan_roots(project, args.include)
        sources = read_sources(project, roots)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    findings = audit(project, sources, config)
    versions = package_versions(project)
    errors = sum(finding.severity == "ERROR" for finding in findings)
    warnings = sum(finding.severity == "WARN" for finding in findings)
    advisory = sum(finding.state == "ADVISORY" for finding in findings)
    unknown = sum(finding.state == "UNKNOWN" for finding in findings)
    fail_on = "error" if args.legacy_blocking else (args.fail_on or config.fail_on)

    if args.json:
        print(
            json.dumps(
                {
                    "tool_version": TOOL_VERSION,
                    "contract_version": CONTRACT_VERSION,
                    "authority": AUTHORITY,
                    "project": str(project),
                    "files_scanned": len(sources),
                    "packages": versions,
                    "fail_on": fail_on,
                    "errors": errors,
                    "warnings": warnings,
                    "advisory": advisory,
                    "unknown": unknown,
                    "findings": [asdict(finding) for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        package_text = ", ".join(f"{name}={version}" for name, version in sorted(versions.items()))
        print(
            f"KDI architecture audit {TOOL_VERSION} / contract {CONTRACT_VERSION}: "
            f"{len(sources)} C# files"
        )
        print("Authority: ADVISORY (Roslyn/compiler/runtime evidence remains authoritative)")
        print(f"Packages: {package_text or 'not resolved from manifests'}")
        for finding in findings:
            print(
                f"{finding.state} {finding.severity} {finding.confidence} "
                f"{finding.code} {finding.path}:{finding.line} {finding.message}"
            )
        print(
            f"Summary: {errors} error signal(s), {warnings} warning signal(s); "
            f"{advisory} advisory, {unknown} unknown; fail_on={fail_on}"
        )

    return 1 if should_fail(findings, fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
