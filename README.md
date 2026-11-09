# aa-health-duckdb

[![Tests](https://github.com/brian-a-au/aa-health-duckdb/actions/workflows/tests.yml/badge.svg)](https://github.com/brian-a-au/aa-health-duckdb/actions/workflows/tests.yml)
[![Lint](https://github.com/brian-a-au/aa-health-duckdb/actions/workflows/lint.yml/badge.svg)](https://github.com/brian-a-au/aa-health-duckdb/actions/workflows/lint.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)](https://github.com/brian-a-au/aa-health-duckdb/tree/main/tests)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![DuckDB 2.0](https://img.shields.io/badge/DuckDB-2.0-fff000.svg)](https://duckdb.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/brian-a-au/aa-health-duckdb/blob/main/LICENSE)

[![Slideshow of the report: summary tiles, trend charts with expected ranges, component tables, alerts, browser breakdowns, and light and dark mode](docs/showcase.gif)](docs/showcase.png)

27-second loop of real report excerpts, made from synthetic data. Click the animation for a still image.

aa-health-duckdb checks the health of an Adobe Analytics report suite. For every custom event, eVar and prop, it shows yesterday's value, the usual value, and whether Adobe's anomaly detection flagged it. It also finds large drops, values that dropped to zero, and components that never collected data.

It is an unofficial alternative to Adobe's Excel workbook ["Analytics Health Dashboard"](https://new.express.adobe.com/webpage/y6DGOeWmpo7Uh), built on the [Adobe Analytics 2.0 API](https://developer.adobe.com/analytics-apis/docs/2.0/). The command is `ahd`.

## What it does

- **One report you can open anywhere.** Each run writes one HTML file that works in any browser, offline. It has three tabs: Components Summary, Individual Component Summary and Alerts Summary. [The report](docs/report.md) walks through it.
- **Alerts that matter.** It lists Adobe's anomaly flags, drops larger than a percent you set with a slider, and components that dropped to zero. Components that never collected data are listed apart, so they don't hide real problems.
- **Every run kept.** It stores each run in a local DuckDB file, so you can compare runs, report suites and organizations with SQL, and send alert emails only when something is new. See the [DuckDB guide](docs/duckdb.md).
- **Any dimension by any metric.** The Individual Component Summary can hold several pairs, e.g., Internal Search Term by Cart Additions, and you add one in a few seconds.
- **Plain errors.** When sign-in or access fails, it names the cause and the fix.
- **Read-only and private.** It only reads from Adobe, never shows or stores your credential, and never sends usage data.

[Compared with the Excel workbook](docs/excel-comparison.md) lists how the numbers and features differ.

## Quick start

You need:

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- an [OAuth Server-to-Server credential](https://developer.adobe.com/developer-console/docs/guides/authentication/ServerToServerAuthentication/) for the Adobe Analytics API, made in the [Adobe Developer Console](https://developer.adobe.com/console/)
- the credential's technical account in an [Adobe Analytics product profile](https://experienceleague.adobe.com/en/docs/analytics/admin/admin-console/permissions/product-profile), in the [Adobe Admin Console](https://adminconsole.adobe.com/)

[Configuration](docs/configuration.md#create-the-credential) walks through both steps.

1. Install the tool from the project folder:

   ```
   uv sync
   ```

2. Save the credential's four values in `config.json` in the project folder, or use a profile. See [Configuration](docs/configuration.md).

   ```json
   {
     "org_id": "YOUR_ORG_ID@AdobeOrg",
     "client_id": "YOUR_CLIENT_ID",
     "secret": "YOUR_CLIENT_SECRET",
     "scopes": "openid, AdobeID, additional_info.projectedProductContext"
   }
   ```

3. Check the sign-in and find your report suite:

   ```
   uv run ahd check
   uv run ahd suites
   uv run ahd check --rsid "My Suite"
   ```

   `--rsid` takes a report suite ID or a name. [How IDs and names are matched](docs/usage.md#how-ids-and-names-are-matched) gives the rules.

4. Fetch the data and write the report. It takes about 1 minute for a suite with 400 components, and it prints the report's path at the end:

   ```
   uv run ahd run --rsid "My Suite"
   ```

   To check several suites, give `--rsid` more than once, or use `--all-suites` for every suite.

5. Open the HTML file in your browser.

If something fails, see [Troubleshooting](docs/troubleshooting.md).

## Documentation

| Document | What it covers |
|---|---|
| [Usage](docs/usage.md) | Every command, with its options, exit codes and defaults |
| [Configuration](docs/configuration.md) | Where the tool finds your credential, profiles, and the Admin Console permissions it needs |
| [The report](docs/report.md) | A walkthrough of the HTML report: the slider, tiles, tabs, alerts and copying |
| [How runs are stored](docs/runs.md) | The DuckDB file, run numbers, deleting runs, and the tables inside |
| [Run it every day](docs/scheduling.md) | A daily check of every suite with an alert email, on Windows, macOS and Linux |
| [DuckDB guide](docs/duckdb.md) | SQL recipes: alert emails, owner routing, digests across suites and organizations, audits and exports |
| [Troubleshooting](docs/troubleshooting.md) | Sign-in problems, limited access, warnings and database errors |
| [Compared with the Excel workbook](docs/excel-comparison.md) | How the numbers differ, what the tool adds, and what it leaves out |
| [Development](docs/development.md) | Tests, lint and the README slideshow. See also [AGENTS.md](AGENTS.md) |

## Related tools

These read-only tools also work with Adobe Analytics implementations. aa_auto_sdr and cja_auto_sdr use the same kind of credential as `ahd`, and one credential can serve all three. See [Share one credential across tools](docs/configuration.md#share-one-credential-across-tools).

- [aa_auto_sdr](https://github.com/brian-a-au/aa_auto_sdr) builds a Solution Design Reference for Adobe Analytics report suites. It shares its profiles with `ahd`.
- [cja_auto_sdr](https://github.com/brian-a-au/cja_auto_sdr) does the same for Customer Journey Analytics data views.
- [sdr-grader](https://github.com/brian-a-au/sdr-grader) checks an Adobe Analytics or Customer Journey Analytics implementation against a set of rules, and writes an HTML report card with a grade.
- [sdr-visualizer](https://github.com/brian-a-au/sdr-visualizer) writes one HTML file with a searchable catalog of the components, and a graph of which components refer to which.

sdr-grader and sdr-visualizer read the JSON files that aa_auto_sdr and cja_auto_sdr write. They do not call Adobe themselves. With `--rsid`, each one runs aa_auto_sdr for you, and aa_auto_sdr signs in with its profile as usual.

## Known limits

- Each run and each report covers one report suite. `ahd run` can check several suites in one command, but it checks them one after another and writes one report for each.
- It is in English only.
- The `ahd` command does not send email. The [DuckDB guide](docs/duckdb.md#email-recipes) shows how to send alerts with a short script.
- Adobe allows 12 requests in any 6 seconds for each user, and the tool starts no more than that. Two `ahd` commands that run at the same time with the same credential each keep their own count, so together they can go over the limit. After a rate limit or a temporary error, the tool waits and tries again. See [Troubleshooting](docs/troubleshooting.md#problems-in-the-status-section).

## License

Apache License 2.0. See [LICENSE](LICENSE).

This is an unofficial tool. Adobe does not make, support or endorse it. Adobe, Adobe Analytics and Analytics Health Dashboard are names that belong to Adobe.
