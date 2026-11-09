# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Report privately through
[GitHub private vulnerability reporting](https://github.com/brian-a-au/aa-health-duckdb/security/advisories/new).
The repository setting is enabled so outside reporters can open a private
report without repository access.

Do not open a public issue for a vulnerability. Include the affected version,
impact, reproduction steps, and any suggested mitigation, without adding
credentials, reports, database files or other data from Adobe. The maintainer
aims to acknowledge a report within one week.

## Threat model notes

The generated report is a static HTML file that embeds data from Adobe, e.g.,
component names and values. That data is treated as hostile: `<`, `>` and `&`
are emitted as JSON escapes in the embedded payload, template values are
Jinja-autoescaped, and the page script sets every payload-derived string with
`textContent` or DOM calls, never as HTML. Reports that bypass this escaping
are exactly what this policy is for. The report makes no network requests, and
its Content Security Policy blocks any attempt, so exfiltration requires a
separate delivery channel. Script execution in a viewer's browser is still a
real impact.

The tool only reads from Adobe, and it never prints, logs or stores a
credential value or a token. A way to make it change data in Adobe, or to make
it reveal a credential or a token, is also in scope.
