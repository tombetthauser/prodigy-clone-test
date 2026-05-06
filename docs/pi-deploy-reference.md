# Raspberry Pi Deploy Reference

This document consolidates current manual deploy and sync commands for the `fivemore` app.

## Host and Paths

- Remote host: `tom@192.168.1.222`
- Remote app root: `/home/tom/apps/fivemore`
- Remote static path: `/home/tom/apps/fivemore/backend/static/`
- Local static path: `backend/static/`
- Local Pi backup path: `~/local-pi-dup/`
- Service name: `fivemore`

## Common Operations

### 1) Deploy static assets only

```bash
rsync -av --delete backend/static/ tom@192.168.1.222:/home/tom/apps/fivemore/backend/static/
```

### 2) Pull latest code on remote

```bash
ssh tom@192.168.1.222 "cd /home/tom/apps/fivemore && git pull"
```

### 3) Restart service

```bash
ssh tom@192.168.1.222 "sudo systemctl restart fivemore"
```

## Recommended Routine

Use this order for a manual deploy:

1. Sync static assets:
   ```bash
   rsync -av --delete backend/static/ tom@192.168.1.222:/home/tom/apps/fivemore/backend/static/
   ```
2. Pull latest backend code:
   ```bash
   ssh tom@192.168.1.222 "cd /home/tom/apps/fivemore && git pull"
   ```
3. Restart service:
   ```bash
   ssh tom@192.168.1.222 "sudo systemctl restart fivemore"
   ```

## Full Home Directory Sync (Backup/Clone)

### Push local backup to remote home

```bash
rsync -avz ~/local-pi-dup/ tom@192.168.1.222:/home/tom/
```

### Pull remote home to local backup

```bash
rsync -avz tom@192.168.1.222:/home/tom/ ~/local-pi-dup
```

### SSH shortcut usage

```bash
ssh piserver
```

Use this only if `piserver` is configured in your SSH config.

## Quick Verification

After restart, verify service status/logs:

```bash
ssh tom@192.168.1.222 "systemctl status fivemore --no-pager"
ssh tom@192.168.1.222 "journalctl -u fivemore -n 100 --no-pager"
```

## Notes

- `--delete` in `rsync` removes remote files not present locally. Use with care.
- Prefer key-based SSH auth over passwords.
- Avoid committing backup/sync directories (see `.gitignore`).
