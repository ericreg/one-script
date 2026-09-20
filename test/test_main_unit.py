import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import one_script

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
        name = one_script.definition_name(node)
        if name is not None:
            result[name] = node
    return result


class TestMainUnit:
    def test_normalize_exclude_patterns_discards_empty_values(self):
        assert one_script.normalize_exclude_patterns(
            [" *.py ", "", "  ", "internal/*.py", "nested/drop.py "]
        ) == (
            "*.py",
            "internal/*.py",
            "nested/drop.py",
        )
        assert one_script.normalize_exclude_patterns(None) == ()

    def test_path_matches_exclude_accepts_relative_and_rooted_globs(self):
        package_dir = fixture_package("exclude_pkg")
        keep = package_dir / "keep.py"
        nested = package_dir / "internal" / "drop.py"

        assert one_script.path_matches_exclude(keep, package_dir, ("keep.py",))
        assert one_script.path_matches_exclude(keep, package_dir, ("exclude_pkg/keep.py",))
        assert one_script.path_matches_exclude(keep, package_dir, (str(keep),))
        assert one_script.path_matches_exclude(nested, package_dir, ("internal/*.py",))
        assert not one_script.path_matches_exclude(nested, package_dir, ("keep.py",))

    def test_collect_python_files_skips_empty_inits_and_applies_excludes(self):
        package_dir = fixture_package("exclude_pkg")

        files = one_script.collect_python_files(package_dir, ("skip.py", "internal/drop.py"))
        relative = [path.relative_to(package_dir).as_posix() for path in files]

        assert relative == ["internal/keep_nested.py", "keep.py"]

    def test_is_self_import_distinguishes_internal_and_external_imports(self):
        _path, tree = parse_fixture_module("unitnodes", "imports")
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]

        expected = [False, True, False, False, False, True, True, False, True, True]
        for index, node in enumerate(imports):
            assert one_script.is_self_import(node, "unitnodes") == expected[index]

        _consumer_path, consumer_tree = parse_fixture_module("stdlib_shadow_pkg", "consumer")
        consumer_import = next(node for node in consumer_tree.body if isinstance(node, ast.Import))
        assert not one_script.is_self_import(
            consumer_import,
            "stdlib_shadow_pkg",
        )
        assert one_script.is_self_import(consumer_import, "json")

    def test_import_key_deduplicates_source_equivalent_imports(self):
        _constants_path, constants = parse_fixture_module("basicpkg", "constants")
        _models_path, models = parse_fixture_module("basicpkg", "models")
        constants_import = next(
            node for node in constants.body if isinstance(node, ast.ImportFrom) and node.module == "math"
        )
        models_import = next(node for node in models.body if isinstance(node, ast.ImportFrom) and node.module == "math")

        assert one_script.import_key(constants_import) == one_script.import_key(models_import)

    def test_definition_name_covers_supported_definition_shapes(self):
        _path, tree = parse_fixture_module("unitnodes", "defs")
        nodes = tree.body

        assert one_script.definition_name(nodes[0]) == "decorated"
        assert one_script.definition_name(nodes[1]) == "Thing"
        assert one_script.definition_name(nodes[2]) == "VALUE"
        assert one_script.definition_name(nodes[3]) == "ANNOTATED"
        assert one_script.definition_name(nodes[4]) is None
        assert one_script.definition_name(nodes[5]) is None

    def test_extract_source_text_includes_leading_comments_and_decorators(self):
        path, tree = parse_fixture_module("unitnodes", "defs")
        decorated = tree.body[0]

        source_text = one_script.extract_source_text(path.read_text(), decorated)

        assert source_text == (OUTPUTS / "unitnodes_decorated_source.txt").read_text()

    def test_collect_file_keeps_external_imports_and_drops_self_imports(self):
        path = fixture_package("basicpkg") / "models.py"

        contents = one_script.collect_file(
            path,
            "basicpkg",
        )

        import_keys = [one_script.import_key(node) for node in contents.imports]
        definition_names = [one_script.definition_name(node) for node, _source_text in contents.definitions]
        assert import_keys == (OUTPUTS / "basicpkg_models_external_imports.txt").read_text().splitlines()
        assert definition_names == ["Entity", "Worker", "build_worker"]

    def test_hard_and_soft_dependency_detection(self):
        _path, tree = parse_fixture_module("unitnodes", "deps")
        named = body_by_name(tree)
        aug_assign = next(node for node in tree.body if isinstance(node, ast.AugAssign))

        assert one_script._hard_deps(named["Child"]) == {"register", "Base", "Mixin"}

        build_hard = one_script._hard_deps(named["build"])
        build_soft = one_script._soft_deps(named["build"])
        assert build_hard == {"register", "DEFAULT", "OPTION"}
        assert "Helper" in build_soft
        assert "DEFAULT" not in build_soft
        assert "register" not in build_soft

        assert one_script._hard_deps(named["VALUE"]) == {"factory", "Base"}
        assert one_script._hard_deps(named["ANNOTATED"]) == ({"factory", "DEFAULT", "int"} if sys.version_info < (3, 14) else {"factory", "DEFAULT"})
        assert one_script._hard_deps(aug_assign) == {"step", "TOTAL"}
        assert "TOTAL" not in one_script._soft_deps(aug_assign)
        assert "LateThing" in one_script._soft_deps(named["UsesOnlyInBody"])

    def test_topo_sort_definitions_orders_real_fixture_definitions(self):
        package_dir = fixture_package("dependency_pkg")
        files = one_script.collect_python_files(package_dir, ())
        ordered_defs: list[tuple[Path, ast.stmt, str]] = []
        for path in files:
            contents = one_script.collect_file(path, "dependency_pkg")
            for node, source_text in contents.definitions:
                ordered_defs.append((path, node, source_text))

        sorted_defs = one_script.topo_sort_definitions(ordered_defs)
        names = [one_script.definition_name(node) for _path, node, _source_text in sorted_defs]

        assert names.index("DEFAULT_SIZE") < names.index("make_size")
        assert names.index("mark") < names.index("decorated")
        assert names.index("Base") < names.index("Child")
        assert names.index("LaterThing") < names.index("UsesLater")
