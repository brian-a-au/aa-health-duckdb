# ahd

ahd rebuilds Adobe's Excel "Analytics Health Dashboard 3.07" as a command line tool. It reads data from the Adobe Analytics 2.0 API, stores it in DuckDB, and writes one self-contained HTML report that you can open in any browser. The report loads nothing from the network.

## Install

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). From the project folder, run:

```
uv sync
```

This installs the `ahd` command into the project environment. Run it with `uv run ahd`.

## Configuration

Create a file named `config.json` in the folder where you run the tool, or pass its path with `--config`. The file holds the four values of your Adobe OAuth Server-to-Server credential:

```json
{
  "org_id": "YOUR_ORG_ID@AdobeOrg",
  "client_id": "YOUR_CLIENT_ID",
  "secret": "YOUR_CLIENT_SECRET",
  "scopes": "openid, AdobeID, additional_info.projectedProductContext"
}
```

You can also set the environment variables `AHD_ORG_ID`, `AHD_CLIENT_ID`, `AHD_SECRET` and `AHD_SCOPES`. A variable wins over the same key in the file. Keep `config.json` out of version control. The tool never writes these values to logs, to the database, or to the report.

## Permissions

The tool only reads from Adobe. Among Analytics API calls, the only one that sends a body is the report request, and it only reads data.

The credential's technical account must be added to an Admin Console product profile that includes:

- The report suite's metrics and dimensions, so that the custom events, eVars and props are visible.
- The Report Suite Tools item "Anomaly Detection", so that the expected values and anomaly flags can be fetched.

If the profile hides the custom components, the report shows no custom data. If "Anomaly Detection" is missing, the run still finishes, but the report has no expected values and no anomaly flags, and it says so. Changes in the Admin Console can take a while to reach Analytics.

Run `ahd check` to test your setup before a full run.

## Usage

Check the sign in and see how many report suites the credential can see:

```
uv run ahd check
```

Check one report suite. This counts the custom events, eVars and props and tests anomaly detection. It exits with 2 if access is limited, and it explains the fix in plain words. It also exits with 2 if a check could not be run, for example because of a network problem:

```
uv run ahd check --rsid my_suite
```

Fetch data and write the report. The path of the HTML file is printed when the run ends:

```
uv run ahd run --rsid my_suite
uv run ahd run --rsid my_suite --segment s300000000_abc --batch-size 20 --workers 4
uv run ahd run --rsid my_suite --date 2026-11-07 --out ./reports/
```

Exit codes for `run`: 0 when everything worked, 1 when it failed, and 3 when it finished but some columns or requests had problems (the report lists them).

Rebuild a report from a stored run without any API calls, and list stored runs:

```
uv run ahd runs
uv run ahd render --run 3
```

Defaults: the database is `./data/ahd.duckdb`, the report folder is `./reports/`, the batch size is 20 columns per request, and 4 requests run at the same time. Add `--verbose` before the command for debug logging on stderr.

## How the numbers differ from Excel

- The week is the 7 days ending yesterday. The baseline is the 84 days from 91 days ago through 8 days ago, divided by 12 to give a weekly average.
- "Today" is today in the report suite's time zone, not your computer's time zone. The workbook asks for today's partial data as well. The tool fetches that row and ignores it.
- The top 100 tables compare yesterday with the 90 days before it (from 91 days ago through 2 days ago). The browser and device tables use yesterday.
- An arrow shows when the change is more than 20 percent. When the baseline is zero, there is no percent change and no arrow.
- A column the credential cannot read is listed as a problem. It is never shown as zero.

## Not yet checked against live data

The numbers have not yet been compared with live data from a credential that has full access. The test credential could not see the custom components or use anomaly detection, so these parts are still unverified:

- Asking for anomaly detection in requests that hold many columns at once. The Excel version asked for 3 columns per request. This tool asks for 20 by default.
- The item IDs that pick the browser groups (Google, Microsoft, Apple, Mozilla, Other) and the device types (Other, Mobile Phone, Tablet, Media Player, Television).
- The component IDs for the Activity Map, Target and AMO groups.

Until these checks are done, compare a run with the same numbers in Analysis Workspace before you rely on it.

## Known limits

- It covers one report suite per run.
- It is English only.
- It does not send email.
- Adobe publishes no rate limits, so the tool uses a small number of parallel requests and retries politely.
