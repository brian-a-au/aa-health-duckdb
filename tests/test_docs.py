"""The docs agree with the code: links, command options and quoted messages."""

import re
from pathlib import Path

import pytest

from ahd.cli import _parser

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted(
    [ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md")]
    + list((ROOT / "docs").glob("*.md"))
)
GITHUB = "https://github.com/brian-a-au/aa-health-duckdb/blob/main/"
COMMANDS = (
    "check",
    "suites",
    "segments",
    "run",
    "individual",
    "render",
    "runs",
    "prune",
    "export",
    "import",
)


def _prose(page: Path) -> str:
    """The page without its code blocks."""
    return re.sub(r"```.*?```", "", page.read_text(encoding="utf-8"), flags=re.DOTALL)


def _heading_links(page: Path) -> set[str]:
    """The link of each heading, as GitHub makes it: lower case, spaces as dashes."""
    found: set[str] = set()
    for heading in re.findall(r"^#{1,6}\s+(.*)$", _prose(page), re.MULTILINE):
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
        link = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        number = 0
        while (link if number == 0 else f"{link}-{number}") in found:
            number += 1
        found.add(link if number == 0 else f"{link}-{number}")
    return found


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_link_to_a_file_or_a_heading_of_this_repository_works(page):
    """A link to a page that was renamed, or to a heading that was reworded, fails here."""
    for target in re.findall(r"\]\(([^)\s]+)\)", _prose(page)):
        if target.startswith(GITHUB):
            base, target = ROOT, target.removeprefix(GITHUB)
        elif re.match(r"[a-z]+:", target):
            continue  # another website
        else:
            base = page.parent
        path, _, heading = target.partition("#")
        file = (base / path).resolve() if path else page
        assert file.exists(), f"{page.name} links to {target}, which does not exist"
        if heading and file.suffix == ".md":
            assert heading in _heading_links(file), (
                f"{page.name} links to {target}, but {file.name} has no such heading"
            )


def _options(command: str) -> set[str]:
    parser = _parser()
    [commands] = [a for a in parser._actions if a.dest == "command"]
    actions = parser._actions + commands.choices[command]._actions
    return {o for a in actions for o in a.option_strings if o.startswith("--")} - {"--help"}


@pytest.mark.parametrize("command", COMMANDS)
def test_usage_names_every_option_of_every_command(command):
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    missing = sorted(o for o in _options(command) if f"`{o}" not in usage)
    assert not missing, f"docs/usage.md does not name {missing} of ahd {command}"


def test_usage_names_no_option_that_no_command_has():
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    known = set().union(*(_options(c) for c in COMMANDS))
    # --name-match is an option of aa_auto_sdr, which the page compares with.
    named = set(re.findall(r"`(--[a-z-]+)", usage)) - {"--name-match", "--help"}
    assert named <= known, f"docs/usage.md names {sorted(named - known)}"


def test_usage_has_a_section_for_every_command():
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    headings = " ".join(re.findall(r"^## (.*)$", usage, re.MULTILINE))
    [commands] = [a for a in _parser()._actions if a.dest == "command"]
    assert set(commands.choices) == set(COMMANDS)
    for command in COMMANDS:
        assert re.search(rf"\b{command}\b", headings), f"no section for ahd {command}"


def _source_text() -> str:
    """The source with string parts that follow each other joined, and spaces made single."""
    parts = [
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "src").rglob("*"))
        if p.suffix in (".py", ".js", ".j2")
    ]
    joined = re.sub(r'"\s*\n\s*\+?\s*f?"', "", "\n".join(parts))
    return re.sub(r"\s+", " ", joined)


# Messages that the page quotes but that are not one piece of text in the source.
NOT_IN_SOURCE = {
    # cli.py builds it from "Anomaly detection: " and "allowed" or "not allowed".
    "Anomaly detection: not allowed",
    # DuckDB's own message.
    "Could not set lock on file",
}


def test_the_messages_that_troubleshooting_quotes_are_in_the_code():
    """A message that was reworded in the code must be reworded on the page too."""
    page = _prose(ROOT / "docs" / "troubleshooting.md")
    quoted = re.findall(r'\*\*"([^"]+)"\*\*', page) + re.findall(
        r'^\| "([^"]+)" \|', page, re.MULTILINE
    )
    assert len(quoted) > 20
    source = _source_text()
    for message in quoted:
        if message in NOT_IN_SOURCE:
            continue
        # A name in the message is an example, e.g., the profile 'x'. Check the text around it.
        pieces = [p.strip(" .") for p in re.split(r"'x'|\.\.\.", message)]
        for piece in pieces:
            assert piece in source, f'troubleshooting.md quotes "{message}", which the code lacks'
