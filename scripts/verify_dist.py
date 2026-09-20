"""Verify distributable contents, clean installations, and tests from the sdist.

Run after `python -m build`. Requires uv and the development dependencies.
All installations, extraction, and rebuilt artifacts stay in a temporary directory.
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path


def run(*args: str | Path, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def install_and_smoke(
    artifact: Path, work: Path, version: str, dependencies: list[str]
) -> None:
    env_dir = work / "venv"
    run("uv", "venv", "--python", sys.executable, env_dir, cwd=work)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command = env_dir / (
        "Scripts/one-script.exe" if os.name == "nt" else "bin/one-script"
    )
    run("uv", "pip", "install", "--python", python, artifact, cwd=work)
    # Do not allow the checkout or an editable installation to satisfy an import.
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    run(
        python,
        "-I",
        "-c",
        (
            "import importlib.metadata as m; import one_script; "
            f"assert m.version('one-script') == {version!r}; "
            "from pathlib import Path; import sys; "
            "assert Path(one_script.__file__).is_relative_to(Path(sys.prefix)); "
            f"assert sorted(m.requires('one-script') or []) == {sorted(dependencies)!r}"
        ),
        cwd=work,
        env=environment,
    )
    pkg = work / "example_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .models import answer\n", encoding="utf-8")
    (pkg / "constants.py").write_text("VALUE = 42\n", encoding="utf-8")
    (pkg / "models.py").write_text(
        "from .constants import VALUE\ndef answer():\n    return VALUE\n",
        encoding="utf-8",
    )
    for prefix, name in [
        ([command], "console.py"),
        ([python, "-I", "-m", "one_script"], "module.py"),
    ]:
        run(*prefix, "--help", cwd=work, env=environment)
        run(*prefix, pkg, "example_pkg", "-o", work / name, cwd=work, env=environment)
        run(
            python,
            "-m",
            "ruff",
            "format",
            "--isolated",
            "--no-cache",
            "--check",
            work / name,
            cwd=work,
            env=environment,
        )
        run(
            python,
            "-I",
            "-c",
            f"import runpy; ns = runpy.run_path({str(work / name)!r}); assert ns['answer']() == 42",
            cwd=work,
            env=environment,
        )
    run(
        python,
        "-I",
        "-c",
        (
            "from pathlib import Path; from one_script import bundle; "
            f"text = bundle(Path({str(pkg)!r}), 'example_pkg'); "
            "ns = {}; exec(compile(text, '<api>', 'exec'), ns); assert ns['answer']() == 42"
        ),
        cwd=work,
        env=environment,
    )


def verify(dist_dir: Path) -> None:
    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1 or len(list(dist_dir.iterdir())) != 2:
        raise ValueError(
            "dist must contain exactly one wheel and one source distribution; clean it before building"
        )
    wheel, sdist = wheels[0].resolve(), sdists[0].resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if "one_script.py" not in names or any(
            name.startswith(("test/", "scripts/")) for name in names
        ):
            raise ValueError(
                "Wheel must contain the application without development files"
            )
        if any(
            not (name == "one_script.py" or name.startswith("one_script-"))
            for name in names
        ):
            raise ValueError("Unexpected file in wheel")
    with tempfile.TemporaryDirectory(prefix="one-script-dist-") as directory:
        root = Path(directory)
        with tarfile.open(sdist) as archive:
            archive.extractall(root / "source", filter="data")
        sources = list((root / "source").iterdir())
        if len(sources) != 1:
            raise ValueError("Expected a single source distribution root")
        source = sources[0]
        for relative in [
            "README.md",
            "one_script.py",
            "uv.lock",
            "test/test_regressions.py",
            "test/fixtures/basicpkg/models.py",
            "test/output/basicpkg.py",
            "scripts/verify_dist.py",
        ]:
            if not (source / relative).is_file():
                raise ValueError(f"Source distribution is missing {relative}")
        project = tomllib.loads(
            (source / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        version = project["version"]
        dependencies = project.get("dependencies", [])
        for artifact, label in [(wheel, "wheel"), (sdist, "sdist")]:
            work = root / label
            work.mkdir()
            install_and_smoke(artifact, work, version, dependencies)
        rebuilt = root / "rebuilt"
        run(
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            rebuilt,
            source,
            cwd=root,
        )
        (rebuilt_wheel,) = rebuilt.glob("*.whl")
        work = root / "rebuilt-smoke"
        work.mkdir()
        install_and_smoke(rebuilt_wheel, work, version, dependencies)
        # Run the full test suite in the extracted sdist, using an independent environment.
        test_env = root / "test-venv"
        run("uv", "venv", "--python", sys.executable, test_env, cwd=root)
        test_python = test_env / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        # Install locked runtime and development dependencies before the wheel.
        requirements = root / "dev-requirements.txt"
        run(
            "uv",
            "export",
            "--locked",
            "--project",
            source,
            "--group",
            "dev",
            "--no-emit-project",
            "--output-file",
            requirements,
            cwd=root,
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            test_python,
            "--require-hashes",
            "-r",
            requirements,
            cwd=root,
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            test_python,
            "--no-deps",
            rebuilt_wheel,
            cwd=root,
        )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        run(test_python, "-m", "pytest", "-q", cwd=source, env=environment)
    print(json.dumps({"wheel": wheel.name, "sdist": sdist.name, "status": "verified"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, default=Path("dist"))
    verify(parser.parse_args().dist)
