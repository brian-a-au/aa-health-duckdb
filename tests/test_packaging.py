"""The package metadata, the README and the release workflow agree with each other."""

import re
import tomllib
from pathlib import Path

from ahd.fetch import DIST_NAME, _tool_version

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_the_pypi_name_is_the_one_the_tool_reads_its_version_from():
    assert PROJECT["name"] == DIST_NAME == "aa-health-duckdb"
    assert _tool_version() == PROJECT["version"]


def test_the_changelog_has_a_section_for_the_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(
        rf"^## {re.escape(PROJECT['version'])} \(\d{{4}}-\d{{2}}-\d{{2}}\)$",
        changelog,
        re.MULTILINE,
    )


def test_the_readme_has_no_relative_links():
    # PyPI shows the README outside the repository, where a relative link points at pypi.org.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)", readme)
    assert targets
    assert [t for t in targets if not re.match(r"(https?:|mailto:|#)", t)] == []


def test_the_readme_installs_from_pypi():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"uv tool install {DIST_NAME}" in readme and f"pipx install {DIST_NAME}" in readme


def test_the_release_workflow_matches_the_pypi_publisher():
    # PyPI trusts this file name and environment. Changing either one breaks the upload.
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags:\n      - "v*.*.*"' in workflow and "workflow_dispatch:" in workflow
    assert "environment: pypi" in workflow and "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    # The upload and the GitHub release run only for a pushed tag, never for a dry run.
    assert workflow.count("if: github.event_name == 'push'") == 2


def test_codeql_scans_python_javascript_and_the_workflows():
    workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
    for language in ("python", "javascript-typescript", "actions"):
        assert f"- language: {language}" in workflow
    assert "queries: security-and-quality" in workflow and "security-events: write" in workflow
