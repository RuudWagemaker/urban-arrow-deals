#!/usr/bin/env bash
# One-time (and re-runnable) setup of the deal tracker on the Hetzner VPS.
# Run as root:   bash /opt/deal-tracker/app/deploy/install.sh
#
# Assumes: user `tracker`, /opt/deal-tracker/{app,venv,data} exist (see deploy/README.md),
# and the curve-royale Caddy container `deploy-caddy-1` imports /etc/caddy/sites/*.caddy.
set -euo pipefail

BASE=/opt/deal-tracker
APP=$BASE/app
ENVFILE=/etc/deal-tracker/env
SITES=/etc/caddy-sites
HOSTNAME_SITE=deals.128-140-101-183.sslip.io
CADDY=deploy-caddy-1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
as_tracker() { sudo -u tracker -H "$@"; }

[ "$(id -u)" = 0 ] || { echo "Run this as root."; exit 1; }
id tracker >/dev/null || { echo "User 'tracker' is missing (see deploy/README.md)."; exit 1; }

say "1/7 Updating code and Python packages"
as_tracker git -C "$APP" pull --ff-only -q
as_tracker "$BASE/venv/bin/pip" install -q -r "$APP/requirements.txt"
echo "code at $(git -C "$APP" log --oneline -1)"

say "2/7 Secrets file $ENVFILE"
mkdir -p /etc/deal-tracker
if [ ! -f "$ENVFILE" ]; then
  echo "The tracker needs a Claude token to read ads (it uses your Claude Pro plan)."
  echo "If you don't have one yet: open a SECOND SSH window and run:  claude setup-token"
  echo "(log in when asked; it prints a long token). Leave empty to skip for now."
  read -r -s -p "Paste the Claude token (input is hidden): " TOKEN; echo
  read -r -p "ntfy channel name for phone alerts (the one you subscribed to in the app): " NTFY
  umask 027
  {
    echo "CLAUDE_CODE_OAUTH_TOKEN=$TOKEN"
    echo "NTFY_TOPIC=$NTFY"
    echo "SITE_URL=https://$HOSTNAME_SITE/"
  } > "$ENVFILE"
  umask 022
else
  echo "already exists, keeping it (edit with: nano $ENVFILE)"
fi
chown root:tracker /etc/deal-tracker "$ENVFILE"
chmod 750 /etc/deal-tracker; chmod 640 "$ENVFILE"

say "3/7 Website password and Caddy site"
mkdir -p "$SITES"
PWFILE=/root/deal-tracker-site-password.txt
if [ ! -f "$SITES/deals.caddy" ]; then
  PASSWORD=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
  HASH=$(docker exec "$CADDY" caddy hash-password --plaintext "$PASSWORD")
  cat > "$SITES/deals.caddy" <<EOF
$HOSTNAME_SITE {
	encode gzip
	basic_auth {
		ruud $HASH
	}
	reverse_proxy 127.0.0.1:8090
}
EOF
  umask 077; printf 'user: ruud\npassword: %s\n' "$PASSWORD" > "$PWFILE"; umask 022
  echo "created $SITES/deals.caddy (password saved in $PWFILE)"
else
  echo "already exists, keeping it"
fi
chmod 600 "$SITES/deals.caddy"   # holds the password hash; Caddy runs as root and can still read it

say "4/7 Installing services"
cp "$APP/deploy/deal-tracker-scan.service" "$APP/deploy/deal-tracker-scan.timer" \
   "$APP/deploy/deal-tracker-web.service" /etc/systemd/system/
systemctl daemon-reload
mkdir -p "$BASE/site"; chown tracker:tracker "$BASE/site"

say "5/7 Test scan (no alerts) - takes a few minutes the first time"
set -a; . "$ENVFILE"; set +a
cd "$APP"
as_tracker env TZ=Europe/Amsterdam UA_DATA_DIR=$BASE/data UA_SITE_DIR=$BASE/site \
  PATH=$BASE/.local/bin:/usr/local/bin:/usr/bin:/bin HOME=$BASE \
  CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" \
  "$BASE/venv/bin/python" -m uatracker run --no-alerts 2>&1 | { grep -E "listings|merge|Claude|pricing|site written|ERROR|WARNING" || true; } | tail -20

say "6/7 Starting the website and the 20-minute timer"
systemctl enable --now deal-tracker-web.service
systemctl enable --now deal-tracker-scan.timer
sleep 1
curl -s -o /dev/null -w "local site: HTTP %{http_code}\n" http://127.0.0.1:8090/
if docker exec "$CADDY" test -d /etc/caddy/sites 2>/dev/null; then
  docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile >/dev/null && echo "Caddy reloaded"
else
  echo "Caddy doesn't see $SITES yet: the snake repo update will add that within ~5 minutes."
fi

say "7/7 Done"
systemctl list-timers deal-tracker-scan.timer --no-pager | head -3
echo
echo "Website:  https://$HOSTNAME_SITE/"
[ -f "$PWFILE" ] && cat "$PWFILE"
echo
echo "Scan log: journalctl -u deal-tracker-scan -n 50 --no-pager"
