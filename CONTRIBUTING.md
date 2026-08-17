# Contributing to factum-fic

First off, thanks for taking the time to contribute! 🎉

factum-fic is an **open-core** project. The core engine is free and open-source under
AGPL-3.0. Contributions of all kinds are welcome — code, documentation, bug reports,
feature ideas, or translations.

---

## 📋 Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating,
you are expected to uphold it. Report unacceptable behavior to info@pyragogy.org.

---

## 🚀 Quick start

```bash
# Clone & enter
git clone https://github.com/pyragogy/factum-fic.git
cd factum-fic

# Install with uv (recommended) or pip
uv sync --all-extras --dev

# Run tests
uv run pytest -q

# Lint
uv run ruff check .

# Try it
uv run factum-fic --help
```

---

## 🧪 Before submitting a PR

1. **One feature per PR** — small, focused changes are reviewed faster.
2. **Tests are required** — new features need new tests; `pytest -q` must pass.
3. **Ruff-clean** — run `ruff check .` before pushing; the CI will enforce it.
4. **No secrets ever** — `.env` is gitignored. Never commit API keys, tokens, or passwords.
5. **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat:` — new feature (minor version bump)
   - `fix:` — bug fix (patch version bump)
   - `docs:` — documentation only
   - `refactor:` — code change with no functional difference
   - `test:` — adding or updating tests
   - `chore:` — tooling, CI, dependencies

---

## 📁 Structure

```
factum-fic/
├── src/factum_fic/         # Source code
│   ├── cli/                # CLI commands (elabora, status, history, setup, verify)
│   ├── core/               # Core logic (parser, pipeline, mappers, clients)
│   ├── storage/            # SQLite queue
│   └── watcher/            # Watchdog daemon
├── tests/                  # Pytest suite (168+ tests)
├── scripts/                # Utility scripts (test PDF generation)
├── .github/                # CI workflows, assets
└── docs/                   # Documentation
```

---

## 🔐 Security

If you discover a security vulnerability, **do not open a public issue**. Email
info@pyragogy.org instead. See [SECURITY.md](SECURITY.md) for details.

---

## 📖 More

- [README.md](README.md) — project overview
- [ROADMAP.md](ROADMAP.md) — planned features
- [ARCHITECTURE.md](docs/architecture.md) — system design (coming soon)

---

*Made with ❤️ by Fabrizio Terzi and contributors.*