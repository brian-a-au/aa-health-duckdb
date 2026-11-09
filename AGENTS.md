# AGENTS.md

This file tells coding agents how to work on this repository. People can read it too. The README explains what the tool does for a user. This file explains how to change it safely.

## The project

`ahd` checks the health of an Adobe Analytics report suite. It reads data from the Adobe Analytics 2.0 API, stores each run in a local DuckDB file, and writes one self-contained HTML report. It is an unofficial alternative to Adobe's Excel workbook "Analytics Health Dashboard". It may differ from the workbook when that gives a better design, but the counts must stay correct.

The repository is public, and the tool is on PyPI as `aa-health-duckdb`. The command and the Python package are both `ahd`. Until version 1.0.0, a minor release may change the database layout or the command options. Record each change for users in `CHANGELOG.md`. Do not change the version, tag or publish a release unless the maintainer asks. "Release a new version" in `docs/development.md` gives the steps.

## Commands

Set up the environment once:

```
uv sync
```

Run the same checks as GitHub Actions before every commit:

```
uv run pytest --cov=ahd --cov-branch
uv run ruff check
uv run ruff format --check
uv lock --check
```

The tests must pass on Python 3.12, 3.13 and 3.14 on Linux, and on Python 3.14 on Windows and macOS. Branch coverage must stay at 95 percent or more. Run `uv lock` after you change `pyproject.toml`, so that `uv lock --check` passes.

## Where things are

| Path | What it does |
|---|---|
| `src/ahd/cli.py` | The `ahd` command: `check`, `suites`, `segments`, `run`, `individual`, `render`, `runs`, `prune` |
| `src/ahd/config.py` | Reads credentials from a profile, the `AHD_*` variables or a config file |
| `src/ahd/api/auth.py` | OAuth Server-to-Server sign-in, with a plain message for each way it can fail |
| `src/ahd/api/client.py` | The only HTTP client for Adobe: retries, backoff, and one sign-in renewal across threads |
| `src/ahd/api/reports.py` | Builds report request bodies and parses responses |
| `src/ahd/dates.py` | Every date a run uses: today, yesterday, the week, the baseline and the 92 days of trends |
| `src/ahd/catalog.py`, `src/ahd/data/` | Decides which components a run checks and how each is counted |
| `src/ahd/fetch.py` | One run: catalog, anomaly probe, batched trends, and Individual Component Summary pairs |
| `src/ahd/lookup.py` | Lists suites and segments, and turns names into IDs |
| `src/ahd/store.py`, `src/ahd/sql/views.sql` | The DuckDB tables, views and the `alerts` table macro |
| `src/ahd/summary.py` | Turns a stored run into the data the page shows. Every number is computed in SQL |
| `src/ahd/render/` | The Jinja template, CSS and JavaScript of the HTML report |
| `tests/fakes.py` | A fake Adobe client. Tests use it instead of the network |
| `tests/chrome.py`, `tests/test_report_js.py` | Run `report.js` in headless Chrome and read the results back |
| `scripts/make_showcase.py` | Builds the README slideshow from synthetic data |
| `docs/` | The user documentation. The README is a short landing page that links to it. Update the matching page when behavior changes |
| `docs/duckdb.md` | SQL recipes for the database, including alert emails |

## Rules

### Adobe and credentials

- The tool only reads from Adobe. The only request with a body is `POST /reports`, and it only reads data. Never add a request that changes anything in Adobe.
- Never print, log or store a credential value or a token. Messages may name where a credential came from, e.g., "profile prod", but never a value. Exceptions carry only the exception type when the message could hold a URL.
- Never send usage data or telemetry anywhere.
- Live checks use a credential that lives outside this repository. Ask the maintainer before you make live API calls. Keep any database or report you make for a check outside the repository.

### Tests

- Tests never use the network. `tests/conftest.py` blocks it and fails any test that tries. Use `tests/fakes.py` and the saved responses in `tests/fixtures/`.
- `tests/conftest.py` also hides the maintainer's own `AA_PROFILE`, `AHD_*` variables and `~/.aa` profiles from every test.
- Add or update a test for every change in behavior.
- `tests/test_report_js.py` runs `report.js` in headless Chrome. It skips without Chrome, and fails without Chrome when `CI` is set. Set `AHD_CHROME` to choose the Chrome program. Add a test there for every change to what the page does.
- A test that waits for another thread or process must wait for a signal, e.g., a `threading.Event`, and never for a fixed time.

### The database

- The layout is in `store.py` and `sql/views.sql`. When you change a table, a view or the macro, raise `SCHEMA_VERSION`. The tool refuses a file with another version and tells the user to start a new file. There is no upgrade code, on purpose.
- Every row carries its `run_id`. A run's data never changes after the run, except that `ahd individual` may add a pair.
- Only the calling thread writes to the store. Worker threads only call the API.
- The tool targets DuckDB 2.0 or newer. Use its features freely.

### Alert rules live in three places

The rules for unusual values, large drops, zero values and components that never collected data appear in:

1. `src/ahd/render/static/report.js`, which applies them in the page with the slider
2. `src/ahd/summary.py`, which selects the candidate rows for the page
3. `src/ahd/sql/views.sql`, which holds the `alerts` macro for your own SQL

Change all three together. `test_alert_lists_match_the_alerts_macro` and `test_tiles_match_the_alerts_macro` in `tests/test_report_js.py` fail when the page and the macro disagree. `test_alerts_macro` in `tests/test_summary.py` checks the macro on its own.

### The HTML report

- The report is one file. It loads nothing from the internet, and its Content Security Policy blocks any attempt. Do not add external scripts, fonts or images.
- Write the JavaScript in the same plain ES5 style as the rest of `report.js`.
- Colors are tokens on `:root`, with light and dark versions. Check both themes and a narrow phone width after a layout change.
- Build page text with DOM methods and `textContent`, never with HTML strings from data. Component names and values come from Adobe and must stay inert.

### Writing

All prose in this repository uses a plain style. This covers the README, docs, messages, page text, commit messages and PR descriptions:

- Use simple, everyday words and complete sentences.
- Do not use dashes to join clauses. Use a colon only to introduce a list.
- Do not use jargon. If a technical term is needed, explain it once in plain words.
- Do not use analogies, filler or empty emphasis words.
- Give each idea its own sentence or bullet, and give one example with "e.g." when an example helps.

### Git

- Make each change on its own branch, not on `main`. `main` accepts changes only through a pull request whose checks pass, and nobody can push to it directly. Merge with a merge commit, e.g., `gh pr merge --merge`.
- Do not merge or push unless the maintainer asks.
- Never add AI attribution of any kind to commits, tags or pull requests, e.g., no `Co-Authored-By` trailer for an AI tool and no "Generated with" line.
- Never commit credentials, database files, reports or the Adobe workbooks. `.gitignore` covers them.
