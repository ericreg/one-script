"""
Bundles a pure-Python package into a single flat file.

Assumptions:
  - All imports are at the top of each file.
  - Each file contains plain top-level classes / functions (and optionally
    module-level constants).
  - Class and function names are unique across the whole package; collisions
    are a hard error.
  - Imports of the package itself (e.g. `from my_package import X`) are
    dropped, since X will live in the same global namespace after bundling.
  - __init__.py files are only used for imports, not definitions or executable code.

Usage:
    python main.py <package_dir> <package_name> -o bundled.py
"""

import argparse
import ast
import sys
from pathlib import Path, PurePosixPath


class FileContents:
    """Parsed contents of one source file."""

    def __init__(self, path: Path):
        self.path = path
        self.imports: list[ast.stmt] = []  # import / from-import nodes
        self.definitions: list[tuple[ast.stmt, str]] = []  # (node, source_text) tuples
        self.source: str = path.read_text()
        self.tree: ast.Module = ast.parse(self.source, filename=str(path))


def is_self_import(node: ast.stmt, package_name: str, bundled_modules: set[str] | None = None) -> bool:
    """
    True if the import targets our own package or a bundled module and should be dropped.

    A bundled module name that is ALSO a stdlib name (e.g. a local file
    named `queue.py` or `json.py`) is treated as stdlib — the import is
    preserved. The package itself (`package_name`) is always treated as
    self even if it happens to clash with a stdlib name.
    """
    stdlib = sys.stdlib_module_names

    def is_bundled_nonstdlib(root: str) -> bool:
        return bool(bundled_modules and root in bundled_modules and root not in stdlib)

    if isinstance(node, ast.ImportFrom):
        # `from my_package...` or `from my_package.sub import X`
        if node.module is not None:
            root = node.module.split(".")[0]
            if root == package_name:
                return True
            if is_bundled_nonstdlib(root):
                return True
        # `from . import X`  (relative import inside the package)
        if node.level and node.level > 0:
            return True
    elif isinstance(node, ast.Import):
        # `import my_package` or `import my_package.sub`
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root == package_name:
                return True
            if is_bundled_nonstdlib(root):
                return True
    return False


class BundleError(Exception):
    """Raised when a source file violates the bundler's contract."""


def _fail(path: Path, node: ast.AST, reason: str) -> None:
    raise BundleError(f"{path}:{node.lineno}: {reason}")


def extract_source_text(source: str, node: ast.AST) -> str:
    """Extract source text for a node, including decorators and any leading comment lines."""
    lines = source.split("\n")

    # The node's `lineno` points at the `def`/`class` keyword, NOT the first
    # decorator. Use the earliest decorator line as the real start.
    start_line = node.lineno - 1  # AST uses 1-indexing
    decorators = getattr(node, "decorator_list", None) or []
    for dec in decorators:
        start_line = min(start_line, dec.lineno - 1)

    # Look backwards for comment lines (but stop at other code)
    comment_start = start_line
    for i in range(start_line - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            comment_start = i
        elif stripped == "":
            continue
        else:
            break

    end_line = node.end_lineno if node.end_lineno else node.lineno
    return "\n".join(lines[comment_start:end_line])


def collect_file(path: Path, package_name: str, bundled_modules: set[str] | None = None) -> FileContents:
    fc = FileContents(path)
    is_init = path.name == "__init__.py"

    for node in fc.tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not is_self_import(node, package_name, bundled_modules):
                fc.imports.append(node)

        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_init:
                _fail(
                    path,
                    node,
                    f"__init__.py must contain only imports — found "
                    f"{type(node).__name__} {node.name!r}. Move it into a submodule.",
                )
            source_text = extract_source_text(fc.source, node)
            fc.definitions.append((node, source_text))

        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if is_init:
                _fail(
                    path,
                    node,
                    "__init__.py must contain only imports — found a module-level "
                    "assignment. Move it into a submodule.",
                )
            source_text = extract_source_text(fc.source, node)
            fc.definitions.append((node, source_text))

        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # module-level docstring or bare string literal — harmless, drop it.
            continue

        else:
            kind = type(node).__name__
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
                f"unsupported top-level {kind}{extra}. Only imports, classes, "
                f"functions, and module-level assignments are allowed.",
            )

    return fc


def normalize_exclude_patterns(exclude_patterns: list[str] | None) -> tuple[str, ...]:
    """Return non-empty glob patterns."""
    # keep cli defaults simple while ignoring accidental empty values
    if exclude_patterns is None:
        return ()
    return tuple(str(pattern).strip() for pattern in exclude_patterns if str(pattern).strip())


def path_matches_exclude(path: Path, package_dir: Path, exclude_patterns: tuple[str, ...]) -> bool:
    """Return true when a source path matches an exclude glob."""
    # match common package-relative glob patterns
    relative_path = PurePosixPath(path.relative_to(package_dir).as_posix())
    rooted_path = PurePosixPath(package_dir.name) / relative_path

    # also allow absolute path globs for scripted callers
    for pattern in exclude_patterns:
        if relative_path.match(pattern) or rooted_path.match(pattern) or path.match(pattern):
            return True
    return False


def collect_python_files(package_dir: Path, exclude_patterns: tuple[str, ...]) -> list[Path]:
    """Return bundled python files after excludes are applied."""
    # gather non-empty python modules in deterministic filesystem order
    py_files = sorted(p for p in package_dir.rglob("*.py") if p.name != "__init__.py" or p.stat().st_size > 0)

    # remove excluded files before deriving bundled module names
    return [path for path in py_files if not path_matches_exclude(path, package_dir, exclude_patterns)]


def import_key(node: ast.stmt) -> str:
    """A stable string key for deduplicating import nodes."""
    return ast.unparse(node).strip()


def definition_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None



def _hard_deps(node: ast.stmt) -> set[str]:
    """
    Names that MUST be defined before this node, or Python raises NameError
    at module import time.

    Hard dependencies are values evaluated at definition time:
      - Class base classes and decorators
      - Function decorators and default argument values
      - Right-hand side of module-level assignments

    Names referenced inside function/method bodies are NOT hard deps —
    they're resolved lazily when the function is called.
    """
    names: set[str] = set()

    def add_names_from_expr(expr: ast.AST) -> None:
        # Collect every Name and the leaf of every Attribute chain.
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                # foo.bar.Baz -> the leftmost Name (e.g. "foo") will already
                # be picked up by ast.walk; the leaf "Baz" is what we care
                # about for matching bundled definitions.
                names.add(sub.attr)

    if isinstance(node, ast.ClassDef):
        for base in node.bases:
            add_names_from_expr(base)
        for kw in node.keywords:
            add_names_from_expr(kw.value)
        for dec in node.decorator_list:
            add_names_from_expr(dec)

    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            add_names_from_expr(dec)
        # Default values are evaluated at def-time; the args/annotations
        # themselves are not (annotations stay strings or are looked up lazily).
        for default in node.args.defaults:
            add_names_from_expr(default)
        for default in node.args.kw_defaults:
            if default is not None:
                add_names_from_expr(default)

    elif isinstance(node, ast.Assign):
        add_names_from_expr(node.value)
    elif isinstance(node, ast.AnnAssign):
        if node.value is not None:
            add_names_from_expr(node.value)
    elif isinstance(node, ast.AugAssign):
        add_names_from_expr(node.value)

    return names


def _soft_deps(node: ast.stmt) -> set[str]:
    """
    Names referenced anywhere inside a definition that aren't hard deps.

    Used as a soft ordering hint to improve readability: if a class's
    methods reference another type, we'd like that type to come first
    when there's no conflicting hard constraint.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    # Subtract hard deps so soft is strictly the "extra" references.
    return names - _hard_deps(node)


def topo_sort_definitions(
    ordered_defs: list[tuple[Path, ast.stmt, str]],
) -> list[tuple[Path, ast.stmt, str]]:
    """
    Reorder definitions so that:
      1. Hard dependencies always precede their dependents (else NameError
         at import). Cycles here are an error.
      2. Soft dependencies (names referenced inside function/method bodies)
         are honored where possible, as a tiebreaker.

    Original order is the final tiebreaker, so unrelated code stays in place.
    """
    import heapq

    n = len(ordered_defs)

    # Map name -> index (only for top-level definitions we can match).
    name_to_idx: dict[str, int] = {}
    for idx, (_path, node, _src) in enumerate(ordered_defs):
        name = definition_name(node)
        if name is not None:
            name_to_idx[name] = idx

    # Build hard edges and soft edges. Edges go: dependency_idx -> dependent_idx.
    hard_children: list[set[int]] = [set() for _ in range(n)]
    soft_children: list[set[int]] = [set() for _ in range(n)]
    hard_indeg = [0] * n
    soft_indeg = [0] * n

    for idx, (_path, node, _src) in enumerate(ordered_defs):
        for dep_name in _hard_deps(node):
            dep_idx = name_to_idx.get(dep_name)
            if dep_idx is not None and dep_idx != idx:
                if idx not in hard_children[dep_idx]:
                    hard_children[dep_idx].add(idx)
                    hard_indeg[idx] += 1
        for dep_name in _soft_deps(node):
            dep_idx = name_to_idx.get(dep_name)
            if dep_idx is not None and dep_idx != idx:
                # Don't double-count if it's already a hard edge.
                if idx not in hard_children[dep_idx] and idx not in soft_children[dep_idx]:
                    soft_children[dep_idx].add(idx)
                    soft_indeg[idx] += 1

    # Priority for tie-breaking: prefer nodes whose soft deps are also
    # already emitted, then earliest original index.
    # Heap entry: (soft_remaining, original_idx, idx)
    ready: list[tuple[int, int, int]] = []

    def push_ready(idx: int) -> None:
        heapq.heappush(ready, (soft_indeg[idx], idx, idx))

    for idx in range(n):
        if hard_indeg[idx] == 0:
            push_ready(idx)

    result: list[tuple[Path, ast.stmt, str]] = []
    emitted_set: set[int] = set()

    while ready:
        # Pop the best candidate; recheck soft_indeg in case it dropped
        # since this entry was pushed (we re-push rather than mutate the heap).
        soft_at_push, _orig, idx = heapq.heappop(ready)
        if idx in emitted_set:
            continue
        if soft_at_push != soft_indeg[idx]:
            # Stale priority — re-push with current value.
            push_ready(idx)
            continue

        result.append(ordered_defs[idx])
        emitted_set.add(idx)

        # Releasing this node may unblock hard children and lower soft_indeg
        # for soft children.
        for child in hard_children[idx]:
            hard_indeg[child] -= 1
            if hard_indeg[child] == 0 and child not in emitted_set:
                push_ready(child)
        for child in soft_children[idx]:
            if child in emitted_set:
                continue
            soft_indeg[child] -= 1
            # If this child is already hard-ready, its priority just improved;
            # push a new entry (the stale one will be skipped on pop).
            if hard_indeg[child] == 0:
                push_ready(child)

    if len(result) != n:
        cyclic = [definition_name(ordered_defs[i][1]) for i in range(n) if i not in emitted_set]
        raise BundleError(
            "hard dependency cycle detected among definitions: " + ", ".join(repr(c) for c in cyclic if c)
        )

    return result


def bundle(package_dir: Path, package_name: str, exclude_patterns: list[str] | None = None) -> str:
    exclude_globs = normalize_exclude_patterns(exclude_patterns)
    py_files = collect_python_files(package_dir, exclude_globs)

    bundled_modules: set[str] = set()
    for path in py_files:
        if path.name != "__init__.py":
            bundled_modules.add(path.stem)

    all_files: list[FileContents] = []
    for path in py_files:
        if path.name == "__init__.py" and path.stat().st_size == 0:
            continue
        all_files.append(collect_file(path, package_name, bundled_modules))

    # merge imports (dedup by source-equivalent text)
    seen_imports: dict[str, ast.stmt] = {}
    for fc in all_files:
        for imp in fc.imports:
            seen_imports.setdefault(import_key(imp), imp)

    # collect definitions and check collisions
    by_name: dict[str, tuple[Path, ast.stmt]] = {}
    ordered_defs: list[tuple[Path, ast.stmt, str]] = []
    for fc in all_files:
        for node, source_text in fc.definitions:
            name = definition_name(node)
            if name is not None:
                if name in by_name:
                    other_path, _ = by_name[name]
                    raise ValueError(f"name collision: {name!r} defined in both {other_path} and {fc.path}")
                by_name[name] = (fc.path, node)
            ordered_defs.append((fc.path, node, source_text))

    # topologically sort so subclasses come after their bundled bases
    ordered_defs = topo_sort_definitions(ordered_defs)

    # emit
    out: list[str] = []
    out.append(f'"""Bundled from package {package_name!r}."""')
    out.append("")

    plain = sorted(
        (n for n in seen_imports.values() if isinstance(n, ast.Import)),
        key=import_key,
    )
    froms = sorted(
        (n for n in seen_imports.values() if isinstance(n, ast.ImportFrom)),
        key=import_key,
    )
    for imp in plain + froms:
        out.append(ast.unparse(imp))
    if plain or froms:
        out.append("")

    last_path: Path | None = None
    for path, node, source_text in ordered_defs:
        if path != last_path:
            out.append("")
            out.append(f"# imported from: {path}")
            last_path = path
        out.append(source_text)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package_dir", type=Path, help="path to the package directory")
    ap.add_argument("package_name", help="top-level package name to strip from imports")
    ap.add_argument("-o", "--output", type=Path, default=Path("bundled.py"))
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude package-relative paths matching this glob; repeat to exclude multiple patterns",
    )
    args = ap.parse_args()

    try:
        text = bundle(args.package_dir, args.package_name, exclude_patterns=args.exclude)
    except BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    args.output.write_text(text)

    # print out the number of lines written
    print(f"wrote {args.output} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()