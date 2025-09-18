#!/usr/bin/env bash
set -euo pipefail

# One-shot setup for running Uvicorn under systemd and exposing it via Nginx + Let's Encrypt.
#
# Usage (run as root):
#   DOMAIN=langchain.example.com EMAIL=admin@example.com \
#   APP_DIR=/root/backend-langchain VENV_BIN=/root/backend-langchain/.venv/bin \
#   bash docs/deploy/setup_https.sh
#
# Optional variables (defaults shown):
#   APP_HOST=127.0.0.1
#   APP_PORT=8001
#   WORKERS=2
#   PRISMA_CMD_TIMEOUT=15

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

: "${DOMAIN:?Set DOMAIN (e.g., langchain.example.com)}"
: "${EMAIL:?Set EMAIL for Let's Encrypt registration}"

APP_DIR=${APP_DIR:-/root/backend-langchain}
VENV_BIN=${VENV_BIN:-$APP_DIR/.venv/bin}
APP_HOST=${APP_HOST:-127.0.0.1}
APP_PORT=${APP_PORT:-8001}
WORKERS=${WORKERS:-2}
PRISMA_CMD_TIMEOUT=${PRISMA_CMD_TIMEOUT:-15}

echo "==> Installing Nginx and Certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx

echo "==> Creating systemd service: /etc/systemd/system/langchain.service"
cat > /etc/systemd/system/langchain.service <<SERVICE
[Unit]
Description=LangChain Backend (Uvicorn)
After=network.target

[Service]
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_BIN
Environment=PRISMA_CMD_TIMEOUT=$PRISMA_CMD_TIMEOUT
ExecStart=$VENV_BIN/uvicorn main:app --host $APP_HOST --port $APP_PORT --workers $WORKERS --proxy-headers --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now langchain
sleep 1 || true
systemctl status langchain --no-pager || true

echo "==> Writing initial Nginx site for $DOMAIN (HTTP only; Certbot will upgrade to HTTPS)"
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
cat > /etc/nginx/sites-available/langchain.conf <<NGINX
server {
  listen 80;
  listen [::]:80;
  server_name $DOMAIN;

  client_max_body_size 16m;

  location / {
    proxy_pass http://$APP_HOST:$APP_PORT;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
  }
}
NGINX

ln -sf /etc/nginx/sites-available/langchain.conf /etc/nginx/sites-enabled/langchain.conf
nginx -t
systemctl reload nginx

echo "==> Requesting TLS with Let's Encrypt for $DOMAIN"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || {
  echo "Certbot failed. Check DNS (A record) and port 80 reachability, then rerun: certbot --nginx -d $DOMAIN" >&2
  exit 1
}

echo "==> Verifying HTTPS"
set +e
curl -sSfI "https://$DOMAIN/" || true
set -e

echo "==> Done. Logs:"
echo "  - App:    journalctl -u langchain -f"
echo "  - Nginx:  tail -f /var/log/nginx/access.log /var/log/nginx/error.log"
echo "==> Renew test: certbot renew --dry-run"

