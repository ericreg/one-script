"""The release gate must reject accidental or mismatched versions."""

import pytest

from scripts.check_release import check_release


@pytest.mark.parametrize(
    "version, tag",
    [
        ("0.1.0", "v0.1.0"),
        ("1.2.3", "v1.2.3"),
        ("1.2.3.post1", "v1.2.3.post1"),
    ],
)
def test_matching_stable_release(tmp_path, version, tag):
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "one-script"\nversion = "{version}"\n')
    assert check_release(tag, path) == version


@pytest.mark.parametrize(
    "version, tag",
    [
        ("0.1.0", "0.1.0"),
        ("0.1.0", "v0.2.0"),
        ("0.1.0rc1", "v0.1.0rc1"),
        ("0.1.0.dev1", "v0.1.0.dev1"),
        ("0.1.0+local", "v0.1.0+local"),
        ("not-a-version", "vnot-a-version"),
        ("01.0", "v01.0"),
    ],
)
def test_invalid_release(tmp_path, version, tag):
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "one-script"\nversion = "{version}"\n')
    with pytest.raises(ValueError):
        check_release(tag, path)
