# Contributing

This file tells you how to set up, test and commit. `README.md` tells you what VISR is.

## Set up

Python 3.11 with [uv](https://docs.astral.sh/uv/):

    uv venv --python 3.11 .venv
    uv pip install --python .venv -r correlation/requirements.txt pytest numba pymodbus==3.6.9

The console needs Node.js 20 or later:

    cd dashboard
    npm ci
    npm run dev

## Test

    python -m pytest plant/tests twin/tests eval/tests -q

The plant reads the site weather from `data/weather/` when the file is there. Without it, the plant
uses its design-condition weather, as CI does.

## What goes into git

Code, tests, configuration, `README.md` and the deploy instructions. Nothing else:

- No data: measured or field data, recordings, simulated-day caches, scores, trained models.
- No documents: decks, PDFs, reports, plans, logs, research material.
- No binaries or media: videos, archives, disk images.
- No credentials and no personal paths: passwords, tokens, keys, a home folder with a user name.

`scripts/check_commit.py` checks this. CI runs it on every push and pull request. Run it before
each commit:

    python scripts/check_commit.py --staged

Or install the hook in a clone (not in a worktree: a worktree shares its hook folder with the main
checkout):

    pip install pre-commit
    pre-commit install

If a credential gets into a commit, rotate it. Removing it from the file is not enough, because the
history keeps the old commit.

## Commit messages

Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

    <type>(<scope>): <summary>

    <body: what changed and why>

    Refs: LOG-nnn

- Types: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`.
- Scopes: `plant`, `twin`, `eval`, `console`, `api`, `scada`, `vplc`, `plc`, `deploy`, `repo`.
- The summary is in the imperative mood and has 72 characters or fewer.
- `Refs:` names the entry of the private decision log, when there is one.

## Branches

- `main` holds the Stage 2 proof of concept. It stays as it is until the overhaul replaces it.
- `overhaul` is the development branch.
- Make a topic branch from `overhaul`. Merge it with a pull request after CI passes.

## Line endings and format

The repository stores LF line endings (`.gitattributes`). `.editorconfig` sets the line endings, the
indentation and the final newline for your editor.
