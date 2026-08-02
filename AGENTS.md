# Repository Boundaries

- Use the declared Python 3.12 environment through `uv sync` and `uv run`; do not use global `pip`.
- Never read, print, commit, log, screenshot, or place secret values in Docker environment variables. Runtime secrets are file-mounted from the documented secret directories.
- Logs and metric labels must not contain message bodies, email addresses, raw Feishu identifiers, tokens, URLs with credentials, or third-party response bodies.
- Keep `/v1/notifications` compatible with the documented HMAC contract. Contract changes require tests and an ADR.
- Bot commands remain read-only unless the user explicitly authorizes a new state-changing workflow and its approval model.
- Before deployment, run `uv run ruff check .`, `uv run mypy`, `uv run pytest`, `docker compose config -q`, and a fresh image build.
- Do not modify production routing, credentials, DNS, certificates, or persistent data during ordinary code work.
