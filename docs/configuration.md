# Configuration

[README](../README.md) · [Usage](usage.md) · **Configuration** · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Email alerts](email-alerts.md) · [Slack alerts](slack-alerts.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

The tool signs in to Adobe with an [OAuth Server-to-Server credential](https://developer.adobe.com/developer-console/docs/guides/authentication/ServerToServerAuthentication/), which you create in the [Adobe Developer Console](https://developer.adobe.com/console/). This page explains how to create it, where the tool looks for it, and which permissions it needs.

## Create the credential

You need permission to create projects in the Adobe Developer Console. If you don't have it, ask a System Administrator of your Adobe organization.

1. Open the [Adobe Developer Console](https://developer.adobe.com/console/) and create a new project, or open an existing one.
2. Click **Add API**, choose **Adobe Analytics**, and choose **OAuth Server-to-Server** as the authentication type.
3. Open the new credential. Copy the **Client ID**, the **Client Secret**, the **Organization ID** and the **Scopes**.
4. Open the [Adobe Admin Console](https://adminconsole.adobe.com/). Under Adobe Analytics, open a product profile that holds the report suites you want to check, and add the credential's technical account to it under **API credentials**. See [Permissions](#permissions) for what the product profile must include.
5. Save the four values as described below, then run `ahd check`.

Adobe's guides explain each step in more depth:

- [OAuth Server-to-Server credentials](https://developer.adobe.com/developer-console/docs/guides/authentication/ServerToServerAuthentication/) in the Developer Console documentation
- [Getting started with the Analytics API](https://developer.adobe.com/analytics-apis/docs/2.0/guides/)
- [Product profiles for Adobe Analytics](https://experienceleague.adobe.com/en/docs/analytics/admin/admin-console/permissions/product-profile) on Experience League

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

Copy them from the credential's page in the Adobe Developer Console. The scopes must include `openid`, `AdobeID` and `additional_info.projectedProductContext`. Copy the full list from the console, e.g., with `read_organizations` too, rather than only these three.

## Where the tool looks

The tool uses the first of these that it finds:

1. **A profile.** Pass `--profile <name>`, or set the `AA_PROFILE` environment variable. The tool reads `~/.aa/orgs/<name>/config.json`.
2. **Environment variables.** Set all four of `AHD_ORG_ID`, `AHD_CLIENT_ID`, `AHD_SECRET` and `AHD_SCOPES`.
3. **A config file.** Pass its path with `--config`, or put a file named `config.json` in the folder where you run the tool. If you also set some of the four `AHD_` variables, each one wins over the same key in the file.
4. **The plain variables.** Set all four of `ORG_ID`, `CLIENT_ID`, `SECRET` and `SCOPES`. These are the names that the companion tools read.
5. **A `.env` file.** Put a file named `.env` in the folder where you run the tool, with the same four names, e.g., `SECRET=...`. The tool reads only these four lines of the file. A value may be in quotes, and a line may start with `export`. A `#` after a closing quote or after a space starts a comment. It does not read a `.env` file in a folder above, and it puts nothing from the file into the environment. So the file cannot set `AA_PROFILE` or the `AHD_ANOMALY_` variables.

The tool reads the plain variables and the `.env` file only when the first three give nothing at all. So a setup that worked before 0.1.23 gets the same credential as before. The tool takes all four values from the plain variables or all four from the `.env` file, and does not mix the two.

Run `ahd config` to see which source the tool uses. It makes no call to Adobe. See [config](usage.md#config).

A profile and `--config` cannot be used together. The tool stops and says so, also when the profile comes from `AA_PROFILE`.

`--config` and all four credential variables cannot be used together either, because the tool would have to ignore one of them. The tool stops and says so. When all four variables are set and you do not give `--config`, the tool uses the variables and does not read `./config.json`.

A `~` at the start of the `--config` path means your home folder, e.g., `--config "~/ahd/config.json"`, also in quotes.

Two more variables, `AHD_ANOMALY_METHOD` and `AHD_ANOMALY_SENSITIVITY`, hold no credential. They set the defaults of `--anomaly-method` and `--anomaly-sensitivity`. See [Set the anomaly method once](usage.md#set-the-anomaly-method-once).

When the tool finds no credential at all, it stops and lists the ways to give one.

The tool reads a profile or config file as UTF-8 JSON. It accepts the byte-order mark that Windows PowerShell 5 adds. If the file is missing, is not valid JSON, or does not hold one JSON object, the tool stops and names the file.

### Notices and warnings

Version 0.2.0 will look for the credential in another order, the one that the companion tools will share:

1. `--profile` or `--config`
2. `AA_PROFILE`
3. The environment variables, with or without `AHD_` in front
4. The `.env` file in the folder where you run the tool
5. `./config.json`

From 0.2.0, the tool also takes all four values from one source. A source that holds only some of them stops the command.

Until then, the tool keeps its order. When the new order would give another result, a command that signs in prints one line on stderr that starts with "Notice", and then goes on as before. `ahd config` lists the same notices. Each notice has a code:

| Code | What it means | What you can do |
|---|---|---|
| `plain_variables_not_used` | `ORG_ID`, `CLIENT_ID`, `SECRET` and `SCOPES` are all set, and the tool uses `./config.json`. From 0.2.0, it uses the variables. | Unset the variables, or remove the file. |
| `variable_forms_differ` | A variable is set with and without `AHD_` in front, e.g., `AHD_SECRET` and `SECRET`, with different values. The tool uses the `AHD_` name. From 0.2.0, it stops. | Set both to the same value, or unset one. |
| `mixed_sources` | Some of the `AHD_` variables are set, and the other keys come from a config file. From 0.2.0, the tool stops. | Set all four variables, or none. |
| `partial_source_skipped` | Some of the plain variables are set, or the `.env` file holds some of the four names. The tool does not read them. From 0.2.0, it stops. | Set all four, or none. |
| `dotenv_and_config_differ` | The `.env` file and `./config.json` hold different credentials. The tool uses `config.json`. From 0.2.0, it uses the `.env` file. | Keep one of the two files. |

A notice never changes the result or the exit code of a command, and it never goes to stdout.

On macOS and Linux, the tool also prints one line that starts with "Warning (file_readable_by_others)" when other users of the computer can read a profile, a config file or a `.env` file that it read. Make the file private, e.g., with `chmod 600 config.json`. When the file is a link, the tool checks the file that the link points to. The tool does not check this on Windows.

### Profiles

A profile is the same file that [aa_auto_sdr](https://github.com/brian-a-au/aa_auto_sdr) uses, so a profile you made with `aa_auto_sdr --profile-add` works in both tools, and one `AA_PROFILE` setting picks the organization for both. The tool only reads profiles. Use aa_auto_sdr to create, test or change them, e.g., `aa_auto_sdr --profile-test prod`.

A profile also keeps its own database, `~/.aa/orgs/<name>/ahd.duckdb`, so data from different organizations never shares a file. `--db` still wins over this. See [How runs are stored](runs.md).

## Share one credential across tools

One OAuth Server-to-Server credential can serve this tool and its two companion tools:

| Tool | What it does | Where it keeps profiles |
|---|---|---|
| `ahd` (this tool) | Health checks for Adobe Analytics report suites | `~/.aa/orgs/<name>/config.json` |
| [aa_auto_sdr](https://github.com/brian-a-au/aa_auto_sdr) | Solution Design Reference for Adobe Analytics report suites | `~/.aa/orgs/<name>/config.json` |
| [cja_auto_sdr](https://github.com/brian-a-au/cja_auto_sdr) | Solution Design Reference for Customer Journey Analytics data views | `~/.cja/orgs/<name>/config.json` |

All three read the same four values: `org_id`, `client_id`, `secret` and `scopes`.

[sdr-grader](https://github.com/brian-a-au/sdr-grader) and [sdr-visualizer](https://github.com/brian-a-au/sdr-visualizer) need no credential of their own. They read the JSON files that aa_auto_sdr and cja_auto_sdr write. With `--rsid`, each one runs aa_auto_sdr, which uses its profile as usual.

### ahd and aa_auto_sdr

These two share the profile folder. Create a profile once with `aa_auto_sdr --profile-add prod`, and both tools use it with `--profile prod` or `AA_PROFILE=prod`. Each tool only needs the Adobe Analytics API on the credential.

### Adding cja_auto_sdr

A project in the Adobe Developer Console can hold several APIs on one credential. To use one credential for all three tools:

1. In the [Adobe Developer Console](https://developer.adobe.com/console/), open the project that holds the credential.
2. Click **Add API** and add each API that the credential is missing: **Adobe Analytics** for `ahd` and aa_auto_sdr, and **Customer Journey Analytics** and **Adobe Experience Platform** for cja_auto_sdr. cja_auto_sdr needs the Experience Platform API too. Its [quickstart](https://github.com/brian-a-au/cja_auto_sdr/blob/main/docs/QUICKSTART_GUIDE.md#15-add-the-adobe-experience-platform-aep-api) explains why.
3. When the console asks for the authentication type, choose the existing **OAuth Server-to-Server** credential, so every API shares it.
4. Copy the scopes again. Adding an API can add scopes, and the credential's page lists the full set.
5. In the [Adobe Admin Console](https://adminconsole.adobe.com/), add the credential's technical account to a product profile for each product: Adobe Analytics, Customer Journey Analytics and Adobe Experience Platform. Adding an API in the Developer Console does not give access to data by itself.

Then put the same values in both profile folders. Copy the file:

```
mkdir -p ~/.aa/orgs/prod
cp ~/.cja/orgs/prod/config.json ~/.aa/orgs/prod/config.json
```

Or link it, so a rotated secret only needs to change in one place:

```
mkdir -p ~/.aa/orgs/prod
ln -s ~/.cja/orgs/prod/config.json ~/.aa/orgs/prod/config.json
```

Run `ahd check --profile prod` and `aa_auto_sdr --profile-test prod` afterwards. If `ahd check` signs in but sees no report suites, the technical account is not yet in an Adobe Analytics product profile. See [Troubleshooting](troubleshooting.md#sign-in-problems).

The maintainer tests the tool with this setup. The credential was made for cja_auto_sdr, and the Adobe Analytics API was added to it.

## Permissions

The credential's technical account must be added to an [Adobe Analytics product profile](https://experienceleague.adobe.com/en/docs/analytics/admin/admin-console/permissions/product-profile) in the [Adobe Admin Console](https://adminconsole.adobe.com/). The product profile must include:

- **The report suite's metrics and dimensions**, so that the custom events, eVars and props are visible.
- **The Report Suite Tools item "Anomaly Detection"**, so that the expected values and anomaly flags can be fetched.

If the product profile hides the custom components, the report shows no custom data. If "Anomaly Detection" is missing, the run still finishes, but the report has no expected values and no anomaly flags from Adobe, and it says so. A local anomaly method can still flag unusual values. See [Limited access](troubleshooting.md#limited-access).

Changes in the Admin Console can take a while to reach Analytics. Run `ahd check --rsid <suite>` to test the setup. It names anything that is missing and explains the fix.

## What the tool does with your credential

- It only reads from Adobe. Among Analytics API calls, the only one that sends a body is the report request, and it only reads data.
- When it signs in, it prints where the credential came from, e.g., "Signed in using profile prod". It prints only the name of the source.
- It never writes the secret or a token to the screen, to logs, to the database or to the report. No part of the secret is printed, also not its length.
- Only `ahd config` shows other values of the credential: the organization ID, the scopes, and the client ID with only its first 4 and last 4 characters.
- It never sends usage data anywhere.

Keep `config.json` out of version control. If you work from a clone of this repository, its `.gitignore` already leaves the file out.

If sign-in fails, see [Troubleshooting](troubleshooting.md#sign-in-problems).

## Adobe resources

- [Adobe Developer Console](https://developer.adobe.com/console/)
- [Adobe Admin Console](https://adminconsole.adobe.com/)
- [OAuth Server-to-Server credentials](https://developer.adobe.com/developer-console/docs/guides/authentication/ServerToServerAuthentication/)
- [Adobe Analytics 2.0 API documentation](https://developer.adobe.com/analytics-apis/docs/2.0/)
- [Getting started with the Analytics API](https://developer.adobe.com/analytics-apis/docs/2.0/guides/)
- [Product profiles for Adobe Analytics](https://experienceleague.adobe.com/en/docs/analytics/admin/admin-console/permissions/product-profile)
