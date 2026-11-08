# Configuration

[README](../README.md) · [Usage](usage.md) · **Configuration** · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Troubleshooting](troubleshooting.md)

The tool signs in to Adobe with an OAuth Server-to-Server credential, which you create in the Adobe Developer Console. This page explains where the tool looks for it, and which permissions the credential needs.

## The credential

The tool needs the four values of the credential:

```json
{
  "org_id": "YOUR_ORG_ID@AdobeOrg",
  "client_id": "YOUR_CLIENT_ID",
  "secret": "YOUR_CLIENT_SECRET",
  "scopes": "openid, AdobeID, additional_info.projectedProductContext"
}
```

Copy them from the credential's page in the Adobe Developer Console. The scopes must include `openid`, `AdobeID` and `additional_info.projectedProductContext`.

## Where the tool looks

The tool uses the first of these that it finds:

1. **A profile.** Pass `--profile <name>`, or set the `AA_PROFILE` environment variable. The tool reads `~/.aa/orgs/<name>/config.json`.
2. **Environment variables.** Set all four of `AHD_ORG_ID`, `AHD_CLIENT_ID`, `AHD_SECRET` and `AHD_SCOPES`.
3. **A config file.** Pass its path with `--config`, or put a file named `config.json` in the folder where you run the tool. If you also set some of the `AHD_*` variables, each one wins over the same key in the file.

The tool reads a profile or config file as UTF-8 JSON. It accepts the byte-order mark that Windows PowerShell 5 adds. If the file is missing, is not valid JSON, or does not hold one JSON object, the tool stops and names the file.

### Profiles

A profile is the same file that aa_auto_sdr uses, so a profile you made with `aa_auto_sdr --profile-add` works in both tools, and one `AA_PROFILE` setting picks the organization for both. The tool only reads profiles. Use aa_auto_sdr to create, test or change them.

A profile also keeps its own database, `~/.aa/orgs/<name>/ahd.duckdb`, so data from different organizations never shares a file. `--db` still wins over this. See [How runs are stored](runs.md).

## Permissions

The credential's technical account must be added to an Adobe Analytics product profile in the Adobe Admin Console. The product profile must include:

- **The report suite's metrics and dimensions**, so that the custom events, eVars and props are visible.
- **The Report Suite Tools item "Anomaly Detection"**, so that the expected values and anomaly flags can be fetched.

If the product profile hides the custom components, the report shows no custom data. If "Anomaly Detection" is missing, the run still finishes, but the report has no expected values and no anomaly flags, and it says so.

Changes in the Admin Console can take a while to reach Analytics. Run `ahd check --rsid <suite>` to test the setup. It names anything that is missing and explains the fix.

## What the tool does with your credential

- It only reads from Adobe. Among Analytics API calls, the only one that sends a body is the report request, and it only reads data.
- When it signs in, it prints where the credential came from, e.g., "Signed in using profile prod". It prints only the name of the source.
- It never writes a credential value or a token to the screen, to logs, to the database or to the report.
- It never sends usage data anywhere.

Keep `config.json` out of version control. This repository's `.gitignore` already does.

If sign-in fails, see [Troubleshooting](troubleshooting.md#sign-in-problems).
