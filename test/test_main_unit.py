import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gaffer

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OUTPUTS = Path(__file__).resolve().parent / "output"


def fixture_package(name: str) -> Path:
    return FIXTURES / name


def parse_fixture_module(package: str, module: str) -> tuple[Path, ast.Module]:
    path = fixture_package(package) / f"{module}.py"
    return path, ast.parse(path.read_text(), filename=str(path))


def body_by_name(tree: ast.Module) -> dict[str, ast.stmt]:
    result: dict[str, ast.stmt] = {}
    for node in tree.body:
        name = gaffer.definition_name(node)
        if name is not None:
            result[name] = node
    return result


class TestMainUnit:
    def test_normalize_exclude_patterns_discards_empty_values(self):
        assert gaffer.normalize_exclude_patterns(
            [" *.py ", "", "  ", "internal/*.py", "nested/drop.py "]
        ) == (
            "*.py",
            "internal/*.py",
            "nested/drop.py",
        )
        assert gaffer.normalize_exclude_patterns(None) == ()

    def test_path_matches_exclude_accepts_relative_and_rooted_globs(self):
        package_dir = fixture_package("exclude_pkg")
        keep = package_dir / "keep.py"
        nested = package_dir / "internal" / "drop.py"

        assert gaffer.path_matches_exclude(keep, package_dir, ("keep.py",))
        assert gaffer.path_matches_exclude(keep, package_dir, ("exclude_pkg/keep.py",))
        assert gaffer.path_matches_exclude(keep, package_dir, (str(keep),))
        assert gaffer.path_matches_exclude(nested, package_dir, ("internal/*.py",))
        assert not gaffer.path_matches_exclude(nested, package_dir, ("keep.py",))

    def test_collect_python_files_skips_empty_inits_and_applies_excludes(self):
        package_dir = fixture_package("exclude_pkg")

        files = gaffer.collect_python_files(package_dir, ("skip.py", "internal/drop.py"))
        relative = [path.relative_to(package_dir).as_posix() for path in files]

        assert relative == ["internal/keep_nested.py", "keep.py"]

    def test_is_self_import_classifies_package_relative_local_and_stdlib_imports(self):
        _path, tree = parse_fixture_module("unitnodes", "imports")
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        bundled_modules = {"alpha", "queue"}

        expected = [False, True, True, False, False, True, True, True, True, True]
        for index, node in enumerate(imports):
            assert gaffer.is_self_import(node, "unitnodes", bundled_modules) == expected[index]

        _consumer_path, consumer_tree = parse_fixture_module("stdlib_shadow_pkg", "consumer")
        consumer_import = next(node for node in consumer_tree.body if isinstance(node, ast.Import))
        assert not gaffer.is_self_import(
            consumer_import,
            "stdlib_shadow_pkg",
            {"consumer", "json", "queue"},
        )
        assert gaffer.is_self_import(consumer_import, "json", {"json"})

    def test_import_key_deduplicates_source_equivalent_imports(self):
        _constants_path, constants = parse_fixture_module("basicpkg", "constants")
        _models_path, models = parse_fixture_module("basicpkg", "models")
        constants_import = next(
            node for node in constants.body if isinstance(node, ast.ImportFrom) and node.module == "math"
        )
        models_import = next(node for node in models.body if isinstance(node, ast.ImportFrom) and node.module == "math")

        assert gaffer.import_key(constants_import) == gaffer.import_key(models_import)

    def test_definition_name_covers_supported_definition_shapes(self):
        _path, tree = parse_fixture_module("unitnodes", "defs")
        nodes = tree.body

        assert gaffer.definition_name(nodes[0]) == "decorated"
        assert gaffer.definition_name(nodes[1]) == "Thing"
        assert gaffer.definition_name(nodes[2]) == "VALUE"
        assert gaffer.definition_name(nodes[3]) == "ANNOTATED"
        assert gaffer.definition_name(nodes[4]) is None
        assert gaffer.definition_name(nodes[5]) is None

    def test_extract_source_text_includes_leading_comments_and_decorators(self):
        path, tree = parse_fixture_module("unitnodes", "defs")
        decorated = tree.body[0]

        source_text = gaffer.extract_source_text(path.read_text(), decorated)

        assert source_text == (OUTPUTS / "unitnodes_decorated_source.txt").read_text()

    def test_collect_file_keeps_external_imports_and_drops_self_imports(self):
        path = fixture_package("basicpkg") / "models.py"

        contents = gaffer.collect_file(
            path,
            "basicpkg",
            {"async_tools", "assignments", "constants", "decorators", "models"},
        )

        import_keys = [gaffer.import_key(node) for node in contents.imports]
        definition_names = [gaffer.definition_name(node) for node, _source_text in contents.definitions]
        assert import_keys == (OUTPUTS / "basicpkg_models_external_imports.txt").read_text().splitlines()
        assert definition_names == ["Entity", "Worker", "build_worker"]

    def test_hard_and_soft_dependency_detection(self):
        _path, tree = parse_fixture_module("unitnodes", "deps")
        named = body_by_name(tree)
        aug_assign = next(node for node in tree.body if isinstance(node, ast.AugAssign))

        assert gaffer._hard_deps(named["Child"]) == {"register", "Base", "Mixin"}

        build_hard = gaffer._hard_deps(named["build"])
        build_soft = gaffer._soft_deps(named["build"])
        assert build_hard == {"register", "DEFAULT", "OPTION"}
        assert "Helper" in build_soft
        assert "DEFAULT" not in build_soft
        assert "register" not in build_soft

        assert gaffer._hard_deps(named["VALUE"]) == {"factory", "Base"}
        assert gaffer._hard_deps(named["ANNOTATED"]) == {"factory", "DEFAULT"}
        assert gaffer._hard_deps(aug_assign) == {"step"}
        assert "TOTAL" in gaffer._soft_deps(aug_assign)
        assert "LateThing" in gaffer._soft_deps(named["UsesOnlyInBody"])

    def test_topo_sort_definitions_orders_real_fixture_definitions(self):
        package_dir = fixture_package("dependency_pkg")
        files = gaffer.collect_python_files(package_dir, ())
        bundled_modules = {path.stem for path in files if path.name != "__init__.py"}
        ordered_defs: list[tuple[Path, ast.stmt, str]] = []
        for path in files:
            contents = gaffer.collect_file(path, "dependency_pkg", bundled_modules)
            for node, source_text in contents.definitions:
                ordered_defs.append((path, node, source_text))

        sorted_defs = gaffer.topo_sort_definitions(ordered_defs)
        names = [gaffer.definition_name(node) for _path, node, _source_text in sorted_defs]

        assert names.index("DEFAULT_SIZE") < names.index("make_size")
        assert names.index("mark") < names.index("decorated")
        assert names.index("Base") < names.index("Child")
        assert names.index("LaterThing") < names.index("UsesLater")
