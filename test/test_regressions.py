"""Behavioral regressions for failures found during the release audit."""

import ast
import os
import subprocess
import sys

import pytest

import one_script as ss


def package(tmp_path, files):
    root = tmp_path / "pkg"
    root.mkdir()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return root


def execute(root, *, excludes=None):
    text = ss.bundle(root, "pkg", excludes)
    namespace = {"__name__": "bundled"}
    exec(compile(text, "<test bundle>", "exec", dont_inherit=True), namespace)
    return text, namespace


@pytest.mark.parametrize("name", ["", "a.b", "not-valid", "class", "123"])
def test_invalid_package_name(tmp_path, name):
    with pytest.raises(ss.BundleError, match="identifier"):
        ss.bundle(tmp_path, name)


def test_missing_and_file_inputs_are_errors(tmp_path):
    with pytest.raises(ss.BundleError, match="existing directory"):
        ss.bundle(tmp_path / "missing", "pkg")
    file = tmp_path / "file.py"
    file.write_text("")
    with pytest.raises(ss.BundleError, match="existing directory"):
        ss.bundle(file, "pkg")


def test_empty_directory_and_all_excluded(tmp_path):
    root = package(tmp_path, {"a.py": "raise RuntimeError('excluded')"})
    assert ss.bundle(root, "pkg", ["*.py"]) == '"""Bundled from package \'pkg\'."""\n'


def test_encoding_cookie_and_bom(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": b'# coding: latin-1\nWORD = "caf\xe9"\n',
            "b.py": b'\xef\xbb\xbfOTHER = "tea"\n',
        },
    )
    text, ns = execute(root)
    assert ns["WORD"] == "café"
    assert ns["OTHER"] == "tea"
    assert "coding:" not in text
    compile(text.encode("utf-8"), "bundle.py", "exec")


@pytest.mark.parametrize(
    "source",
    [
        b'VALUE = "\xff"\n',
        b"# coding: missing-codec\nVALUE = 1\n",
        b"def invalid(:\n",
        b"return 1\n",
    ],
)
def test_source_errors_have_locations(tmp_path, source):
    root = package(tmp_path, {"broken.py": source})
    with pytest.raises(ss.BundleError, match="broken.py"):
        ss.bundle(root, "pkg")


def test_semicolons_unicode_offsets_and_comments(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "from itertools import count\ncounter = count()\n# retained\ncafé = next(counter); B = next(counter)  # trailing\n"
        },
    )
    text, ns = execute(root)
    assert (ns["café"], ns["B"]) == (0, 1)
    assert text.count("# retained") == 1
    assert text.count("# trailing") == 1
    assert ss.bundle(root, "pkg") == text


def test_external_imports_are_split_deduplicated_and_not_mistaken_for_local(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "import json, math\nimport json\nVALUE = (json.dumps(1), math.sqrt(4))\n",
            "json.py": "LOCAL = 2\n",
        },
    )
    text, ns = execute(root)
    assert ns["VALUE"] == ("1", 2)
    assert text.count("import json\n") == 1


def test_absolute_nonstdlib_import_is_external():
    assert not ss.is_self_import(ast.parse("from other import Thing").body[0], "pkg")


@pytest.mark.parametrize(
    "statement, message",
    [
        ("import pkg, json", "module-object"),
        ("import json, pkg.a", "module-object"),
        ("from .a import VALUE as Alias", "renamed internal"),
        ("from . import a", "module-object"),
        ("from pkg import *", "wildcard"),
        ("from math import *", "wildcard"),
        ("def make():\n    from .a import VALUE\n    return VALUE", "nested internal"),
        ("from ..a import VALUE", "escapes"),
        ("from .missing import VALUE", "missing or excluded"),
        ("from .a import missing", "missing or excluded"),
    ],
)
def test_unsafe_imports_are_rejected(tmp_path, statement, message):
    root = package(tmp_path, {"a.py": "VALUE = 2\n", "b.py": statement + "\n"})
    with pytest.raises(ss.BundleError, match=message):
        ss.bundle(root, "pkg")


def test_internal_reexports_nested_paths_and_external_reexports(tmp_path):
    root = package(
        tmp_path,
        {
            "__init__.py": "from .sub import Thing\n",
            "sub/__init__.py": "from .model import Thing\n",
            "sub/model.py": "class Thing:\n    pass\n",
            "other/model.py": "from ..sub.model import Thing\nclass Other(Thing):\n    pass\n",
            "helpers.py": "from math import sqrt\n",
            "use.py": "from pkg import Thing\nfrom .helpers import sqrt\nfrom .other.model import Other\nVALUE = sqrt(4)\ndef make():\n    return Other()\n",
        },
    )
    _, ns = execute(root)
    assert isinstance(ns["make"](), ns["Thing"])
    assert ns["VALUE"] == 2


def test_excluded_internal_dependency_fails(tmp_path):
    root = package(
        tmp_path, {"a.py": "from .skip import VALUE\n", "skip.py": "VALUE = 1\n"}
    )
    with pytest.raises(ss.BundleError, match="missing or excluded"):
        ss.bundle(root, "pkg", ["skip.py"])


def test_cyclic_reexports_fail(tmp_path):
    root = package(
        tmp_path, {"a.py": "from .b import VALUE\n", "b.py": "from .a import VALUE\n"}
    )
    with pytest.raises(ss.BundleError, match="cyclic internal re-export"):
        ss.bundle(root, "pkg")


@pytest.mark.parametrize(
    "files",
    [
        {"a.py": "import math as dependency\n", "b.py": "import json as dependency\n"},
        {"a.py": "from math import sqrt\n", "b.py": "sqrt = 4\n"},
        {"a.py": "import math as dependency\nimport json as dependency\n"},
    ],
)
def test_conflicting_bindings_fail(tmp_path, files):
    root = package(tmp_path, files)
    with pytest.raises(ss.BundleError, match="conflicting global binding"):
        ss.bundle(root, "pkg")


def test_identical_external_aliases_are_safe(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "import math as maths\nVALUE = maths.sqrt(4)\n",
            "b.py": "import math as maths\nOTHER = maths.sqrt(9)\n",
        },
    )
    text, ns = execute(root)
    assert ns["VALUE"] == 2 and ns["OTHER"] == 3
    assert text.count("import math as maths") == 1


def test_future_imports_precede_ordinary_imports(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "from __future__ import annotations\nimport json\ndef get(x: Missing):\n    return json.dumps(x)\n"
        },
    )
    text, ns = execute(root)
    assert text.index("from __future__") < text.index("import json")
    assert ns["get"].__annotations__ == {"x": "Missing"}


def test_mixed_future_settings_fail(tmp_path):
    root = package(
        tmp_path,
        {"a.py": "from __future__ import annotations\nA = 1\n", "b.py": "B = 2\n"},
    )
    with pytest.raises(ss.BundleError, match="inconsistent __future__"):
        ss.bundle(root, "pkg")


def test_initializer_cannot_change_future_settings(tmp_path):
    root = package(
        tmp_path,
        {"__init__.py": "from __future__ import annotations\n", "a.py": "A = 1\n"},
    )
    with pytest.raises(ss.BundleError, match="future features would change"):
        ss.bundle(root, "pkg")


def test_function_annotations_on_each_supported_python(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "def f(value: T) -> T:\n    return value\n",
            "b.py": "class T:\n    def get(self):\n        return f\n",
        },
    )
    text, ns = execute(root)
    assert ns["f"].__annotations__["value"] is ns["T"]
    if sys.version_info < (3, 14):
        assert text.index("class T") < text.index("def f")


def test_class_body_and_method_default_dependencies(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "class A:\n    value = B\n    LOCAL = 5\n    def method(self, item=LOCAL, *, other=B):\n        return item, other\n",
            "b.py": "class B:\n    def get(self):\n        return A\n",
        },
    )
    _, ns = execute(root)
    assert ns["A"].value is ns["B"]
    assert ns["A"]().method() == (5, ns["B"])


@pytest.mark.parametrize(
    "source, assertion",
    [
        ("import math\nA = math.pi\npi = A\n", lambda ns: ns["A"] == ns["pi"]),
        ("F = lambda: B\nB = F\n", lambda ns: ns["F"]() is ns["F"]),
        ("A = [B for B in range(2)]\nB = A\n", lambda ns: ns["A"] == [0, 1]),
        ("A = {B: B for B in range(2)}\nB = A\n", lambda ns: ns["A"] == {0: 0, 1: 1}),
        ("A = {B for B in range(2)}\nB = A\n", lambda ns: ns["A"] == {0, 1}),
        ("A = (B for item in range(1))\nB = 4\n", lambda ns: list(ns["A"]) == [4]),
    ],
)
def test_false_dependency_cycles(tmp_path, source, assertion):
    root = package(tmp_path, {"a.py": source})
    _, ns = execute(root)
    assert assertion(ns)


def test_augmented_updates_precede_cross_module_reads(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "from .z import STEP\nTOTAL = 1\nTOTAL += STEP\nBEFORE = TOTAL\nTOTAL *= 2\n",
            "b.py": "from .a import TOTAL\nRESULT = TOTAL\n",
            "z.py": "STEP = 3\n",
        },
    )
    _, ns = execute(root)
    assert ns["BEFORE"] == 4
    assert ns["RESULT"] == ns["TOTAL"] == 8


def test_real_hard_cycle_reports_source(tmp_path):
    root = package(tmp_path, {"a.py": "A = B\n", "b.py": "B = A\n"})
    with pytest.raises(ss.BundleError, match="hard dependency cycle") as error:
        ss.bundle(root, "pkg")
    assert "a.py:1" in str(error.value) and "b.py:1" in str(error.value)


@pytest.mark.parametrize(
    "source, message",
    [
        ("A = B = 1", "assignment target"),
        ("A, B = 1, 2", "assignment target"),
        ("obj.value = 1", "assignment target"),
        ("obj[0] = 1", "assignment target"),
        ("A: int", "annotation-only"),
        ("A += 1", "earlier named assignment"),
        ("A = 1\nimport os", "imports must precede"),
        ("A = (B := 1)", "assignment expressions"),
        ("if True:\n    A = 1", "unsupported top-level"),
    ],
)
def test_unsupported_statements_are_explicit(tmp_path, source, message):
    root = package(tmp_path, {"a.py": source + "\n"})
    with pytest.raises(ss.BundleError, match=message):
        ss.bundle(root, "pkg")


def test_bundling_never_executes_source(tmp_path):
    marker = tmp_path / "must-not-exist"
    root = package(
        tmp_path,
        {
            "a.py": f"from pathlib import Path\nRESULT = Path({str(marker)!r}).write_text('ran')\n"
        },
    )
    ss.bundle(root, "pkg")
    assert not marker.exists()


def test_cli_preserves_existing_output_on_bad_input(tmp_path, capsys):
    root = package(tmp_path, {"a.py": "A = 1\nA = 2\n"})
    output = tmp_path / "bundle.py"
    output.write_text("previous output")
    assert ss.main([str(root), "pkg", "-o", str(output)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err and "Traceback" not in captured.err
    assert output.read_text() == "previous output"


def test_cli_repeated_atomic_output_and_unicode(tmp_path, capsys):
    root = package(tmp_path, {"a.py": 'VALUE = "café"\n'})
    output = tmp_path / "bundle.py"
    args = [str(root), "pkg", "-o", str(output)]
    assert ss.main(args) == 0
    first = output.read_bytes()
    assert ss.main(args) == 0
    assert output.read_bytes() == first
    assert b"caf\xc3\xa9" in first
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_rejects_output_in_source_even_if_excluded(tmp_path):
    root = package(tmp_path, {"a.py": "A = 1\n"})
    original = (root / "a.py").read_bytes()
    assert (
        ss.main([str(root), "pkg", "-o", str(root / "a.py"), "--exclude", "a.py"]) == 1
    )
    assert (root / "a.py").read_bytes() == original


@pytest.mark.parametrize("destination", ["missing/bundle.py", "pkg"])
def test_cli_bad_destination(tmp_path, destination, capsys):
    root = package(tmp_path, {"a.py": "A = 1\n"})
    assert ss.main([str(root), "pkg", "-o", str(tmp_path / destination)]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_atomic_replace_failure_preserves_output_and_cleans_temp(
    tmp_path, monkeypatch, capsys
):
    root = package(tmp_path, {"a.py": "A = 1\n"})
    output = tmp_path / "bundle.py"
    output.write_text("original")

    def fail(*args):
        raise PermissionError("simulated permission failure")

    monkeypatch.setattr(os, "replace", fail)
    assert ss.main([str(root), "pkg", "-o", str(output)]) == 1
    assert output.read_text() == "original"
    assert list(tmp_path.glob(".bundle.py.*.tmp")) == []
    assert "simulated permission failure" in capsys.readouterr().err


def test_discovery_failure_is_reported(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("unreadable directory")

    monkeypatch.setattr(os, "walk", fail)
    with pytest.raises(ss.BundleError, match="unreadable directory"):
        ss.bundle(tmp_path, "pkg")


def test_cli_argument_errors_and_module_help():
    result = subprocess.run(
        [sys.executable, "-m", "one_script"], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "usage: one-script" in result.stderr
    result = subprocess.run(
        [sys.executable, "-m", "one_script", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--exclude" in result.stdout


@pytest.mark.parametrize(
    "files",
    [
        {"a.py": "A = 1\n", "a/__init__.py": ""},
        {"a.py": "A = 1\n", "a/b.py": "B = 1\n"},
    ],
)
def test_ambiguous_module_and_package_paths(tmp_path, files):
    root = package(tmp_path, files)
    with pytest.raises(ss.BundleError, match="ambiguous module path"):
        ss.bundle(root, "pkg")


def test_generic_class_type_parameters_are_local(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "class A[T](list[T]):\n    class Inner:\n        VALUE = T\n",
            "z.py": "T = A\n",
        },
    )
    _, ns = execute(root)
    assert ns["T"] is ns["A"]
    assert ns["A"].Inner.VALUE is ns["A"].__type_params__[0]


def test_generic_function_defaults_use_enclosing_scope(tmp_path):
    root = package(
        tmp_path, {"a.py": "def f[T](value=T):\n    return value\n", "z.py": "T = 4\n"}
    )
    _, ns = execute(root)
    assert ns["f"]() == 4


def test_class_comprehension_iterable_uses_class_scope(tmp_path):
    root = package(
        tmp_path,
        {"a.py": "class A:\n    LOCAL = [1, 2]\n    VALUES = [x for x in LOCAL]\n"},
    )
    _, ns = execute(root)
    assert ns["A"].VALUES == [1, 2]


def test_class_comprehension_body_uses_global_scope(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "class A:\n    X = 1\n    VALUES = [X for _ in range(1)]\n",
            "z.py": "X = 3\n",
        },
    )
    _, ns = execute(root)
    assert ns["A"].VALUES == [3]


def test_nested_class_has_its_own_scope(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "class A:\n    X = 1\n    class Inner:\n        VALUE = X\n",
            "z.py": "X = 3\n",
        },
    )
    _, ns = execute(root)
    assert ns["A"].Inner.VALUE == 3


def test_class_type_alias_rhs_is_deferred(tmp_path):
    root = package(
        tmp_path, {"a.py": "class A:\n    type Alias = B\n", "b.py": "B = A\n"}
    )
    _, ns = execute(root)
    assert ns["A"].Alias.__value__ is ns["A"]


def test_source_order_is_kept_when_class_reads_preceding_local(tmp_path):
    root = package(
        tmp_path,
        {"a.py": "class A:\n    LOCAL = 4\n    VALUE = LOCAL\n", "z.py": "LOCAL = A\n"},
    )
    _, ns = execute(root)
    assert ns["A"].VALUE == 4


def test_default_cli_destination(tmp_path, monkeypatch):
    root = package(tmp_path, {"a.py": "VALUE = 1\n"})
    monkeypatch.chdir(tmp_path)
    assert ss.main([str(root), "pkg"]) == 0
    assert (tmp_path / "bundled.py").is_file()


def test_symlink_output_into_source_is_rejected(tmp_path):
    root = package(tmp_path, {"a.py": "VALUE = 1\n"})
    link = tmp_path / "link.py"
    try:
        link.symlink_to(root / "a.py")
    except OSError:
        pytest.skip("symlinks require privileges on this platform")
    assert ss.main([str(root), "pkg", "-o", str(link)]) == 1
    assert (root / "a.py").read_text() == "VALUE = 1\n"


def test_future_only_package_is_valid(tmp_path):
    root = package(tmp_path, {"__init__.py": "from __future__ import annotations\n"})
    text, _ = execute(root)
    assert "from __future__ import annotations" in text


def test_parenthesized_multiline_decorators_are_preserved(tmp_path):
    root = package(
        tmp_path,
        {
            "a.py": "def decorate(fn):\n    return fn\n\n# keep this\n@(\n    decorate\n)\n@decorate\ndef f():\n    return 3\n"
        },
    )
    text, ns = execute(root)
    assert ns["f"]() == 3
    assert text.count("# keep this") == 1
    function = next(
        node for node in ast.parse(text).body if getattr(node, "name", None) == "f"
    )
    assert [ast.unparse(decorator) for decorator in function.decorator_list] == [
        "decorate",
        "decorate",
    ]
