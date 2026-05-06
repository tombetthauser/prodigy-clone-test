# Raspberry Pi Auto-Deploy Handoff

This document explains how `thinkpad-club-server` is currently wired for deploy/startup on the Raspberry Pi, and how to replicate the same pattern for a second project on the same Pi under a different domain.

## Current Deploy Architecture (this repo)

- App process: plain Node server (`server.js`), expected on `127.0.0.1:3000`
- Process manager on Pi: `systemd` service named `thinkpad-server` (referenced in `README.md`)
- Deploy trigger path:
  1. Open `/config.html`
  2. Click **Pull Latest from GitHub**
  3. Browser sends `POST /admin/deploy`
  4. Server executes `/usr/local/bin/deploy-thinkpad-server.sh`
  5. Deploy script does `git pull` + service restart + health check

Code references:
- Deploy endpoint and command: `server.js`
- Deploy button UI: `public/config.html`
- Manual deploy commands and service name: `README.md`

## What Must Exist on the Pi

For this project to auto-pull and auto-start correctly, the Pi must have:

1. A checked-out git repo directory for this app
2. A systemd unit for the app (`thinkpad-server.service`)
3. A deploy script at `/usr/local/bin/deploy-thinkpad-server.sh`
4. A reverse proxy (likely Caddy or Nginx) routing domain traffic to `127.0.0.1:3000`

## Verify Existing Pi Setup (read-only checks)

Run these from your local machine:

```bash
ssh tom@192.168.1.222
```

Then on the Pi:

```bash
# See exact service definition
systemctl cat thinkpad-server

# Confirm service health and boot behavior
systemctl status thinkpad-server --no-pager
systemctl is-enabled thinkpad-server

# Inspect deploy helper script
sudo ls -l /usr/local/bin/deploy-thinkpad-server.sh
sudo sed -n '1,220p' /usr/local/bin/deploy-thinkpad-server.sh

# Confirm local app health
curl -i http://127.0.0.1:3000/health
```

Reverse proxy discovery:

```bash
# If Caddy is used
systemctl status caddy --no-pager
sudo caddy validate --config /etc/caddy/Caddyfile
sudo sed -n '1,260p' /etc/caddy/Caddyfile

# If Nginx is used
systemctl status nginx --no-pager
sudo nginx -t
sudo ls -la /etc/nginx/sites-enabled
sudo sed -n '1,260p' /etc/nginx/sites-enabled/*
```

## Pattern to Reuse for a Second Project + Domain

Assume:
- New repo dir: `/home/tom/apps/PROJECT_B`
- New service: `project-b-server`
- New local port: `3001`
- New domain: `projectb.example.com`
- New deploy script: `/usr/local/bin/deploy-project-b.sh`

### 1) Create a systemd service for project B

Create `/etc/systemd/system/project-b-server.service`:

```ini
[Unit]
Description=Project B Node Server
After=network.target

[Service]
Type=simple
User=tom
WorkingDirectory=/home/tom/apps/PROJECT_B
Environment=PORT=3001
ExecStart=/usr/bin/node /home/tom/apps/PROJECT_B/server.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable project-b-server
sudo systemctl start project-b-server
systemctl status project-b-server --no-pager
curl -i http://127.0.0.1:3001/health
```

### 2) Create deploy script for project B

Create `/usr/local/bin/deploy-project-b.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/tom/apps/PROJECT_B"
SERVICE_NAME="project-b-server"
BRANCH="main"
HEALTH_URL="http://127.0.0.1:3001/health"

echo "==> Deploying ${SERVICE_NAME}"
cd "${APP_DIR}"

echo "==> Fetch + pull"
git fetch origin
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "==> Restart service"
sudo systemctl restart "${SERVICE_NAME}"

echo "==> Health check"
for i in {1..20}; do
  if curl -fsS "${HEALTH_URL}" > /dev/null; then
    echo "Deploy successful"
    exit 0
  fi
  sleep 1
done

echo "Health check failed" >&2
exit 1
```

Make it executable:

```bash
sudo chmod +x /usr/local/bin/deploy-project-b.sh
```

### 3) Add reverse proxy route for new domain

If Caddy:

```caddy
projectb.example.com {
    reverse_proxy 127.0.0.1:3001
}
```

Apply:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If Nginx (server block equivalent), proxy to `127.0.0.1:3001` and reload Nginx.

### 4) Add in-app deploy endpoint/UI for project B (optional)

If you want the same click-to-deploy UX:
- Add a `POST /admin/deploy` route in project B server code
- Route should execute `/usr/local/bin/deploy-project-b.sh`
- Add a private admin page button similar to `public/config.html`

Important: protect this endpoint (auth, allowlist, secret token, or private network only). The current project endpoint has no authentication in code.

## Recommended Hardening Before Reuse

1. Restrict `/admin/deploy` behind auth (or remove public access entirely)
2. In deploy scripts, use `git pull --ff-only` (already shown above)
3. Pin absolute paths for `node`, app directory, and service names
4. Keep each app on a unique localhost port
5. Keep each domain mapped to exactly one local port in proxy config

## Operational Commands (project B)

```bash
# Deploy now
ssh tom@192.168.1.222 '/usr/local/bin/deploy-project-b.sh'

# Service logs
ssh tom@192.168.1.222 'journalctl -u project-b-server -n 200 --no-pager'

# Service status
ssh tom@192.168.1.222 'systemctl status project-b-server --no-pager'

# External test
curl -i https://projectb.example.com/health
```

## Quick Migration Checklist

- [ ] Repo cloned to `/home/tom/apps/PROJECT_B`
- [ ] `project-b-server.service` created and enabled
- [ ] `deploy-project-b.sh` created and executable
- [ ] Reverse proxy domain route added (`projectb.example.com -> 127.0.0.1:3001`)
- [ ] `/health` returns 200 on localhost and external domain
- [ ] Optional `/admin/deploy` secured if enabled

