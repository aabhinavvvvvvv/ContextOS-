"""Smoke tests: prove the toolchain works before any real code exists."""

import sys

import app


def test_python_version_matches_project() -> None:
    assert sys.version_info >= (3, 12)


def test_app_package_is_importable() -> None:
    assert app.__doc__ is not None
