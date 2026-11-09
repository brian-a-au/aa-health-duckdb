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

## What the report protects against

The report is one HTML file that holds data from Adobe, e.g., component names and values. The tool treats that data as unsafe:

- In the data inside the page, `<`, `>` and `&` are written as JSON escapes.
- The template engine escapes every value it puts in the page.
- The page script puts text from the data in the page with `textContent` or DOM calls, never as HTML.

A way around this escaping is a security issue. Please report it. The report makes no network requests, and its Content Security Policy blocks any attempt. A script that ran in the page could therefore not send data anywhere by itself. A script that runs in a viewer's browser is still a real problem.

The tool only reads from Adobe, and it never prints, logs or stores a
credential value or a token. A way to make it change data in Adobe, or to make
it reveal a credential or a token, is also in scope.
