"""Flatten a supported pure-Python package into one Ruff-formatted file.

Usage: one-script PACKAGE_DIR PACKAGE_NAME [-o bundled.py] [--exclude GLOB]
See the README for the supported source contract and limitations.
"""

import argparse
import ast
import heapq
import keyword
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path, PurePosixPath


class BundleError(Exception):
    """An input, source-contract, dependency, or output error."""


def _fail(path: Path, node: ast.AST, reason: str) -> None:
    raise BundleError(f"{path.as_posix()}:{node.lineno}: {reason}")


class FileContents:
    """Source and validated statements from one module."""

    def __init__(self, path: Path):
        self.path = path
        self.imports: list[ast.stmt] = []
        self.internal_imports: list[ast.ImportFrom] = []
        self.future_features: set[str] = set()
        self.definitions: list[tuple[ast.stmt, str]] = []
        try:
            with tokenize.open(path) as stream:
                self.source = stream.read()
            self.tree = ast.parse(self.source, filename=str(path))
            # Parsing alone accepts some invalid programs (e.g. return outside a function).
            compile(self.tree, str(path), "exec", dont_inherit=True)
        except SyntaxError as exc:
            raise BundleError(f"{path}:{exc.lineno or 1}: {exc.msg}") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise BundleError(f"{path}: cannot read Python source: {exc}") from exc


def is_self_import(node: ast.stmt, package_name: str) -> bool:
    """Identify relative and explicitly package-qualified imports only."""
    if isinstance(node, ast.ImportFrom):
        return bool(node.level or (node.module or "").split(".")[0] == package_name)
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == package_name for alias in node.names)
    return False


def extract_source_text(source: str, node: ast.AST) -> str:
    """Keep exact statement spans, decorators, and adjacent leading comments."""
    lines = source.splitlines(keepends=True)
    decorators = getattr(node, "decorator_list", ())
    start = min([node.lineno] + [item.lineno for item in decorators]) - 1
    if decorators:
        # A parenthesized decorator expression can begin below its @ token.
        while start > 0 and not lines[start].lstrip().startswith("@"):
            start -= 1
    start_col = 0 if decorators else node.col_offset
    end = node.end_lineno - 1
    # AST columns are UTF-8 byte offsets, including for non-ASCII identifiers.
    encoded = [line.encode("utf-8") for line in lines]
    if start == end:
        segment = encoded[start][start_col : node.end_col_offset].decode("utf-8")
    else:
        segment = (
            encoded[start][start_col:]
            + b"".join(encoded[start + 1 : end])
            + encoded[end][: node.end_col_offset]
        ).decode("utf-8")
    tail = encoded[end][node.end_col_offset :].decode("utf-8").rstrip("\r\n")
    if tail.lstrip().startswith("#"):
        segment += tail
    if start_col == 0:
        comment_start = start
        for index in range(start - 1, -1, -1):
            stripped = lines[index].strip()
            if stripped.startswith("#"):
                # Output is UTF-8; an input coding cookie must not change its decoding.
                if index < 2 and re.search(r"coding[:=]\s*[-\w.]+", stripped):
                    break
                comment_start = index
            elif stripped:
                break
        segment = "".join(lines[comment_start:start]) + segment
    return segment


def definition_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def collect_file(path: Path, package_name: str) -> FileContents:
    fc = FileContents(path)
    is_init = path.name == "__init__.py"
    seen_definition = False
    for index, node in enumerate(fc.tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if seen_definition:
                _fail(path, node, "imports must precede definitions and assignments")
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                _fail(
                    path,
                    node,
                    "wildcard imports are unsupported; import explicit symbols",
                )
            if is_self_import(node, package_name):
                if isinstance(node, ast.Import):
                    _fail(
                        path,
                        node,
                        "internal module-object imports are unsupported; use from package.module import Symbol",
                    )
                if any(
                    alias.asname and alias.asname != alias.name for alias in node.names
                ):
                    _fail(
                        path,
                        node,
                        "renamed internal aliases are unsupported; import the original symbol name",
                    )
                fc.internal_imports.append(node)
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                fc.future_features.update(alias.name for alias in node.names)
                if any(alias.asname for alias in node.names):
                    _fail(path, node, "aliased future imports are unsupported")
                fc.imports.append(node)
            else:
                # Normalize to one binding per node, so deduplication and conflict checks agree.
                for alias in node.names:
                    if isinstance(node, ast.Import):
                        part = ast.Import(names=[alias])
                    else:
                        part = ast.ImportFrom(
                            module=node.module, names=[alias], level=node.level
                        )
                    fc.imports.append(ast.copy_location(part, node))
            continue

        seen_definition = True
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_init:
                _fail(
                    path,
                    node,
                    f"__init__.py must contain only imports — found {type(node).__name__} {node.name!r}. Move it into a submodule.",
                )
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if is_init:
                _fail(
                    path,
                    node,
                    "__init__.py must contain only imports — found a module-level assignment. Move it into a submodule.",
                )
            if isinstance(node, ast.AugAssign):
                valid = isinstance(node.target, ast.Name)
            else:
                valid = definition_name(node) is not None
            if not valid:
                _fail(
                    path,
                    node,
                    "unsupported assignment target; assign to one plain name per statement",
                )
            if isinstance(node, ast.AnnAssign) and node.value is None:
                _fail(
                    path,
                    node,
                    "annotation-only assignments are unsupported; provide a value",
                )
        else:
            extra = ""
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
            ):
                extra = (
                    ' (looks like an `if __name__ == "__main__":` block — '
                    "move it into your entry-point script, not a bundled module)"
                )
            _fail(
                path,
                node,
                f"unsupported top-level {type(node).__name__}{extra}. Only imports, classes, functions, and module-level assignments are allowed.",
            )
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)) and is_self_import(
                child, package_name
            ):
                _fail(
                    path,
                    child,
                    "nested internal imports are unsupported; move the import to module scope",
                )
            if isinstance(child, ast.NamedExpr):
                _fail(
                    path,
                    child,
                    "assignment expressions are unsupported; use a separate assignment",
                )
        fc.definitions.append((node, extract_source_text(fc.source, node)))
    return fc


def normalize_exclude_patterns(exclude_patterns: list[str] | None) -> tuple[str, ...]:
    """Discard blank glob patterns and trim surrounding whitespace."""
    return tuple(
        pattern.strip() for pattern in (exclude_patterns or []) if pattern.strip()
    )


def path_matches_exclude(
    path: Path, package_dir: Path, exclude_patterns: tuple[str, ...]
) -> bool:
    relative = PurePosixPath(path.relative_to(package_dir).as_posix())
    rooted = PurePosixPath(package_dir.name) / relative
    absolute = PurePosixPath(path.absolute().as_posix())
    patterns = (
        pattern.replace("\\", "/") if os.name == "nt" else pattern
        for pattern in exclude_patterns
    )
    return any(
        relative.match(pattern) or rooted.match(pattern) or absolute.match(pattern)
        for pattern in patterns
    )


def collect_python_files(
    package_dir: Path,
    exclude_patterns: tuple[str, ...],
    *,
    include_empty_inits: bool = False,
) -> list[Path]:
    """Discover files deterministically, reporting traversal failures."""

    def onerror(exc: OSError) -> None:
        raise exc

    files = []
    for directory, _subdirs, names in os.walk(package_dir, onerror=onerror):
        for name in names:
            path = Path(directory) / name
            if path.suffix == ".py" and not path_matches_exclude(
                path, package_dir, exclude_patterns
            ):
                if include_empty_inits or name != "__init__.py" or path.stat().st_size:
                    files.append(path)
    return sorted(files)


def import_key(node: ast.stmt) -> str:
    return ast.unparse(node).strip()


def _bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {
            part.id
            for target in targets
            for part in ast.walk(target)
            if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store)
        }
    if isinstance(node, ast.TypeAlias):
        return {node.name.id}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {
            alias.asname
            or (
                alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
            )
            for alias in node.names
        }
    return set()


class _EagerNames(ast.NodeVisitor):
    """Names looked up now, with class and comprehension scopes accounted for."""

    def __init__(self, eager_annotations: bool):
        self.names: set[str] = set()
        self.bound: set[str] = set()
        self.in_class = False
        self.type_params: set[str] = set()
        self.eager_annotations = eager_annotations

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id not in self.bound:
            self.names.add(node.id)

    def _arguments(
        self, args: ast.arguments, type_params: set[str] | None = None
    ) -> None:
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)
        previous = self.bound
        self.bound = previous | (type_params or set())
        if self.eager_annotations:
            for arg in [
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                args.vararg,
                args.kwarg,
            ]:
                if arg is not None and arg.annotation is not None:
                    self.visit(arg.annotation)
        self.bound = previous

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        previous = self.bound
        type_params = {param.name for param in node.type_params}
        self._arguments(node.args, type_params)
        self.bound = previous | type_params
        if self.eager_annotations and node.returns is not None:
            self.visit(node.returns)
        self.bound = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._arguments(node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        previous, was_class, previous_types = (
            self.bound,
            self.in_class,
            self.type_params,
        )
        self.type_params = previous_types | {param.name for param in node.type_params}
        self.bound = previous | self.type_params
        for expr in [*node.bases, *(item.value for item in node.keywords)]:
            self.visit(expr)
        self.bound = self.type_params.copy()
        self.in_class = True
        for statement in node.body:
            self.visit(statement)
            self.bound.update(_bound_names(statement))
        self.bound, self.in_class, self.type_params = (
            previous,
            was_class,
            previous_types,
        )

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        # Type aliases inside a class have deferred right-hand sides.
        pass

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        if self.eager_annotations:
            self.visit(node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id not in self.bound:
            self.names.add(node.target.id)
        self.visit(node.value)

    def _comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp) -> None:
        first, *rest = node.generators
        self.visit(first.iter)
        previous, was_class = self.bound, self.in_class
        self.bound = self.type_params.copy() if was_class else previous.copy()
        self.in_class = False
        for index, generator in enumerate([first, *rest]):
            if index:
                self.visit(generator.iter)
            self.bound.update(
                part.id
                for part in ast.walk(generator.target)
                if isinstance(part, ast.Name)
            )
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.bound, self.in_class = previous, was_class

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit(node.generators[0].iter)


def _hard_deps(node: ast.stmt, eager_annotations: bool | None = None) -> set[str]:
    if eager_annotations is None:
        eager_annotations = sys.version_info < (3, 14)
    visitor = _EagerNames(eager_annotations)
    visitor.visit(node)
    return visitor.names


def _soft_deps(node: ast.stmt, eager_annotations: bool | None = None) -> set[str]:
    names = {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }
    return names - _hard_deps(node, eager_annotations)


def topo_sort_definitions(
    ordered_defs: list[tuple[Path, ast.stmt, str]],
    eager_annotations: dict[Path, bool] | None = None,
) -> list[tuple[Path, ast.stmt, str]]:
    """Preserve module order and place required values before their consumers."""
    size = len(ordered_defs)
    writers: dict[str, list[int]] = {}
    for index, (_path, node, _source) in enumerate(ordered_defs):
        name = (
            node.target.id if isinstance(node, ast.AugAssign) else definition_name(node)
        )
        if name is not None:
            writers.setdefault(name, []).append(index)
    children: list[set[int]] = [set() for _ in range(size)]
    indegree = [0] * size
    soft: list[set[int]] = [set() for _ in range(size)]

    def edge(before: int, after: int) -> None:
        if after not in children[before]:
            children[before].add(after)
            indegree[after] += 1

    previous_by_path: dict[Path, int] = {}
    for index, (path, node, _source) in enumerate(ordered_defs):
        if path in previous_by_path:
            edge(previous_by_path[path], index)
        previous_by_path[path] = index
        eager = eager_annotations.get(path) if eager_annotations is not None else None
        for name in _hard_deps(node, eager):
            candidates = writers.get(name, [])
            if not candidates:
                continue
            # Local reads observe preceding local writes. Other modules see the final value.
            local = [
                candidate
                for candidate in candidates
                if ordered_defs[candidate][0] == path and candidate < index
            ]
            provider = local[-1] if local else candidates[-1]
            edge(provider, index)
        for name in _soft_deps(node, eager):
            if name in writers and writers[name][-1] != index:
                soft[index].add(writers[name][-1])

    # Update only affected priorities; large independent packages need no heap rescans.
    soft_children: list[set[int]] = [set() for _ in range(size)]
    soft_remaining = [len(dependencies) for dependencies in soft]
    for child, dependencies in enumerate(soft):
        for provider in dependencies:
            soft_children[provider].add(child)
    emitted: set[int] = set()
    result = []
    ready = [
        (soft_remaining[index], index) for index in range(size) if indegree[index] == 0
    ]
    heapq.heapify(ready)
    while ready:
        priority, index = heapq.heappop(ready)
        if index in emitted or priority != soft_remaining[index]:
            continue
        emitted.add(index)
        result.append(ordered_defs[index])
        for child in children[index]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (soft_remaining[child], child))
        for child in soft_children[index]:
            soft_remaining[child] -= 1
            if child not in emitted and indegree[child] == 0:
                heapq.heappush(ready, (soft_remaining[child], child))
    if len(result) != size:
        blocked = [
            f"{path}:{node.lineno} ({definition_name(node) or ast.unparse(node)})"
            for index, (path, node, _source) in enumerate(ordered_defs)
            if index not in emitted
        ]
        raise BundleError(
            "hard dependency cycle or incompatible source order: " + ", ".join(blocked)
        )
    return result


def _module_name(path: Path, package_dir: Path, package_name: str) -> str:
    parts = list(path.relative_to(package_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([package_name, *parts])


def _import_target(module: str, fc: FileContents, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module
    parent = module if fc.path.name == "__init__.py" else module.rsplit(".", 1)[0]
    parts = parent.split(".")
    if node.level > len(parts):
        _fail(fc.path, node, "relative import escapes the input package")
    return ".".join(
        parts[: len(parts) - node.level + 1] + ([node.module] if node.module else [])
    )


def _external_binding(node: ast.stmt, alias: ast.alias) -> tuple[str, tuple[str, ...]]:
    if isinstance(node, ast.Import):
        name = alias.asname or alias.name.split(".")[0]
        target = alias.name if alias.asname else alias.name.split(".")[0]
        return name, ("module", target)
    return alias.asname or alias.name, ("symbol", node.module, alias.name)


def _validate_bindings(
    all_files: list[FileContents], package_dir: Path, package_name: str
) -> None:
    # Each exported binding is either a definition, an external import, or a link to a re-export.
    exports: dict[
        str, dict[str, list[tuple[tuple[str, ...], FileContents, ast.stmt]]]
    ] = {package_name: {}}
    for fc in all_files:
        module = _module_name(fc.path, package_dir, package_name)
        table = exports.setdefault(module, {})
        parts = module.split(".")
        for length in range(1, len(parts)):
            exports.setdefault(".".join(parts[:length]), {})
        for node, _source in fc.definitions:
            name = definition_name(node)
            if name:
                table.setdefault(name, []).append(
                    (("definition", str(fc.path), name), fc, node)
                )
        for node in fc.imports:
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            for alias in node.names:
                name, identity = _external_binding(node, alias)
                table.setdefault(name, []).append((identity, fc, node))
        for node in fc.internal_imports:
            target = _import_target(module, fc, node)
            for alias in node.names:
                table.setdefault(alias.name, []).append(
                    (("link", target, alias.name), fc, node)
                )

    def resolve(
        module: str,
        name: str,
        trail: set[tuple[str, str]],
        fc: FileContents,
        node: ast.stmt,
    ) -> tuple[str, ...]:
        key = (module, name)
        if key in trail:
            _fail(fc.path, node, f"cyclic internal re-export of {module}.{name}")
        if module not in exports:
            _fail(fc.path, node, f"internal module {module!r} is missing or excluded")
        entries = exports[module].get(name, [])
        if not entries:
            if f"{module}.{name}" in exports:
                _fail(
                    fc.path,
                    node,
                    "internal module-object imports are unsupported; import an explicit symbol",
                )
            _fail(
                fc.path, node, f"internal symbol {module}.{name} is missing or excluded"
            )
        identities = set()
        for identity, origin, statement in entries:
            if identity[0] == "link":
                identity = resolve(
                    identity[1], identity[2], trail | {key}, origin, statement
                )
            identities.add(identity)
        if len(identities) != 1:
            _fail(fc.path, node, f"conflicting global binding: {name!r}")
        return identities.pop()

    globals_seen: dict[str, tuple[tuple[str, ...], Path]] = {}
    for module, table in exports.items():
        for name, entries in table.items():
            _identity, fc, node = entries[0]
            identity = resolve(module, name, set(), fc, node)
            if name in globals_seen and globals_seen[name][0] != identity:
                _fail(
                    fc.path,
                    node,
                    f"conflicting global binding: {name!r}, also bound in {globals_seen[name][1]}",
                )
            globals_seen[name] = (identity, fc.path)


def _format_bundle(source: str) -> str:
    """Format the completed bundle with Ruff's defaults, independent of local config."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-m",
                "ruff",
                "format",
                "--isolated",
                "--no-cache",
                "--target-version",
                f"py{sys.version_info.major}{sys.version_info.minor}",
                "--stdin-filename",
                "bundled.py",
                "-",
            ],
            input=source,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
    except OSError as exc:
        raise BundleError(f"could not run Ruff formatter: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or f"exit status {exc.returncode}"
        raise BundleError(f"Ruff formatting failed: {detail}") from exc
    return result.stdout


def _bundle(
    package_dir: Path, package_name: str, exclude_patterns: list[str] | None
) -> str:
    if not package_name.isidentifier() or keyword.iskeyword(package_name):
        raise BundleError(
            "package_name must be a valid top-level Python identifier, not a keyword"
        )
    if not package_dir.is_dir():
        raise BundleError(f"{package_dir}: input must be an existing directory")
    paths = collect_python_files(
        package_dir,
        normalize_exclude_patterns(exclude_patterns),
        include_empty_inits=True,
    )
    modules: dict[str, Path] = {}
    all_files = []
    for path in paths:
        module = _module_name(path, package_dir, package_name)
        if module in modules:
            raise BundleError(
                f"ambiguous module path {module!r}: {modules[module]} and {path}"
            )
        modules[module] = path
        all_files.append(collect_file(path, package_name))
    namespace_parents = {
        ".".join(module.split(".")[:length])
        for module in modules
        for length in range(1, len(module.split(".")))
    }
    for module, path in modules.items():
        if path.name != "__init__.py" and module in namespace_parents:
            raise BundleError(
                f"{path}: ambiguous module path {module!r} is also a package directory"
            )

    ordered_defs = []
    definitions: dict[str, tuple[Path, ast.stmt]] = {}
    future_sets = {frozenset(fc.future_features) for fc in all_files if fc.definitions}
    if len(future_sets) > 1:
        raise BundleError(
            "inconsistent __future__ features across modules containing definitions; use the same future imports in every such module"
        )
    active_futures = set(next(iter(future_sets), frozenset()))
    for fc in all_files:
        if future_sets and not fc.definitions and fc.future_features - active_futures:
            raise BundleError(
                f"{fc.path}: initializer/import-only future features would change other modules"
            )
        for node, source in fc.definitions:
            name = definition_name(node)
            if name:
                if name in definitions:
                    other_path, other_node = definitions[name]
                    _fail(
                        fc.path,
                        node,
                        f"name collision: {name!r}, also defined at {other_path}:{other_node.lineno}",
                    )
                definitions[name] = (fc.path, node)
            elif isinstance(node, ast.AugAssign):
                owner = definitions.get(node.target.id)
                if (
                    owner is None
                    or owner[0] != fc.path
                    or not isinstance(owner[1], (ast.Assign, ast.AnnAssign))
                ):
                    _fail(
                        fc.path,
                        node,
                        "augmented assignment requires an earlier named assignment in the same module",
                    )
            ordered_defs.append((fc.path, node, source))
    _validate_bindings(all_files, package_dir, package_name)
    eager = {
        fc.path: sys.version_info < (3, 14) and "annotations" not in fc.future_features
        for fc in all_files
    }
    ordered_defs = topo_sort_definitions(ordered_defs, eager)

    imports: dict[str, ast.stmt] = {}
    for fc in all_files:
        for node in fc.imports:
            imports.setdefault(import_key(node), node)
    futures = [
        text
        for text, node in imports.items()
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]
    ordinary = [text for text in imports if text not in futures]
    out = [
        f'"""Bundled from package {package_name!r}."""',
        "",
        *sorted(futures),
        *ordinary,
    ]
    last_path = None
    for path, _node, source in ordered_defs:
        if path != last_path:
            out.extend(["", f"# imported from: {path.as_posix()}"])
            last_path = path
        out.extend([source, ""])
    text = _format_bundle("\n".join(out).rstrip() + "\n")
    try:
        compile(text, "<one-script bundle>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise BundleError(f"generated bundle:{exc.lineno}: {exc.msg}") from exc
    return text


def bundle(
    package_dir: Path, package_name: str, exclude_patterns: list[str] | None = None
) -> str:
    """Return Ruff-formatted source without executing it; raise BundleError on failure."""
    try:
        return _bundle(Path(package_dir), package_name, exclude_patterns)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BundleError(f"{package_dir}: {exc}") from exc


def _write_output(output: Path, text: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="one-script", description=__doc__)
    parser.add_argument("package_dir", type=Path, help="path to the package directory")
    parser.add_argument(
        "package_name", help="top-level package name used by internal imports"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("bundled.py"),
        help="destination outside the package (default: bundled.py)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude matching package-relative paths; repeat for multiple patterns",
    )
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        if output.resolve().is_relative_to(args.package_dir.resolve()):
            raise BundleError("output must be outside the input package directory")
        if not output.parent.is_dir():
            raise BundleError(
                f"{args.output.parent}: output parent must be an existing directory"
            )
        if output.is_dir():
            raise BundleError(f"{args.output}: output must be a file, not a directory")
        text = bundle(args.package_dir, args.package_name, args.exclude)
        _write_output(output, text)
    except (BundleError, OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
