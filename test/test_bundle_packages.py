import asyncio
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import one_script

FIXTURES = Path("test") / "fixtures"
OUTPUTS = ROOT / "test" / "output"


def fixture_package(name: str) -> Path:
    return FIXTURES / name


@contextmanager
def repo_root():
    previous = Path.cwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(previous)


def bundle_text(package_name: str, *, excludes: list[str] | None = None) -> str:
    with repo_root():
        return one_script.bundle(fixture_package(package_name), package_name, exclude_patterns=excludes)


def expected_bundle(name: str) -> str:
    return (OUTPUTS / f"{name}.py").read_text()


def bundled_namespace(package_name: str, *, excludes: list[str] | None = None) -> tuple[str, dict[str, object]]:
    text = bundle_text(package_name, excludes=excludes)
    namespace: dict[str, object] = {}
    exec(compile(text, f"<bundle {package_name}>", "exec"), namespace)
    return text, namespace


class TestBundlePackages:
    def test_basic_real_package_bundles_and_executes(self):
        text, namespace = bundled_namespace("basicpkg")

        assert text == expected_bundle("basicpkg")

        assert namespace["BASE_VALUE"] == 7
        assert namespace["STATUS"] == "ready"
        assert namespace["SCALE"] == 4.0
        assert namespace["COUNTS"] == {"base": 7}
        assert namespace["TOTAL"] == 13

        worker = namespace["build_worker"]("Ada")
        assert namespace["build_worker"].traced
        assert isinstance(worker, namespace["Worker"])
        assert worker.score() == 16.0
        assert json.loads(worker.describe()) == {"name": "Ada", "status": "ready"}
        assert asyncio.run(namespace["fetch_name"](worker)) == "Ada"

    def test_dependency_sorting_handles_hard_and_soft_references(self):
        text, namespace = bundled_namespace("dependency_pkg")

        assert text == expected_bundle("dependency_pkg")

        assert namespace["make_size"]() == 11
        assert namespace["decorated"].marked
        assert isinstance(namespace["Child"](), namespace["Base"])
        assert isinstance(namespace["UsesLater"]().build(), namespace["LaterThing"])

    def test_explicit_internal_symbol_imports_are_removed(self):
        text, namespace = bundled_namespace("importpkg")

        assert text == expected_bundle("importpkg")

        alpha = namespace["Alpha"]()
        assert isinstance(alpha.make(), namespace["Beta"])
        assert alpha.counter()["a"] == 2

    def test_stdlib_shadow_module_imports_are_preserved(self):
        text, namespace = bundled_namespace("stdlib_shadow_pkg")

        assert text == expected_bundle("stdlib_shadow_pkg")

        consumer = namespace["Consumer"]()
        assert consumer.json_module_name() == "json"
        assert isinstance(consumer.local_json(), namespace["Json"])
        assert namespace["StdQueue"].__module__ == "queue"
        assert namespace["Queue"].__name__ == "Queue"

    def test_exclude_patterns_omit_bad_real_package_modules(self):
        with pytest.raises(one_script.BundleError):
            bundle_text("exclude_pkg")

        text, namespace = bundled_namespace("exclude_pkg", excludes=["skip.py", "internal/drop.py"])

        assert text == expected_bundle("exclude_pkg")
        assert namespace["kept"]() == "keep"
        assert namespace["NESTED_KEEP"] == "nested"

    def test_empty_package_has_only_bundle_header(self):
        text, _namespace = bundled_namespace("empty_pkg")

        assert text == expected_bundle("empty_pkg")

    def test_init_file_function_is_a_bundle_error(self):
        with pytest.raises(one_script.BundleError, match="__init__.py must contain only imports"):
            bundle_text("init_function_pkg")

    def test_init_file_assignment_is_a_bundle_error(self):
        with pytest.raises(one_script.BundleError, match="module-level assignment"):
            bundle_text("init_assignment_pkg")

    def test_unsupported_top_level_main_block_has_specific_error(self):
        expected_error = (OUTPUTS / "bad_top_level_error.txt").read_text().strip()
        with pytest.raises(one_script.BundleError, match=re.escape(expected_error)):
            bundle_text("bad_top_level_pkg")

    def test_unsupported_top_level_statement_is_a_bundle_error(self):
        with pytest.raises(one_script.BundleError, match="unsupported top-level For"):
            bundle_text("unsupported_pkg")

    def test_name_collision_is_reported(self):
        with pytest.raises(one_script.BundleError, match="name collision: 'duplicate'"):
            bundle_text("collision_pkg")

    def test_hard_dependency_cycle_is_reported(self):
        with pytest.raises(one_script.BundleError, match="hard dependency cycle"):
            bundle_text("cycle_pkg")
