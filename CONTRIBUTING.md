# Contributing to SSH Console (Python)

Thanks for your interest. SSH Console is intentionally small and focused — a
local, single-user tool — so the most useful contributions keep it that way.

## Scope

**In scope:** the web terminal, session recording/replay, the SFTP file manager,
the command runner, connection management, and anything that makes running it
locally on Windows / macOS / Linux / Docker smoother.

**Out of scope** (by design): user accounts, teams, roles, or login; a hosted
control plane; billing/quotas; a fleet dashboard, metrics, or alerting. If a change
needs any of those, it belongs in a different project.

## Security invariants (do not break)

These are the reason the tool is safe to run. A change that regresses one will be
declined:

- **One dial-and-pin path** (`remote.Manager.connect`). Every feature connects
  through it; there is no host-key-verification bypass anywhere.
- **Host keys are pinned trust-on-first-use.** The pin is written only when none is
  stored yet (`store.pin_ssh_host_key` guards on `host_key IS NULL`); a mismatch
  refuses the connection, and the check runs before any credential is sent.
- **Credentials and recordings are encrypted at rest** (`crypto.encrypt_for`), bound
  to a purpose and the owning row. The `.db` carries no plaintext secret.
- **Every SSH exec is bounded** in time and output (`Manager._run_on`).
- **The recursive-delete guard** (`web.app.resolve_delete_target`) runs before any
  SFTP remove.
- **Remote paths use `posixpath`, never `os.path`** — the target is always Linux,
  SSH Console may run on Windows.
- **The outbound dial guard** (`remote.dialguard`) refuses link-local / metadata
  targets; private ranges stay allowed.

## Development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m ssh_console               # run it
pytest                              # run the tests
```

Please keep the README current on **what the project is** and **how to run it
locally on Windows, macOS, Linux, and via Docker** — that is the README's job.

For anything you'd rather raise privately (including security reports), email
**rauf89772@gmail.com** instead of opening a public issue.
