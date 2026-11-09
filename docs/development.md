# Development

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · **Development**

This page is for anyone who changes the code. [AGENTS.md](../AGENTS.md) explains the layout of the code and the rules for changing it. It is written for people and for AI coding agents.

## Tests and lint

Run the same checks as GitHub Actions:

```
uv run pytest --cov=ahd --cov-branch
uv run ruff check
uv run ruff format --check
uv lock --check
```

`uv lock --check` tests that `uv.lock` matches `pyproject.toml`. Run `uv lock` after you change `pyproject.toml`.

- The tests use saved Adobe responses and never call Adobe.
- Every test runs with the network blocked, so a test fails if it tries to open a connection.
- The tests ignore your own `AA_PROFILE`, `AHD_*` variables and `~/.aa` profiles.
- The example script in `docs/examples` has tests too.
- The tests in `tests/test_report_js.py` open reports in headless Google Chrome or Chromium. They check the page script, e.g., that the alert lists match the `alerts` macro. The tests look for Chrome in the usual places. To choose the program, set `AHD_CHROME` to its path. Without Chrome these tests are skipped, but on GitHub Actions they fail.

GitHub Actions runs the tests for every push to `main` and every pull request:

- On Linux the tests run on Python 3.12, 3.13 and 3.14.
- On Windows and macOS they run on Python 3.14.
- A pull request that changes only Markdown files or the slideshow images skips the tests.
- "Run workflow" on the tests workflow runs them on any branch.

A run fails if branch coverage drops below 95 percent.

## Release a new version

The tool is on PyPI as [aa-health-duckdb](https://pypi.org/project/aa-health-duckdb/). The workflow `.github/workflows/release.yml` uploads it when you publish a GitHub release. It uses PyPI trusted publishing, so no PyPI token is stored anywhere. PyPI trusts this publisher: owner `brian-a-au`, repository `aa-health-duckdb`, workflow `release.yml` and environment `pypi`.

1. In a pull request, set the new `version` in `pyproject.toml`, run `uv lock`, and add a section to `CHANGELOG.md`, e.g., `## 0.2.0 (2026-12-01)`. Follow [Semantic Versioning](https://semver.org/). Until 1.0.0, raise the minor version, e.g., 0.2.0, for a change to the database layout or the options, and the patch version, e.g., 0.1.1, for fixes.
2. Merge the pull request and wait for the checks on `main` to pass.
3. Publish the release with its tag. Use the changelog section as the notes:

   ```
   gh release create v0.2.0 --title v0.2.0 --notes-file notes.md
   ```

4. The release workflow checks that the tag matches the version and that `CHANGELOG.md` has its section. It builds the package, checks that the README links work outside GitHub, installs the wheel and runs `ahd --help`. Then it uploads to PyPI. If the `pypi` environment has a required reviewer, approve the job on GitHub.
5. Check the page on PyPI, and try the release from a clean shell: `uvx --from aa-health-duckdb ahd --help`.

A tag alone or a draft release uploads nothing. PyPI never accepts the same version twice, so a mistake needs a new version.

## Remake the slideshow

The slideshow at the top of the README uses the real report, filled with synthetic data from `scripts/make_showcase.py`. It uses no Adobe API, credential or customer data. You need Google Chrome and FFmpeg. Run:

```
uv run python scripts/make_showcase.py --out docs
```

This writes `docs/showcase.gif` and `docs/showcase.png`.

`tests/test_showcase.py` opens each scene of the slideshow in Chrome, without FFmpeg. It fails when a scene keeps a part of the report that is gone, or when a scene's setup script fails. Run the script again after you change what a scene shows.
