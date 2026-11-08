# aa-health-duckdb

[![Slideshow of the report: summary tiles, trend charts with expected ranges, component tables, alerts, browser breakdowns, and light and dark mode](docs/showcase.gif)](docs/showcase.png)

27-second loop of real report excerpts, made from synthetic data. Click the animation for a still image.

aa-health-duckdb checks the health of an Adobe Analytics report suite. For every custom event, eVar and prop, it shows yesterday's value, the usual value, and whether Adobe's anomaly detection flagged it. It also lists the components that collected no data.

It is an unofficial alternative to Adobe's Excel workbook "Analytics Health Dashboard 3.07", built on the Adobe Analytics 2.0 API. It does not replace the workbook. The command is `ahd`.

## How it works

`ahd run` does three things on your computer:

1. **Fetch.** It signs in with your OAuth Server-to-Server credential and asks the Adobe Analytics 2.0 API for 92 days of daily data, with Adobe's expected values and anomaly flags. It only reads from Adobe.
2. **Store.** It saves the data in a DuckDB file on your computer, by default `./data/ahd.duckdb`.
3. **Report.** It works out the summaries with SQL on that file and writes one HTML file to `./reports/`.

### DuckDB runs inside ahd

DuckDB is a database that runs inside the `ahd` program itself, so there is no database server to install, start or connect to. `uv sync` installs it with the other Python packages.

- **One local file.** The whole database is one file on your disk. Each run adds to it, and older runs stay in it. You can rebuild any stored run's report with `ahd render`, without calling Adobe again.
- **Nothing is uploaded.** The tool never sends the file anywhere.
- **No credentials inside.** The file holds report data, e.g., component names and daily values. It never holds your credential. Treat it with the same care as the reports.
- **Easy to reset or inspect.** To start over, delete the file. To look inside it, open it with the DuckDB command line tool, e.g., `duckdb data/ahd.duckdb "select * from runs"`.

### The report

The report is one HTML file with its styles, script and data inside it. Open it in any browser by double clicking it. It loads nothing from the internet, so it also works offline. It has three tabs:

- **Components Summary:** every custom event, eVar and prop, with trend charts. You can view it by day or by week, and show only the flagged components.
- **Individual Component Summary:** one dimension in more depth. By default this is Page by Occurrences. It shows a trend, the top 100 values, and the top values for each browser group and device type.
- **Alerts Summary:** the values Adobe flagged as unusual yesterday, then the components with no data. You can copy the list as text.

Summary tiles at the top give yesterday's counts. Every table can be sorted, and a switch at the top right chooses light mode, dark mode, or your computer's setting.

Working offline does not mean the report is cleared for sharing. It contains your component names and values, so share it only where your organization allows.

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

## Known limits

- It covers one report suite per run.
- It is English only.
- It does not send email.
- Adobe publishes no rate limits, so the tool uses a small number of parallel requests and retries politely.

## Remake the slideshow

The slideshow at the top uses the real report, filled with synthetic data from `scripts/make_showcase.py`. It uses no Adobe API, credential or customer data. You need Google Chrome and FFmpeg installed. Run:

```
uv run python scripts/make_showcase.py --out docs
```

This writes `docs/showcase.gif` and `docs/showcase.png`.
