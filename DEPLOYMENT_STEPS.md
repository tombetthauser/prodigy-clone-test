# Raspberry Pi Deployment Steps (Auto Pull + Auto Start)

This sets up `prodigy-clone-test` on the Pi with the same pattern you described:

1. On boot, pull latest code from GitHub
2. Start server automatically with `systemd`

---

## 0) Decide your values (fill these in once)

- `PI_HOST`: `tom@192.168.1.222`
- `APP_DIR`: `/srv/prodigy/repo/prodigy`
- `DEPLOY_SCRIPT`: `/usr/local/bin/prodigy-server.sh`
- `SERVICE_NAME`: the systemd unit that actually runs this app (discover with commands below)
- `BRANCH`: `main`
- `PORT`: `8001` (keep this distinct from the existing `thinkpad.club` app port)
- `PYTHON_BIN`: `/usr/bin/python3`

If your other project already uses a different structure, keep that structure and just swap names/paths below.

Where each value is set:

- `PI_HOST`: used in the SSH commands you run from your local machine (`ssh ...`).
- `APP_DIR`: set in command paths, in the deploy script (`APP_DIR="..."`), and in systemd service `WorkingDirectory`.
- `DEPLOY_SCRIPT`: command used for manual pull/sync.
- `SERVICE_NAME`: used in `systemctl status/restart` and `journalctl -u`.
- `BRANCH`: set inside deploy script as `BRANCH="main"`.
- `PORT`: set in `/etc/systemd/system/prodigy-clone-test.service` (`Environment=PORT=...` and `uvicorn --port ...`) and in Caddy (`reverse_proxy 127.0.0.1:PORT`).
- `PYTHON_BIN`: used in scripts/commands that create the venv (for this doc, it is effectively the `python3` command you run on the Pi).

Important: Step 0 itself does not write values anywhere yet; it is a checklist.  
You actually apply those values in Steps 1-10 when creating/editing files and running commands.

Detect active script/service names on your Pi first (recommended before any restart commands):

```bash
ssh tom@192.168.1.222
ls /usr/local/bin/ | grep -Ei 'prodigy|thinkpad|deploy'
systemctl list-unit-files | grep -Ei 'prodigy|thinkpad'
```

Then inspect candidate services and choose the one whose `WorkingDirectory` is `/srv/prodigy/repo/prodigy`:

```bash
systemctl cat prodigy-app.service
systemctl cat prodigy.service
systemctl cat thinkpad-server.service
```

---

## 1) SSH into the Pi and install prerequisites

```bash
ssh tom@192.168.1.222
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

---

## 2) Create app directory in `/srv` with correct ownership, then clone

```bash
sudo mkdir -p /srv/prodigy/repo
sudo chown -R tom:tom /srv/prodigy
cd /srv/prodigy/repo
git clone <YOUR_GITHUB_REPO_URL> prodigy
cd /srv/prodigy/repo/prodigy
```

If the repo is private, make sure deploy SSH keys/token are already configured on the Pi first.

If you already cloned with `sudo`, fix ownership before continuing:

```bash
sudo chown -R tom:tom /srv/prodigy
```

---

## 3) Create virtualenv and install dependencies

```bash
cd /srv/prodigy/repo/prodigy
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

Important:

- Do **not** run `sudo source .venv/bin/activate` (`source` is a shell builtin).
- Do **not** use `sudo pip ...` for this project; install through the venv pip as shown above.

---

## 4) Create the deploy script used at boot

Create `/usr/local/bin/prodigy-server.sh`:

```bash
sudo tee /usr/local/bin/prodigy-server.sh > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/prodigy/repo/prodigy"
BRANCH="main"

echo "==> Boot deploy for prodigy-clone-test"
cd "${APP_DIR}"

echo "==> Fetch + pull"
git fetch origin
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "==> Install/update Python deps"
if [ ! -d ".venv" ]; then
  /usr/bin/python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "==> Deploy script complete"
EOF
```

Make executable:

```bash
sudo chmod +x /usr/local/bin/prodigy-server.sh
```

---

## 5) Create systemd service to run boot-time git pull

Create `/etc/systemd/system/prodigy-clone-test-deploy.service`:

```bash
sudo tee /etc/systemd/system/prodigy-clone-test-deploy.service > /dev/null <<'EOF'
[Unit]
Description=Boot deploy (git pull) for prodigy-clone-test
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=tom
WorkingDirectory=/srv/prodigy/repo/prodigy
ExecStart=/usr/local/bin/prodigy-server.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
```

---

## 6) Create systemd service to run the FastAPI server

Create `/etc/systemd/system/prodigy-clone-test.service`:

```bash
sudo tee /etc/systemd/system/prodigy-clone-test.service > /dev/null <<'EOF'
[Unit]
Description=Prodigy clone test FastAPI server
After=network.target prodigy-clone-test-deploy.service
Wants=prodigy-clone-test-deploy.service

[Service]
Type=simple
User=tom
WorkingDirectory=/srv/prodigy/repo/prodigy
Environment=PORT=8001
ExecStart=/srv/prodigy/repo/prodigy/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

> If you change `PORT`, update both `Environment=PORT=...` and the `--port ...` value.

---

## 7) Enable and start services

```bash
sudo systemctl daemon-reload
sudo systemctl enable prodigy-clone-test-deploy.service
sudo systemctl enable prodigy-clone-test.service

sudo systemctl start prodigy-clone-test-deploy.service
sudo systemctl start prodigy-clone-test.service
```

---

## 8) Verify status and logs

```bash
systemctl status prodigy-clone-test-deploy.service --no-pager
systemctl status prodigy-clone-test.service --no-pager
journalctl -u prodigy-clone-test-deploy.service -n 150 --no-pager
journalctl -u prodigy-clone-test.service -n 150 --no-pager
```

Smoke-test the app locally on Pi:

```bash
curl -I http://127.0.0.1:8001/login
```

You should get an HTTP response (typically `200` or a redirect), which confirms the app is serving.

---

## 9) Reboot test (proves boot automation)

```bash
sudo reboot
```

After Pi comes back:

```bash
ssh tom@192.168.1.222
systemctl status prodigy-clone-test-deploy.service --no-pager
systemctl status prodigy-clone-test.service --no-pager
```

---

## 10) Reverse proxy for `prodigy.thinkpad.club` (Caddy)

`Caddy` is the name of a specific web server program (similar category as Nginx/Apache).  
In this setup, "Caddy" refers to the `caddy` service running on your Raspberry Pi and reading config from `/etc/caddy/Caddyfile`.

Quick definitions:

- **Reverse proxy:** a front-door server that receives internet requests for domains (like `prodigy.thinkpad.club`) and forwards them to an internal app process (like `127.0.0.1:8001`).
- **TLS certificate:** the HTTPS certificate that proves the site identity and encrypts traffic between browser and server (the lock icon in the browser).

What Caddy is doing for you:

1. Listens publicly on ports 80/443 for your domains.
2. Automatically gets/renews TLS certificates for those domains.
3. Proxies each domain/subdomain to the right local app port.

Since this Pi already serves `thinkpad.club`, add a second Caddy route that points the subdomain to this app's port (`8001`).

Also ensure DNS has an `A` record for `prodigy.thinkpad.club` pointing to your Pi's public IP.

Where/how to do this step:

1. SSH into the Raspberry Pi (this is done on the Pi itself, not in this repo).
2. Edit Caddy's config file at `/etc/caddy/Caddyfile`.
3. Add the `prodigy.thinkpad.club` block shown below.
4. Validate the config, then reload Caddy.

Commands:

```bash
ssh tom@192.168.1.222
sudo nano /etc/caddy/Caddyfile
```

Example Caddy block:

```caddy
prodigy.thinkpad.club {
    reverse_proxy 127.0.0.1:8001
}
```

Then reload Caddy:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Verify externally:

```bash
curl -I https://prodigy.thinkpad.club/login
```

---

## Operational commands

Manual pull + restart:

```bash
ssh tom@192.168.1.222 '/usr/local/bin/prodigy-server.sh'

# Then restart the app service that points to /srv/prodigy/repo/prodigy:
ssh tom@192.168.1.222 'sudo systemctl restart <SERVICE_NAME>'
```

Quick logs:

```bash
ssh tom@192.168.1.222 'journalctl -u <SERVICE_NAME> -n 200 --no-pager'
```

---

## One-time data sync + never override guide

If you intentionally pushed `data/` and/or `static/` once and now want server-local changes to persist without future pull overrides:

```bash
cd /srv/prodigy/repo/prodigy

# Mark currently tracked files under these directories as local-only
git ls-files -z static data | xargs -0 git update-index --skip-worktree

# Verify (S means skip-worktree set)
git ls-files -v static data | grep '^S'
```

If you later want to re-sync them from remote:

```bash
cd /srv/prodigy/repo/prodigy
git ls-files -z static data | xargs -0 git update-index --no-skip-worktree
git pull --ff-only origin main
```

Recommended long-term: keep runtime data outside Git tracking (`data/` ignored/untracked), and only version code/static assets that should be shared.

---

## Troubleshooting quick hits

- If boot pull fails: check repo auth on Pi (`git fetch origin` manually in app dir).
- If service fails: confirm `uvicorn` exists in `.venv` and `requirements.txt` installed cleanly.
- If port conflict: switch to another free port and update service + proxy config.
- If static/media issues: verify write permissions under `/srv/prodigy/repo/prodigy`.
- If pull says files were changed/deleted unexpectedly (especially under `data/` or `static/`), check skip-worktree status:
  - `git ls-files -v static data | grep '^S'`
  - re-apply `git update-index --skip-worktree` if needed.
