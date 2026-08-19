<div align="center">

# SSH Console

**A local, single-user web console for your SSH servers — with session recording.**

Open any server that runs `sshd` in a browser tab: a real terminal, an SFTP file
manager, and a one-shot command runner. Every terminal session is **recorded and
replayable** with its original timing.

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-2b6858)
![Runs on Docker](https://img.shields.io/badge/Docker-ready-2b6858)
![License MIT](https://img.shields.io/badge/License-MIT-2b6858)
![No account · localhost](https://img.shields.io/badge/Auth-none%20·%20localhost-2b6858)

</div>

![The live terminal](assets/screenshot-terminal.svg)

---

## Contents

- [Overview](#overview) · [Features](#features) · [Screenshots](#screenshots)
- [**Run it** (pick one way)](#run-it) → [Compose](#option-1--docker-compose-recommended) · [Plain Docker](#option-2--plain-docker) · [From source](#option-3--from-source-python-311)
- [Open it from your laptop (remote server)](#open-it-from-your-laptop-remote-server)
- [✅ Do & ❌ Don't](#-do--dont)
- [Configuration](#configuration) · [Security](#security) · [How it works](#how-it-works)

## Overview

SSH Console runs on **your own machine (or a server)** and gives you a browser UI over
your SSH servers. Add a server once (its credential is encrypted on disk), then from a
browser tab you get a real interactive terminal, an SFTP file manager, and a command
runner — and every terminal session is captured so you can replay exactly what was
run, and for how long.

It is deliberately small and focused: **no accounts, no login, no cloud.** It binds to
`127.0.0.1` by default and keeps everything in one local SQLite file. There is **no
installer and no binary to manage** — you run it as a Docker container or as a Python
module (`python -m ssh_console`).

## Features

| | |
|---|---|
| 🖥️ **Web terminal** | A real PTY over SSH, in the browser (xterm.js). `top`, `vim`, `less` all work. |
| ⏺️ **Session recording** | Every session captured **with timing** and replayable — play / pause / seek / 1×·2×·4×. |
| 📁 **SFTP file manager** | Browse, upload (multi + drag-drop), download, in-place edit, mkdir, rename, delete. |
| ⚡ **Command runner** | Run a one-off command and see its output, with a palette of common commands. |
| 🔑 **Flexible auth** | Password or private key (upload the key file, or paste it). |
| 📌 **Host-key pinning** | Trust-on-first-use; if a server's key later changes, the connection is refused. |
| 🔒 **Encrypted at rest** | Credentials and recordings sealed with AES-256-GCM under a local master key. |
| 🌓 **Light / dark** | Follows your OS, with a manual toggle in the top-right corner. |

## Screenshots

**Manage your connections** — add a server, then pick it from the sidebar:

![Connections](assets/screenshot-connections.svg)

**Replay a recorded session** — with its original timing, scrub and speed controls:

![Session replay](assets/screenshot-replay.svg)

---

# Run it

> ### ⚠️ Pick **one** way and stick with it
> Compose, plain Docker, and from-source all start the **same app on port 8022**.
> **Do not run two of them at once** — they'll collide on port 8022 (`port is already
> allocated`). To switch methods, stop the current one first (`docker compose down`,
> or `docker rm -f ssh-console`).

**You need:** just **Docker** (Options 1–2), or **Python 3.11+** (Option 3). Clone first:

```bash
git clone https://github.com/AbdulRauf-1/ssh-console.git
cd ssh-console
```

## Option 1 — Docker Compose *(recommended)*

One command builds and runs it.

**On your own laptop/PC (private — the default):**

```bash
docker compose up -d
```

Open **<http://127.0.0.1:8022>**.

**On a server, reachable by its public IP:**

```bash
SSHCONSOLE_BIND=0.0.0.0 docker compose up -d
```

Then [open the firewall](#b-public-ip-open-the-firewall) and browse `http://YOUR_SERVER_IP:8022`.

Everyday commands:

```bash
docker compose logs -f        # watch logs
docker compose up -d --build  # rebuild after you change/pull code
docker compose down           # stop (your data volume stays)
```

> **Tip:** to make public-mode permanent, put it in a `.env` file next to
> `docker-compose.yml` so you don't retype it: `echo "SSHCONSOLE_BIND=0.0.0.0" > .env`.

## Option 2 — Plain Docker

If you'd rather not use Compose. **Build first** (the image isn't published, so `docker
run` alone fails with *"pull access denied"*), then run **one** of these:

```bash
docker build -t ssh-console .

# private (localhost only):
docker run -d --name ssh-console -p 127.0.0.1:8022:8022 -v ssh-console-data:/data ssh-console

# public (server's IP):
docker run -d --name ssh-console -p 8022:8022 -v ssh-console-data:/data ssh-console
```

To re-run after a change: `docker rm -f ssh-console` first, rebuild, then run again.

## Option 3 — From source (Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m ssh_console                             # private, http://127.0.0.1:8022
SSHCONSOLE_LISTEN=0.0.0.0:8022 python -m ssh_console   # reachable on the server's IP
```

Check the version with `python -m ssh_console version`.

---

## Open it from your laptop (remote server)

If it runs on a VPS/EC2, there are two ways to reach it. **The tunnel is safer** —
nothing is exposed to the internet.

### A. SSH tunnel *(recommended — private, no firewall change)*

Run it **loopback** on the server (Option 1 default), then from your laptop:

```bash
ssh -L 8022:127.0.0.1:8022 ubuntu@YOUR_SERVER_IP
```

Leave that open and browse **<http://127.0.0.1:8022>** on your laptop.

### B. Public IP *(open the firewall)*

1. Start it in public mode (`SSHCONSOLE_BIND=0.0.0.0 …` or `-p 8022:8022`).
2. **Open the port to your own IP only:**
   - **AWS EC2:** Security Group → Inbound rules → add **Custom TCP, Port 8022, Source = My IP**.
   - **ufw (if active):** `sudo ufw allow from YOUR_LAPTOP_IP to any port 8022 proto tcp`
3. Browse **`http://YOUR_SERVER_PUBLIC_IP:8022`** (the *public* IP, not `172.31.x.x`).

Sanity check on the server itself: `curl -s -o /dev/null -w "%{http_code}\n"
http://127.0.0.1:8022/` → `200` means the app is healthy and anything else failing is
firewall/network.

---

## ✅ Do & ❌ Don't

**✅ Do**

- **Pick one run method** (Compose *or* plain Docker *or* source) and stop the old one
  before switching.
- On a server, **prefer the SSH tunnel**; if you expose it publicly, restrict the
  firewall to **your IP**.
- Change only the **left** side of the port map to use a different port
  (`-p 9000:8022`, or `SSHCONSOLE_HOST_PORT=9000` with Compose).
- **Back up the data directory / `ssh-console-data` volume** — it holds `master.key`,
  which decrypts everything.

**❌ Don't**

- **Don't run two methods at once** — Compose *and* `docker run` both want port 8022
  → *"port is already allocated"*. `docker compose down` (or `docker rm -f
  ssh-console`) first.
- **Don't put an IP in `-p`** — `-p 18.116.66.88:8022:8022` fails with *"cannot assign
  requested address"* (a cloud VM's public IP isn't on its own network interface). Use
  `-p 8022:8022`.
- **Don't change the right-hand port** — the app inside the container always listens on
  **8022**. `-p 8023:8023` binds nothing. Use `-p 8023:8022`.
- **Don't `docker run` before `docker build`** — you'll get *"pull access denied"*.
- **Don't expose it to `0.0.0.0/0`** — there is **no login**; anyone who reaches the
  port controls every SSH server you saved. Lock it to your IP, or use the tunnel.
- **Don't lose `master.key`** — without it, stored credentials and recordings can't be
  decrypted.

---

## Configuration

All optional. Set via environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `SSHCONSOLE_BIND` | `127.0.0.1` | *(Compose only)* interface to publish on. Set `0.0.0.0` to reach it by the server's IP. |
| `SSHCONSOLE_HOST_PORT` | `8022` | *(Compose only)* the port you browse to (host side). |
| `SSHCONSOLE_LISTEN` | `127.0.0.1:8022` | *(source / plain container)* address the app binds to. |
| `SSHCONSOLE_DATA_DIR` | per-OS / `/data` | Where the SQLite DB and master key live. |
| `SSHCONSOLE_MAX_TERMINALS` | `0` (unlimited) | Optional cap on concurrent live terminals. No limit on saved connections. |

**The data directory** holds `ssh-console.db` (connections + recordings, secrets
encrypted) and `master.key` (the AES-256 key that decrypts them). In Docker it lives in
the named volume; from source it defaults to your per-user config dir
(`~/.config/ssh-console`, `%AppData%\ssh-console`, or `~/Library/Application
Support/ssh-console`). **Back it up; never commit it** (the `.gitignore` blocks it).

## Security

- **No login by default; localhost only.** There's nothing between you and the tool on
  your own machine. If you expose it, restrict the firewall to your IP and ideally put
  a reverse proxy with auth + TLS in front.
- **Encrypted at rest** — passwords, keys, passphrases and recordings are sealed with
  AES-256-GCM before hitting the database.
- **Host-key pinning (TOFU)** — a server's key is pinned on first connect and verified
  every time after, during the handshake, before any credential is sent.
- **Recordings can contain secrets** — treat them as sensitive; delete what you don't need.
- **Outbound guard** — link-local / cloud-metadata addresses (e.g. `169.254.169.254`)
  are refused; private LAN ranges are allowed.
- **Keys:** OpenSSH / PEM private keys (PuTTY `.ppk` is not supported).

## How it works

```
                 ┌──────── Web UI (FastAPI + Jinja2 + htmx + xterm.js) ────────┐
   browser ────▶ │  connections · terminal (WebSocket PTY) · files · replay    │
                 └───────────────┬──────────────────────────────────┬──────────┘
                                 │ calls                             │ reads / writes
                         ┌───────▼──── SSH engine (AsyncSSH) ──┐  ┌──▼──── SQLite store ────┐
   your SSH servers ◀────│  one dial-and-pin path · run · SFTP │  │  servers + recordings   │
                         └───────┬─────────────────────────────┘  └──┬──────────────────────┘
                                 └──────── AES-256-GCM (local master key) ───────┘
```

One connect path (so host-key pinning can't be bypassed), credentials decrypted only in
memory to dial out, everything at rest encrypted under a key that never leaves the
machine. For a full file-by-file tour, see the code — it's small and commented.

```
ssh_console/
├── __main__.py   entrypoint (python -m ssh_console)
├── config.py     env, data dir, master key
├── crypto.py     AES-256-GCM envelope encryption
├── store.py      SQLite: connections + recordings
├── remote/       the SSH engine (AsyncSSH): dial+pin, run, SFTP
└── web/          FastAPI routes, WebSocket terminal, templates, static
```

**Stack:** FastAPI + Uvicorn · AsyncSSH · `cryptography` · SQLite (stdlib) · Jinja2 ·
xterm.js + htmx.

## License

[MIT](LICENSE) © Abdul Rauf.
