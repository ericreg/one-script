import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
FIXTURES = Path("test") / "fixtures"
OUTPUTS = ROOT / "test" / "output"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_writes_bundle_file(tmp_path: Path):
    output = tmp_path / "basic_bundle.py"

    result = run_cli(str(FIXTURES / "basicpkg"), "basicpkg", "-o", str(output))

    assert result.returncode == 0, result.stderr
    assert f"wrote {output}" in result.stdout
    text = output.read_text()
    assert text == (OUTPUTS / "basicpkg.py").read_text()
    namespace: dict[str, object] = {}
    exec(compile(text, str(output), "exec"), namespace)
    assert namespace["build_worker"]("Cli").name == "Cli"


def test_cli_accepts_repeated_exclude_flags(tmp_path: Path):
    output = tmp_path / "excluded_bundle.py"

    result = run_cli(
        str(FIXTURES / "exclude_pkg"),
        "exclude_pkg",
        "-o",
        str(output),
        "--exclude",
        "skip.py",
        "--exclude",
        "internal/drop.py",
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text() == (OUTPUTS / "exclude_pkg.py").read_text()
    namespace: dict[str, object] = {}
    exec(compile(output.read_text(), str(output), "exec"), namespace)
    assert namespace["kept"]() == "keep"


def test_cli_prints_clean_bundle_errors_to_stderr(tmp_path: Path):
    output = tmp_path / "bad_bundle.py"

    result = run_cli(str(FIXTURES / "bad_top_level_pkg"), "bad_top_level_pkg", "-o", str(output))

    assert result.returncode == 1
    assert not output.exists()
    assert result.stdout == ""
    assert "error:" in result.stderr
    assert (OUTPUTS / "bad_top_level_error.txt").read_text().strip() in result.stderr
