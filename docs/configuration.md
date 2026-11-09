# Configuration

[README](../README.md) · [Usage](usage.md) · **Configuration** · [The report](report.md) · [How runs are stored](runs.md) · [DuckDB guide](duckdb.md) · [Run it every day](scheduling.md) · [Troubleshooting](troubleshooting.md) · [Compared with the Excel workbook](excel-comparison.md) · [Development](development.md)

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
3. **A config file.** Pass its path with `--config`, or put a file named `config.json` in the folder where you run the tool. If you also set some of the `AHD_*` variables, each one wins over the same key in the file.

A profile and `--config` cannot be used together. The tool stops and says so, also when the profile comes from `AA_PROFILE`. When all four `AHD_*` variables are set, the tool uses them and does not read a config file.

When the tool finds no credential at all, it stops and lists the three ways to give one.

The tool reads a profile or config file as UTF-8 JSON. It accepts the byte-order mark that Windows PowerShell 5 adds. If the file is missing, is not valid JSON, or does not hold one JSON object, the tool stops and names the file.

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

If the product profile hides the custom components, the report shows no custom data. If "Anomaly Detection" is missing, the run still finishes, but the report has no expected values and no anomaly flags, and it says so.

Changes in the Admin Console can take a while to reach Analytics. Run `ahd check --rsid <suite>` to test the setup. It names anything that is missing and explains the fix.

## What the tool does with your credential

- It only reads from Adobe. Among Analytics API calls, the only one that sends a body is the report request, and it only reads data.
- When it signs in, it prints where the credential came from, e.g., "Signed in using profile prod". It prints only the name of the source.
- It never writes a credential value or a token to the screen, to logs, to the database or to the report.
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
