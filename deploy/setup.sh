#!/usr/bin/env bash
#
# Install or update the Blura News API on an Ubuntu server (24.04+, Python 3.11+)
# and run it as a systemd service that starts on boot and restarts on crash.
#
# First install and every later update are the same command, run on the server:
#
#   curl -fsSL -o setup.sh https://raw.githubusercontent.com/BCSS-Nandeep/News_api/international/deploy/setup.sh
#   sudo bash setup.sh
#
# Port 8000 taken by something else? Choose the port on first install with:
#   sudo env PORT=8010 bash setup.sh
#
# Settings live in /etc/blura-news-api.env (created on first run; later runs
# only migrate settings from older versions). After editing it:
#   sudo systemctl restart blura-news-api
#
# Overridable for testing or a different checkout:
#   REPO_URL, BRANCH, APP_DIR, APP_USER, PORT (PORT only applies on first install),
#   CPU_QUOTA (default 400% = at most 4 cores), MEMORY_MAX (default 6G)

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/BCSS-Nandeep/News_api.git}"
BRANCH="${BRANCH:-international}"
APP_DIR="${APP_DIR:-/opt/blura-engine}"
APP_USER="${APP_USER:-blura}"
PORT="${PORT:-8000}"
CPU_QUOTA="${CPU_QUOTA:-400%}"
MEMORY_MAX="${MEMORY_MAX:-6G}"
SERVICE=blura-news-api
ENV_FILE=/etc/${SERVICE}.env
UNIT_FILE=/etc/systemd/system/${SERVICE}.service

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo"

step "Checking system packages"
# Install only what is actually missing: on a shared server, an apt upgrade
# of python3 would also hit every other Python app running there.
need=()
command -v git >/dev/null 2>&1 || need+=(git)
command -v curl >/dev/null 2>&1 || need+=(curl ca-certificates)
if command -v python3 >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  python3 -m venv "$tmp/venv" >/dev/null 2>&1 || need+=(python3-venv)
  rm -rf "$tmp"
else
  need+=(python3 python3-venv)
fi
if [ ${#need[@]} -gt 0 ]; then
  echo "Installing: ${need[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "${need[@]}" >/dev/null
else
  echo "Everything needed is already installed"
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || die "Python 3.11+ is required, found $(python3 --version)"
echo "Using $(python3 --version)"

# Code and virtualenv are owned by root; the service runs as an unprivileged
# user that can read them but not modify them.
id -u "$APP_USER" >/dev/null 2>&1 \
  || useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"

if [ -d "$APP_DIR/.git" ]; then
  step "Updating code in $APP_DIR (branch $BRANCH)"
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  # The checkout is deploy-only; configuration lives in $ENV_FILE, so any
  # local edits here are discarded rather than allowed to block the update.
  git -C "$APP_DIR" checkout -q -f -B "$BRANCH" "origin/$BRANCH"
else
  step "Downloading code to $APP_DIR (branch $BRANCH)"
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
echo "At commit: $(git -C "$APP_DIR" log -1 --format='%h %s')"

step "Installing Python dependencies"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --no-cache-dir --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q --no-cache-dir -r "$APP_DIR/requirements.txt"

step "Running the test suite (offline)"
(cd "$APP_DIR" && .venv/bin/python -m pytest -q -p no:cacheprovider) | tail -1 \
  || die "tests failed - not restarting the service. Full output: cd $APP_DIR && sudo .venv/bin/python -m pytest"

if [ ! -f "$ENV_FILE" ]; then
  step "Creating settings file $ENV_FILE"
  cat > "$ENV_FILE" <<EOF
# Blura News API settings.
# After editing: sudo systemctl restart ${SERVICE}

# Port the API listens on.
PORT=${PORT}

# Websites allowed to call the API from a browser (comma-separated), e.g.
#   CORS_ORIGINS=https://soceye.example.com,http://localhost:3000
# '*' allows any website. Backend/server-side callers are not affected.
CORS_ORIGINS=*

# Seconds scraped articles stay cached before being re-scraped.
CACHE_TTL_SECONDS=1800
# Max seconds a request waits for scraping; slower sources finish in the
# background. Keep it under the gateway timeout in front of the API (BluGate: 30).
REQUEST_TIME_BUDGET_SECONDS=25
# Worker processes that scrape sources (each uses up to one CPU core; the
# service as a whole is capped at ${CPU_QUOTA} CPU).
SCRAPE_PROCESSES=4
# Sources each worker process scrapes at the same time.
SCRAPE_THREADS_PER_PROCESS=6
# Newest articles taken from each source.
MAX_ITEMS_PER_SOURCE=50
# Re-scrape every active source in the background this often (0 = off).
BACKGROUND_REFRESH_SECONDS=600
EOF
  chmod 644 "$ENV_FILE"
else
  echo "Keeping existing settings in $ENV_FILE"
  # Migrate settings written by older versions of this script. Values still at
  # an old default move to the new one; anything changed by hand is kept.
  # MAX_SOURCES_PER_REQUEST and DISCOVERY_MAX_WORKERS no longer exist (every
  # matching source is searched, by SCRAPE_PROCESSES x SCRAPE_THREADS_PER_PROCESS).
  sed -i '/^# Max news sources scraped for one request/d; /^MAX_SOURCES_PER_REQUEST=/d' "$ENV_FILE"
  sed -i '/^# Sources scraped in parallel\.$/d; /^DISCOVERY_MAX_WORKERS=/d' "$ENV_FILE"
  sed -i 's/^CACHE_TTL_SECONDS=600$/CACHE_TTL_SECONDS=1800/' "$ENV_FILE"
  add_setting() {  # name value comment
    grep -q "^$1=" "$ENV_FILE" || printf '%s\n%s=%s\n' "$3" "$1" "$2" >> "$ENV_FILE"
  }
  add_setting SCRAPE_PROCESSES 4 '# Worker processes that scrape sources (each uses up to one CPU core).'
  add_setting SCRAPE_THREADS_PER_PROCESS 6 '# Sources each worker process scrapes at the same time.'
  add_setting MAX_ITEMS_PER_SOURCE 50 '# Newest articles taken from each source.'
  add_setting BACKGROUND_REFRESH_SECONDS 600 '# Re-scrape every active source in the background this often (0 = off).'
fi
PORT="$(sed -n 's/^PORT=//p' "$ENV_FILE" | tail -1)"
PORT="${PORT:-8000}"

if ! systemctl is-active -q "$SERVICE" && command -v ss >/dev/null 2>&1 \
   && ss -ltn "( sport = :$PORT )" | grep -q LISTEN; then
  die "port $PORT is already used by another program. Pick a free one:
  sudo sed -i 's/^PORT=.*/PORT=8010/' $ENV_FILE && sudo bash setup.sh"
fi

step "Installing systemd service $SERVICE"
# One API process on purpose: the article cache is in-memory and per-process
# (see DEPLOYMENT.md section 6), so no --workers. Scraping runs in its own worker
# processes (SCRAPE_PROCESSES); CPUQuota/MemoryMax cap the service as a whole so
# it can never starve the other services on this server.
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Blura News API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${APP_DIR}/.venv/bin/python main.py
Restart=always
RestartSec=5
CPUQuota=${CPU_QUOTA}
MemoryMax=${MEMORY_MAX}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  step "Opening port $PORT in the ufw firewall"
  ufw allow "$PORT/tcp" >/dev/null
fi

step "Waiting for the API to answer on port $PORT"
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || {
  journalctl -u "$SERVICE" -n 30 --no-pager || true
  die "the service did not start - see the log above"
}
echo "Health check OK"

if ! curl -fsS -o /dev/null --max-time 15 https://www.thehindu.com; then
  printf '\nWARNING: this server could not reach https://www.thehindu.com.\n'
  printf 'The API needs outbound internet (HTTPS) to scrape news; without it\n'
  printf 'every /news/articles request returns 0 articles.\n'
fi

PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
cat <<EOF

============================================================
 Blura News API is running.

   Dashboard : http://${PUBLIC_IP}:${PORT}/
   API docs  : http://${PUBLIC_IP}:${PORT}/docs
   Health    : http://${PUBLIC_IP}:${PORT}/health

 If those don't open from your own computer, allow inbound
 TCP port ${PORT} in your cloud provider's firewall
 (AWS security group, Azure NSG, DigitalOcean firewall...).

 Status : sudo systemctl status ${SERVICE}
 Logs   : sudo journalctl -u ${SERVICE} -f
 Update : run this same setup command again
============================================================
EOF
