#!/usr/bin/env python3
"""Read-only, dependency-free structural audit for KDI Unity C# code."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


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
    r"\b(?P<kind>class|interface|record(?:\s+class)?)\s+"
    r"(?P<name>[A-Za-z_]\w*)(?:\s*<[^>{};]*>)?\s*"
    r"(?:\:\s*(?P<bases>[^\{]+?))?\s*\{",
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
    "IViewLayer": 1,
    "IViewModelLayer": 2,
    "IApplicationServiceLayer": 3,
    "IDomainServiceLayer": 4,
    "IDataLayer": 5,
}

LAYER_NAMES = {
    1: "View",
    2: "ViewModel",
    3: "ApplicationService",
    4: "DomainService",
    5: "Data",
}

IMPLICIT_BASES = {
    "DIBehaviour": {"MonoBehaviour", "IInjectable"},
    "IViewLayer": {"IInjectable"},
    "IViewModelLayer": {"IDependencyObject", "IInjectable"},
    "IApplicationServiceLayer": {"IDependencyObject", "IInjectable"},
    "IDomainServiceLayer": {"IDependencyObject", "IInjectable"},
    "IDataLayer": {"IDependencyObject"},
}

TRACKED_TRANSIENT_BASES = {
    "IDisposable",
    "IUpdatable",
    "IFixedUpdatable",
    "ILateUpdatable",
    "IKDIUpdatable",
    "IKDIFixedUpdatable",
    "IKDILateUpdatable",
}

KNOWN_EXTERNAL_BASES = {
    "IDisposable",
    "MonoBehaviour",
    "ScriptableObject",
    "object",
}


@dataclass
class Source:
    path: Path
    text: str
    code: str


@dataclass
class TypeDecl:
    source: Source
    kind: str
    name: str
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


def simple_type(value: str) -> str:
    value = value.strip().replace("global::", "")
    value = re.split(r"\bwhere\b", value, maxsplit=1)[0].strip()
    value = value.rstrip("? ")
    while value.endswith("[]"):
        value = value[:-2].rstrip()
    if "<" in value:
        value = value.split("<", 1)[0].strip()
    return value.rsplit(".", 1)[-1]


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


def parse_types(source: Source) -> list[TypeDecl]:
    declarations: list[TypeDecl] = []
    for match in TYPE_RE.finditer(source.code):
        raw_bases = match.group("bases") or ""
        raw_bases = re.split(r"\bwhere\b", raw_bases, maxsplit=1)[0]
        bases = tuple(
            simple_type(part)
            for part in split_top_level(raw_bases)
            if simple_type(part)
        )
        opening = source.code.find("{", match.start(), match.end())
        declarations.append(
            TypeDecl(
                source=source,
                kind=match.group("kind"),
                name=match.group("name"),
                bases=bases,
                start=match.start(),
                end=matching_brace(source.code, opening),
            )
        )
    return declarations


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_boundary(path: Path) -> bool:
    return bool(BOUNDARY_FILE_RE.search(path.name)) or any(
        part.lower() in {"test", "tests", "tests~", "editmode", "playmode"}
        for part in path.parts
    )


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
        sources.append(Source(path=path.relative_to(project), text=text, code=mask_non_code(text)))
    return sources


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


def audit(project: Path, sources: list[Source]) -> list[Finding]:
    findings: list[Finding] = []
    declarations = [decl for source in sources for decl in parse_types(source)]
    by_name: dict[str, list[TypeDecl]] = {}
    for declaration in declarations:
        by_name.setdefault(declaration.name, []).append(declaration)

    closure_cache: dict[str, set[str]] = {}

    def closure(name: str, active: set[str] | None = None) -> set[str]:
        name = simple_type(name)
        if name in closure_cache:
            return set(closure_cache[name])
        active = set() if active is None else active
        if name in active:
            return {name}
        active.add(name)
        result = {name}
        result.update(IMPLICIT_BASES.get(name, set()))
        for declaration in by_name.get(name, []):
            for base in declaration.bases:
                result.update(closure(base, active))
        active.remove(name)
        closure_cache[name] = set(result)
        return result

    def layers(name: str) -> set[int]:
        ancestors = closure(name)
        return {level for marker, level in LAYER_LEVELS.items() if marker in ancestors}

    def add(source: Source, offset: int, severity: str, code: str, message: str) -> None:
        findings.append(
            Finding(
                path=str(source.path).replace("\\", "/"),
                line=line_number(source.text, offset),
                severity=severity,
                code=code,
                message=message,
            )
        )

    uses_kdi = any(
        re.search(r"\b(?:Kylin\.DI|ScopeBuilder|IDomainServiceLayer|IApplicationServiceLayer|IViewModelLayer|IDataLayer)\b", source.code)
        for source in sources
    )
    if not uses_kdi:
        findings.append(
            Finding(
                path=".",
                line=1,
                severity="ERROR",
                code="KSI001",
                message="No KDI usage was detected in the selected scan paths.",
            )
        )
        return findings

    uses_layered = any(
        marker in source.code for source in sources for marker in LAYER_LEVELS
    )
    if not uses_layered:
        findings.append(
            Finding(
                path=".",
                line=1,
                severity="WARN",
                code="KSI101",
                message="KDI Layered markers were not detected; layer checks are limited.",
            )
        )

    for declaration in declarations:
        declared_layers = layers(declaration.name)
        if len(declared_layers) > 1:
            names = ", ".join(LAYER_NAMES[level] for level in sorted(declared_layers))
            add(
                declaration.source,
                declaration.start,
                "ERROR",
                "KSI002",
                f"{declaration.name} belongs to multiple KDI layers: {names}.",
            )

    declarations_by_source: dict[Path, list[TypeDecl]] = {}
    for declaration in declarations:
        declarations_by_source.setdefault(declaration.source.path, []).append(declaration)

    for source in sources:
        source_declarations = declarations_by_source.get(source.path, [])
        framework_source = bool(re.search(r"\bnamespace\s+Kylin\.DI\s*(?:\{|$)", source.code, re.MULTILINE))

        def host_at(offset: int) -> TypeDecl | None:
            candidates = [decl for decl in source_declarations if decl.start <= offset < decl.end]
            return min(candidates, key=lambda decl: decl.end - decl.start) if candidates else None

        self_inject_patterns = (
            re.compile(r"\bthis\s*\.\s*Inject\s*\("),
            re.compile(r"\bDependencyInjector\s*\.\s*Inject\s*\(\s*this\b"),
        )
        for pattern in self_inject_patterns:
            for match in pattern.finditer(source.code):
                add(source, match.start(), "ERROR", "KSI003", "Self-injection is forbidden; the creator must inject the object.")

        if not is_boundary(source.path) and not framework_source:
            locator_patterns = (
                (re.compile(r"\bLifetimeScope\s*\.\s*Find\w*\s*\("), "LifetimeScope.Find"),
                (re.compile(r"\bKDI\s*\.\s*RootScope\b"), "KDI.RootScope"),
                (re.compile(r"\b_?[A-Za-z_]*scope\s*\.\s*Resolve\s*(?:<|\()", re.IGNORECASE), "Scope.Resolve"),
                (re.compile(r"\bScope\s*\.\s*Resolve\s*(?:<|\()"), "DIBehaviour.Scope.Resolve"),
            )
            seen_offsets: set[int] = set()
            for pattern, label in locator_patterns:
                for match in pattern.finditer(source.code):
                    if match.start() in seen_offsets:
                        continue
                    seen_offsets.add(match.start())
                    add(
                        source,
                        match.start(),
                        "ERROR",
                        "KSI004",
                        f"{label} appears outside an explicit composition boundary.",
                    )

        for match in INJECT_FIELD_RE.finditer(source.code):
            host = host_at(match.start())
            dependency = simple_type(match.group("type"))
            if dependency in {"IScope", "IResolver", "Scope"}:
                add(
                    source,
                    match.start(),
                    "ERROR",
                    "KSI005",
                    f"Resolver authority {dependency} must not be injected.",
                )

            if host is None:
                add(source, match.start(), "WARN", "KSI102", f"Could not determine the host type for injected field {match.group('name')}.")
                continue

            host_ancestors = closure(host.name)
            if "IInjectable" not in host_ancestors:
                unknown_bases = [
                    base
                    for base in host.bases
                    if base not in by_name
                    and base not in KNOWN_EXTERNAL_BASES
                    and base not in IMPLICIT_BASES
                ]
                severity = "WARN" if unknown_bases else "ERROR"
                add(
                    source,
                    match.start(),
                    severity,
                    "KSI006" if severity == "ERROR" else "KSI103",
                    f"{host.name} has [Inject] fields but no detected IInjectable marker.",
                )

            host_layers = layers(host.name)
            dependency_layers = layers(dependency)
            if len(host_layers) == 1 and len(dependency_layers) == 1:
                host_level = next(iter(host_layers))
                dependency_level = next(iter(dependency_layers))
                if dependency_level <= host_level:
                    relation = "same-layer" if dependency_level == host_level else "upward"
                    add(
                        source,
                        match.start(),
                        "ERROR",
                        "KSI007",
                        f"{host.name} ({LAYER_NAMES[host_level]}) has {relation} injection of "
                        f"{dependency} ({LAYER_NAMES[dependency_level]}).",
                    )
            elif dependency in by_name and not dependency_layers and dependency != "IInstantiator":
                add(
                    source,
                    match.start(),
                    "WARN",
                    "KSI104",
                    f"Injected local contract {dependency} is unclassified; verify it is a narrow infrastructure/config boundary.",
                )

        for bind in BIND_START_RE.finditer(source.code):
            end = find_statement_end(source.code, bind.end())
            chain = source.code[bind.end():end]
            service = simple_type(bind.group("service"))
            has_lifetime = bool(re.search(r"\.\s*As(?:Singleton|Scoped|Transient)\s*\(", chain))
            has_instance_terminal = bool(re.search(r"\.\s*FromInstance\s*\(", chain))
            if not has_lifetime and not has_instance_terminal:
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    "KSI008",
                    f"Bind<{service}> has no lifetime or FromInstance terminator.",
                )

            is_transient = bool(re.search(r"\.\s*AsTransient\s*\(", chain))
            is_entry_point = bool(re.search(r"\.\s*AsEntryPoint\s*\(", chain))
            if is_transient and is_entry_point:
                add(source, bind.start(), "ERROR", "KSI009", f"Bind<{service}> combines Transient with EntryPoint.")

            implementation_match = re.search(r"\.\s*To\s*<(?P<type>[^>]+)>", chain)
            implementation = simple_type(implementation_match.group("type")) if implementation_match else service
            if is_transient and TRACKED_TRANSIENT_BASES.intersection(closure(implementation)):
                tracked = ", ".join(sorted(TRACKED_TRANSIENT_BASES.intersection(closure(implementation))))
                add(
                    source,
                    bind.start(),
                    "ERROR",
                    "KSI010",
                    f"Transient {implementation} has tracked lifetime interfaces: {tracked}.",
                )

            if re.search(r"\.\s*FromFactory\s*\(", chain) and re.search(r"\.\s*Resolve\s*(?:<|\()", chain):
                add(
                    source,
                    bind.start(),
                    "WARN",
                    "KSI105",
                    f"Bind<{service}> resolves dependencies inside FromFactory; analyzer visibility is bypassed.",
                )

        if not is_boundary(source.path):
            managed_names = {
                declaration.name
                for declaration in declarations
                if layers(declaration.name) or "IDependencyObject" in closure(declaration.name)
            }
            for match in re.finditer(r"\bnew\s+(?:global::)?(?:[A-Za-z_]\w*\.)*(?P<type>[A-Za-z_]\w*)\s*(?:<[^;()]+>)?\s*\(", source.code):
                created = match.group("type")
                if created not in managed_names:
                    continue
                add(
                    source,
                    match.start(),
                    "ERROR",
                    "KSI011",
                    f"Direct construction of KDI-managed type {created} appears outside a composition boundary.",
                )

        if not framework_source:
            for match in re.finditer(r"\b(?:UnityEngine\s*\.\s*)?Object\s*\.\s*Instantiate\s*\(", source.code):
                add(
                    source,
                    match.start(),
                    "WARN",
                    "KSI106",
                    "Direct Object.Instantiate detected; use IInstantiator when the hierarchy contains IInjectable components.",
                )

    return sorted(findings)


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
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"error: project root does not exist: {project}", file=sys.stderr)
        return 2

    try:
        roots = discover_scan_roots(project, args.include)
        sources = read_sources(project, roots)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    findings = audit(project, sources)
    versions = package_versions(project)
    errors = sum(finding.severity == "ERROR" for finding in findings)
    warnings = sum(finding.severity == "WARN" for finding in findings)

    if args.json:
        print(
            json.dumps(
                {
                    "project": str(project),
                    "files_scanned": len(sources),
                    "packages": versions,
                    "errors": errors,
                    "warnings": warnings,
                    "findings": [asdict(finding) for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        package_text = ", ".join(f"{name}={version}" for name, version in sorted(versions.items()))
        print(f"KDI architecture audit: {len(sources)} C# files")
        print(f"Packages: {package_text or 'not resolved from manifests'}")
        for finding in findings:
            print(f"{finding.severity} {finding.code} {finding.path}:{finding.line} {finding.message}")
        print(f"Summary: {errors} error(s), {warnings} warning(s)")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
