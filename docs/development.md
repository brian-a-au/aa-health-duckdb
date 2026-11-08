# Development

[README](../README.md) · [AGENTS.md](../AGENTS.md) · **Development**

This page is for anyone who changes the code. [AGENTS.md](../AGENTS.md) explains the layout of the code and the rules for changing it. It is written for people and for AI coding agents.

## Tests and lint

Run the same checks as GitHub Actions:

```
uv run pytest --cov=ahd --cov-branch
uv run ruff check
uv run ruff format --check
```

- The tests use saved Adobe responses and never call Adobe.
- Every test runs with the network blocked, so a test fails if it tries to open a connection.
- The tests ignore your own `AA_PROFILE`, `AHD_*` variables and `~/.aa` profiles.
- The example script in `docs/examples` has tests too.

GitHub Actions runs the tests on Python 3.12, 3.13 and 3.14 for every push to `main` and every pull request. A run fails if branch coverage drops below 95 percent.

## Remake the slideshow

The slideshow at the top of the README uses the real report, filled with synthetic data from `scripts/make_showcase.py`. It uses no Adobe API, credential or customer data. You need Google Chrome and FFmpeg. Run:

```
uv run python scripts/make_showcase.py --out docs
```

This writes `docs/showcase.gif` and `docs/showcase.png`.
