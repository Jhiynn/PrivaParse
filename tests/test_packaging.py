"""The package's own metadata, checked rather than assumed.

Version drift between the module and the distribution is the kind of
error that only shows up after a release, when it is expensive.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

import privaparse

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_module_version_matches_the_installed_distribution() -> None:
    assert privaparse.__version__ == metadata.version("privaparse")


def test_project_declares_the_urls_pypi_renders() -> None:
    urls = _pyproject()["project"]["urls"]
    assert set(urls) >= {"Homepage", "Documentation", "Issues", "Changelog"}
    assert all(value.startswith("https://") for value in urls.values())


def test_project_declares_classifiers_and_keywords() -> None:
    project = _pyproject()["project"]
    assert project["keywords"]
    joined = " ".join(project["classifiers"])
    assert "License :: " not in joined, "licence is expressed as an SPDX expression, not a classifier"
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in joined


def test_every_extra_is_reachable_from_all() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert set(extras) >= {"model", "gateway", "dev", "all"}


def test_build_backend_understands_the_spdx_licence_field() -> None:
    # PEP 639 landed in setuptools 77.0.3. Below that floor the licence
    # metadata is dropped rather than rejected, which is the worse failure.
    requires = " ".join(_pyproject()["build-system"]["requires"])
    assert "setuptools>=77" in requires.replace(" ", "")


def test_requirements_txt_is_gone() -> None:
    # A hand-mirrored copy of [project.dependencies] that had already drifted:
    # it omitted pyyaml and predated the gateway extra.
    assert not (PYPROJECT.parent / "requirements.txt").exists()
