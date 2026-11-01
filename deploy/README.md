# Running the tracker on a server (Hetzner VPS)

Layout on the server:

| Path | What |
|---|---|
| `/opt/deal-tracker/app` | this repository (pulled from GitHub before every scan) |
| `/opt/deal-tracker/venv` | Python packages |
| `/opt/deal-tracker/data` | collected ads, per product (outside git, so updates never clash) |
| `/opt/deal-tracker/site` | the built website |
| `/etc/deal-tracker/env` | secrets: Claude token, ntfy channel (see `env.example`) |
| `/etc/caddy-sites/deals.caddy` | Caddy site with password (see `deals.caddy.example`) |

Everything runs as the system user `tracker`.

Services (copy the files from this folder to `/etc/systemd/system/`):

- `deal-tracker-scan.timer` starts `deal-tracker-scan.service` every 20 minutes, 07:00–23:40.
- `deal-tracker-web.service` serves the site on 127.0.0.1:8090; the existing Caddy
  (curve-royale) forwards `https://deals.128-140-101-183.sslip.io` to it, with a password.

Useful commands:

```bash
systemctl list-timers deal-tracker-scan.timer      # when is the next scan
journalctl -u deal-tracker-scan -n 50 --no-pager   # log of the last scan
systemctl start deal-tracker-scan                  # scan now
```
