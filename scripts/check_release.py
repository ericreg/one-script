"""Check the release tag against the package's stable PEP 440 version."""

import argparse
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version


def check_release(tag: str, project_file: Path) -> str:
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    raw = project["version"]
    try:
        version = Version(raw)
    except InvalidVersion as exc:
        raise ValueError(f"Invalid project version: {raw!r}") from exc
    if project["name"] != "one-script":
        raise ValueError("Release project name must be one-script")
    if version.is_prerelease or version.is_devrelease or version.local:
        raise ValueError("Only stable public versions can be released")
    if tag != f"v{raw}" or str(version) != raw:
        raise ValueError(
            f"Release tag must exactly match v{raw} and the version must be canonical"
        )
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--project-file", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()
    try:
        version = check_release(args.tag, args.project_file)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Validated one-script {version}")


if __name__ == "__main__":
    main()
