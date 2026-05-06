# Raspberry Pi Deploy Reference (example)

Copy to `docs/pi-deploy-reference.md` (ignored by git) and replace placeholders with your real host, paths, and service name.

## Host and Paths

- Remote host: `USER@PI_HOST` (e.g. `tom@192.168.1.222`)
- Remote app root: `/home/USER/apps/APP_NAME`
- Remote static path: `/home/USER/apps/APP_NAME/backend/static/`
- Local static path: `backend/static/`
- Local Pi backup path: `~/local-pi-dup/` (or a path under this repo if you prefer)
- Service name: `SERVICE_NAME` (e.g. `fivemore`)

## Common Operations

### 1) Deploy static assets only

```bash
rsync -av --delete backend/static/ USER@PI_HOST:/home/USER/apps/APP_NAME/backend/static/
```

### 2) Pull latest code on remote

```bash
ssh USER@PI_HOST "cd /home/USER/apps/APP_NAME && git pull"
```

### 3) Restart service

```bash
ssh USER@PI_HOST "sudo systemctl restart SERVICE_NAME"
```

## Recommended routine

1. Sync static assets (rsync as above).
2. Pull on the Pi (`git pull` in app root).
3. Restart the systemd unit.

## Full home directory sync (backup / clone)

Push local backup to remote home:

```bash
rsync -avz ~/local-pi-dup/ USER@PI_HOST:/home/USER/
```

Pull remote home to local backup:

```bash
rsync -avz USER@PI_HOST:/home/USER/ ~/local-pi-dup
```

Optional SSH config alias:

```bash
ssh YOUR_ALIAS
```

## Quick verification

```bash
ssh USER@PI_HOST "systemctl status SERVICE_NAME --no-pager"
ssh USER@PI_HOST "journalctl -u SERVICE_NAME -n 100 --no-pager"
```

## Notes

- `rsync --delete` removes remote files not present locally; use with care.
- Prefer SSH keys over passwords.
- Do not commit real IPs, usernames, or paths if the repo is public.
