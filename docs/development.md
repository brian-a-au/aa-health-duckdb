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

GitHub Actions runs the tests in these cases:

- On Linux the tests run on Python 3.12, 3.13 and 3.14, for every push to `main` and every pull request.
- On Windows and macOS they run on Python 3.14, for every push to `main` only. These runners cost 2 and 10 times as many Actions minutes, and the repository is private, so its minutes are limited. To run them on a branch, open the tests workflow on GitHub and choose "Run workflow".
- A pull request that changes only Markdown files or the slideshow images skips the tests.

A run fails if branch coverage drops below 95 percent.

## Remake the slideshow

The slideshow at the top of the README uses the real report, filled with synthetic data from `scripts/make_showcase.py`. It uses no Adobe API, credential or customer data. You need Google Chrome and FFmpeg. Run:

```
uv run python scripts/make_showcase.py --out docs
```

This writes `docs/showcase.gif` and `docs/showcase.png`.

`tests/test_showcase.py` opens each scene of the slideshow in Chrome, without FFmpeg. It fails when a scene keeps a part of the report that is gone, or when a scene's setup script fails. Run the script again after you change what a scene shows.
