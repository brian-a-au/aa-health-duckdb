# Development

[README](../README.md) · [Usage](usage.md) · [Configuration](configuration.md) · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Email alerts](email-alerts.md) · [Slack alerts](slack-alerts.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · **Development**

This page is for anyone who changes the code. [AGENTS.md](../AGENTS.md) explains the layout of the code and the rules for changing it. It is written for people and for AI coding agents.

## Work from a clone

Clone the repository, and install the tool with its test tools:

```
git clone https://github.com/brian-a-au/aa-health-duckdb.git
cd aa-health-duckdb
uv sync
```

Then put `uv run` in front of each command, e.g., `uv run ahd check`.

## Tests and lint

Run the same checks as GitHub Actions:

```
uv run pytest --cov=ahd --cov-branch
uv run ruff check
uv run ruff format --check
uv run mypy
uv lock --check
```

`uv run mypy` checks the types in `src/ahd`. `uv lock --check` tests that `uv.lock` matches `pyproject.toml`. Run `uv lock` after you change `pyproject.toml`. GitHub Actions also checks the workflow files with actionlint. Run `actionlint` too if you change a file in `.github/workflows/`.

The tests run in one process per CPU core, with pytest-xdist. Add `-n 0` to run them in one process, e.g., `uv run pytest -n 0 tests/test_catalog.py`. A test that you debug with `breakpoint()` needs one process.

- The tests use saved Adobe responses and never call Adobe.
- Every test runs with the network blocked, so a test fails if it tries to open a connection.
- The tests ignore your own `AA_PROFILE`, `AHD_*` variables and `~/.aa` profiles. Only `AHD_CHROME` stays, which chooses the Chrome for the page tests.
- The example script in `docs/examples` has tests too.
- The tests in `tests/test_report_js.py` open reports in headless Google Chrome or Chromium. They check the page script, e.g., that the alert lists match the `alerts` macro. The tests look for Chrome in the usual places. To choose the program, set `AHD_CHROME` to its path. Without Chrome these tests are skipped, but on GitHub Actions they fail.

GitHub Actions runs the tests for every push to `main` and every pull request:

- On Linux the tests run on Python 3.12, 3.13 and 3.14.
- On Windows and macOS they run on Python 3.14.
- "Run workflow" on the tests workflow runs them on any branch.

A run fails if branch coverage drops below 95 percent.

`main` accepts changes only through a pull request. Nobody can push to it directly. Every pull request runs all the test jobs, even one that changes only docs. These checks must pass before a merge:

- the tests on Linux with Python 3.12, 3.13 and 3.14, and on Windows and macOS with Python 3.14
- lint with the type check, actionlint and the lockfile check
- the 3 CodeQL jobs

Merge with a merge commit, e.g., `gh pr merge --merge`.

CodeQL scans the code for every push to `main`, every pull request and once a week, with GitHub's security and quality queries. It checks the Python code, the report's page script `report.js` and the workflow files. Its results are under Security, Code scanning on GitHub.

Dependabot keeps the dependencies up to date. `.github/dependabot.yml` sets it up:

- Once a week it opens pull requests that update the Python packages in `uv.lock` and the actions in `.github/workflows/`.
- The minor and patch updates of each kind come in one pull request. Each major update comes in its own pull request.
- It also opens a pull request soon after GitHub learns of a security problem in a dependency. Its alerts are under Security, Dependabot on GitHub.

These pull requests run the same checks as any other. `.github/workflows/dependabot-auto-merge.yml` turns on auto-merge for a pull request that holds only minor or patch updates. GitHub then merges it with a merge commit once every required check passes. Review each major update yourself and merge it with a merge commit when its checks pass.

## Check a change against Adobe

The tests use a fake Adobe client, so they cannot show how Adobe answers. Adobe can keep the answer to an earlier request and give it to a later one. So check a change of how the tool reads Adobe with a real run. `scripts/live_check.py` does this:

```
uv run python scripts/live_check.py --profile NAME --rsid SUITE
```

- It runs `ahd run` on the suite into a temporary folder, and deletes the folder at the end.
- It prints the GET requests of the run in their order, and says whether each list of dimensions or metrics held `allowedForReporting`.
- It checks the stored run for NULL in the name, kind and `allowed_for_reporting` of each component, for a failed run, and for a run without daily values. It names each problem it finds, and says how many components it checked, so you can give the count in a pull request.
- After the counts, it prints one line for each kind of issue of the run, counted by scope and code, e.g., "Issues: 3 individual no_rows." An issue is something the run could not read, and the issues say why a run is "partial". A partial run is not a problem. A run without issues prints no such line.
- It exits with 0 when it finds no problem, and with 1 when it finds one.
- It sees only the path and the parameters of each request, never a credential, a token or a header.
- It refuses to run when the `CI` variable is set, so it never runs on GitHub Actions.

Run it before the change to see the problem, and again after the change to see it gone. Run it before you send any request of your own to that suite, because your request can change what the run gets. `--config` takes a config file in place of a profile, and `--date` uses another date as today, as `ahd run --date` does. The run then checks the days before that date.

## Release a new version

The tool is on PyPI as [aa-health-duckdb](https://pypi.org/project/aa-health-duckdb/). Pushing a tag `vX.Y.Z` starts `.github/workflows/release.yml`. It works like the release workflow of sdr-visualizer, and has three jobs:

1. **build** checks and builds the package:
   - runs lint and the tests
   - checks that the tag matches the version in `pyproject.toml`, and that `CHANGELOG.md` has a section for it
   - builds the wheel and the source package
   - checks that every link in the README is a full web address, so the links work on PyPI
   - installs the wheel in a new environment and runs `ahd --help`
   - runs `scripts/check_wheel.py` with the installed wheel. It makes a database with one run of made-up data, and runs `ahd runs`, `ahd alerts` and `ahd render` on it. So the release stops when the wheel lacks `views.sql`, the template or the page files. It needs no credential and no network.
   - writes SHA-256 sums
2. **publish** uploads the same files to PyPI with trusted publishing, so no PyPI token is stored anywhere. It runs in the `pypi` environment, which accepts only tags `v*.*.*`. It does not wait for an approval, so a pushed tag goes to PyPI as soon as the build job passes. PyPI trusts this publisher: owner `brian-a-au`, repository `aa-health-duckdb`, workflow `release.yml` and environment `pypi`.
3. **github-release** creates the GitHub release with the files, the sums and the changelog section as its notes.

To release:

1. In a pull request, set the new `version` in `pyproject.toml`, run `uv lock`, and add a section to `CHANGELOG.md`, e.g., `## 0.2.0 (2026-12-01)`. Also put the new tag in the download links of the alert scripts in `docs/email-alerts.md` and `docs/slack-alerts.md`, e.g., `v0.2.0`. `test_script_downloads_link_to_the_release_of_this_version` fails until you do. The links work once the tag is pushed. Follow [Semantic Versioning](https://semver.org/). Until 1.0.0, raise the minor version, e.g., 0.2.0, for a change that can break a user's file or script: a new database layout, or a changed or removed command or option. Raise the patch version, e.g., 0.1.1, for fixes and for additions that break nothing, e.g., a new command.
2. If the release changes how the tool reads Adobe, e.g., a changed expansion, run the [live check](#check-a-change-against-adobe) on the branch before you merge. Use one suite that the change touches, and send no other request to that suite before the check. Write the result in the pull request as counts, without the suite ID or component names, because the repository is public. Do not release while the script reports a NULL that the change should fill.
3. Merge the pull request with `gh pr merge --merge`, and wait for the checks on `main` to pass.
4. Optionally, open the release workflow on GitHub and choose "Run workflow". It runs the build job only, as a dry run, and uploads nothing. Nothing waits for an approval after the tag, so this is the last check before the upload.
5. Tag the merge commit on `main` and push the tag:

   ```
   git switch main && git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```

6. Watch the run in the Actions tab. The upload starts as soon as the build job passes.
7. Check the page on PyPI and the GitHub release. Try the release from a clean shell: `uvx --from aa-health-duckdb ahd --help`.

Release tags cannot be deleted or moved. PyPI never accepts the same version twice, so a mistake needs a new version.

## Remake the slideshow

The slideshow at the top of the README uses the real report, filled with synthetic data from `scripts/make_showcase.py`. It uses no Adobe API, credential or customer data. You need Google Chrome and FFmpeg. Run:

```
uv run python scripts/make_showcase.py --out docs
```

This writes `docs/showcase.gif` and `docs/showcase.png`.

`tests/test_showcase.py` opens each scene of the slideshow in Chrome, without FFmpeg. It fails when a scene keeps a part of the report that is gone, or when a scene's setup script fails. Run the script again after you change what a scene shows.
