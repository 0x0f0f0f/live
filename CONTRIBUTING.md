# Contributing to ml4t-live

This repository follows the shared ML4T libraries workflow: changes go through pull requests, and
releases are cut from `main` after review and CI.

## Development Setup

```bash
git clone https://github.com/ml4t/live.git ml4t-live
cd ml4t-live

uv sync --dev
uv run pre-commit install
```

## Quality Gates

Run these before opening a PR:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run ty check
uv run pytest tests/ -q -m "not integration" --timeout=60 --ignore=tests/stress
```

## Branch And PR Policy

Do not push feature work directly to `main`.

Use this flow instead:

```bash
git checkout -b feat/my-change
# make changes
git push -u origin feat/my-change
gh pr create --title "feat: description" --body "..."
```

Expected merge path:

1. Open a PR against `main`
2. Wait for CI to pass
3. Address review feedback
4. Squash-merge the PR into `main`

Direct pushes to `main` are considered policy exceptions and should be avoided.

## Release Policy

Create releases from `main` after the relevant PRs have merged.

```bash
git checkout main
git pull
git tag -a v0.1.0b1 -m "Release 0.1.0b1"
git push origin v0.1.0b1
```

Notes:

- Alpha, beta, and release-candidate tags are treated as prereleases
- Tagging triggers the GitHub release workflow and PyPI publish workflow
- Do not tag from an unmerged feature branch

## Commit Messages

Use conventional prefixes:

- `feat:`
- `fix:`
- `docs:`
- `test:`
- `refactor:`
- `chore:`
