"""Generated bundles use Ruff formatting without changing behavior or input files."""

import subprocess
import sys

import pytest

import one_script as ss


@pytest.fixture
def unformatted_package(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "greeter.py").write_text(
        "# Keep this comment.\n"
        "PREFIX='café'\n"
        "def greet( name = 'Ada' ):\n"
        " return PREFIX + ', ' + name\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_default_formatting_ignores_local_config_and_path(
    unformatted_package, tmp_path, monkeypatch, interface
):
    root = unformatted_package
    original = (root / "greeter.py").read_bytes()
    (tmp_path / "ruff.toml").write_text(
        'exclude = ["*.py"]\nforce-exclude = true\n'
        '[format]\nquote-style = "single"\nindent-style = "tab"\n'
    )
    (tmp_path / "ruff.py").write_text('raise RuntimeError("must not execute local code")\n')
    monkeypatch.chdir(tmp_path)
    # The formatter must come from the active Python environment, not PATH.
    monkeypatch.setenv("PATH", "")
    if interface == "api":
        text = ss.bundle(root, "pkg")
    else:
        output = tmp_path / "bundle.py"
        assert ss.main([str(root), "pkg", "-o", str(output)]) == 0
        text = output.read_text(encoding="utf-8")
    assert '# Keep this comment.\nPREFIX = "café"\n' in text
    assert '\n\ndef greet(name="Ada"):\n    return PREFIX + ", " + name\n' in text
    assert text.endswith("\n")
    assert (root / "greeter.py").read_bytes() == original
    namespace = {}
    exec(compile(text, "bundle.py", "exec"), namespace)
    assert namespace["greet"]() == "café, Ada"


@pytest.mark.parametrize(
    "failure, message",
    [
        (OSError("cannot start formatter"), "could not run Ruff formatter"),
        (
            subprocess.CalledProcessError(1, "ruff", stderr="No module named ruff\n"),
            "Ruff formatting failed: No module named ruff",
        ),
        (
            subprocess.CalledProcessError(2, "ruff", stderr=""),
            "Ruff formatting failed: exit status 2",
        ),
    ],
)
@pytest.mark.parametrize("existing_output", [False, True])
def test_formatting_failure_leaves_output_untouched(
    unformatted_package, tmp_path, monkeypatch, capsys, failure, message, existing_output
):
    output = tmp_path / "bundle.py"
    if existing_output:
        output.write_text("previous output\n")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ss.subprocess, "run", fail)
    with pytest.raises(ss.BundleError, match=message):
        ss.bundle(unformatted_package, "pkg")
    assert ss.main([str(unformatted_package), "pkg", "-o", str(output)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Traceback" not in captured.err
    if existing_output:
        assert output.read_text() == "previous output\n"
    else:
        assert not output.exists()
    assert not list(tmp_path.glob(".bundle.py.*.tmp"))


@pytest.mark.skipif(sys.version_info < (3, 14), reason="template strings need Python 3.14")
def test_python314_template_strings_are_formatted(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "template.py").write_text('VALUE=t"Hello, {1 + 1}!"\n')
    text = ss.bundle(root, "pkg")
    assert 'VALUE = t"Hello, {1 + 1}!"' in text
    namespace = {}
    exec(compile(text, "bundle.py", "exec"), namespace)
    assert namespace["VALUE"].interpolations[0].value == 2
