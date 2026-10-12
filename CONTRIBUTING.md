# Contributing

## Getting set up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
cp .env.example .env
docker compose up -d
make demo
```

## Branch naming

- `feature/<short-topic>` — new functionality
- `fix/<short-topic>` — bug fixes
- `chore/<short-topic>` — tooling, deps, docs
- `test/<short-topic>` — test-only changes

## Commit messages

Conventional Commits, imperative mood, scoped when useful:

```
feat(ingest): advance watermark inside the landing transaction
fix(handler): reject null-island coordinates before insert
docs(readme): add measured run results
test: cover idle segmentation known answer
```

One logical change per commit. Do not combine unrelated changes.

## Code style

- **Python**: `ruff` (config in `pyproject.toml`). Type hints on all public
  functions, docstrings on modules and public functions. Run `make fmt` then
  `make lint`.
- **JavaScript**: `eslint` flat config (`eslint.config.mjs`). CommonJS modules,
  `'use strict';`, JSDoc on exported functions.
- **SQL**: keep DDL/DML in `sql/*.sql` files; migrations must be idempotent
  (`IF NOT EXISTS` / `CREATE OR REPLACE` / truncate-and-reload). Never edit an
  applied migration to change history — add a new numbered file.

## Tests

Add tests for every behavioural change. CI enforces:

- `ruff check src tests scripts` and `npm run lint`
- `make demo` (which fails if any DQ gate fails)
- `python -m pytest` and `npm test`

Run the same locally before opening a PR. DB-backed tests skip automatically when
Postgres is unavailable.

## Pull-request checklist

- [ ] `make lint` and `make fmt` are clean
- [ ] `make demo` completes green (7/7 DQ gates)
- [ ] `make test` passes; new code has tests
- [ ] No hardcoded secrets, tokens or DSNs (use `.env` / env vars)
- [ ] Public Python functions have type hints and docstrings
- [ ] Migrations are idempotent and ordered by filename
- [ ] README / docs updated if behaviour or outputs changed
- [ ] `reports/metrics.json` regenerated if the README results table changed

## Secrets

Never commit `.env`, tokens, keys or connection strings. `.env.example` documents
the variables; CI supplies them through the workflow environment.
