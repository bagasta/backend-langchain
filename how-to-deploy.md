**Deploy With systemd + Nginx (HTTPS)**

This guide sets up Uvicorn as a managed service that starts on boot and restarts on failure, and exposes it via Nginx with a free Let's Encrypt certificate for `langchain.chiefaiofficer.id`.

**Prerequisites**
- Linux host with sudo/root
- DNS A record: `langchain.chiefaiofficer.id` → your server IP
- Python venv created and dependencies installed in the repo
- Node 18+ and npm (needed for Prisma helper)
- Ports 80 and 443 opened in firewall

**1) Prepare the app**
- Repo dir: `/root/backend-langchain` (adjust paths as needed)
- Venv: `/root/backend-langchain/.venv`
- Ensure `.env` exists at repo root with your DB and API settings; the app loads it automatically (see `main.py`).
- Install Node.js (if needed):
  - Quick: `sudo apt-get update && sudo apt-get install -y nodejs npm`
  - Recommended (Node 18 LTS):
    - `curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -`
    - `sudo apt-get install -y nodejs`
  - Verify: `node -v && npm -v`
- Install Prisma deps and apply migrations (one-time):
  - `cd /root/backend-langchain/database/prisma && npm ci --no-audit --no-fund`
  - `cd /root/backend-langchain`
  - `npx prisma migrate status --schema database/prisma/schema.prisma`
  - If you see a legacy failed migration (P3009), resolve and deploy:
    - `npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250804093746_init`
    - `npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250901000000_add_agent_fields`
    - `npx prisma migrate resolve --schema database/prisma/schema.prisma --rolled-back 20250901000001_add_memory_backend`
  - `npx prisma migrate deploy --schema database/prisma/schema.prisma`
  - `npx prisma generate --schema database/prisma/schema.prisma`

**2) Create a systemd service**
- File: `/etc/systemd/system/langchain.service`
- Contents (adjust paths/port; uses port 8001):
  [Unit]
  Description=LangChain Backend (Uvicorn)
  After=network.target

  [Service]
  WorkingDirectory=/root/backend-langchain
  # Ensure Node is visible for Prisma helper
  Environment=PATH=/root/backend-langchain/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  Environment=PRISMA_CMD_TIMEOUT=15
  # Optional explicit Node binary if PATH is constrained
  # Environment=NODE_BIN=/usr/bin/node
  # Environment=NPX_BIN=/usr/bin/npx
  ExecStart=/root/backend-langchain/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 2 --proxy-headers --log-level info
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target

- Enable and start:
  - `sudo systemctl daemon-reload`
  - `sudo systemctl enable --now langchain`
  - `sudo systemctl status langchain --no-pager`
  - `sudo journalctl -u langchain -f`

**3) Install and configure Nginx**
- Install:
  - `sudo apt-get update && sudo apt-get install -y nginx`
- Site file: `/etc/nginx/sites-available/langchain.conf`
- Contents (proxy directly to 127.0.0.1:8001):
  server {
    listen 80;
    listen [::]:80;
    server_name langchain.chiefaiofficer.id;
    return 301 https://$host$request_uri;
  }

  server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name langchain.chiefaiofficer.id;

    # Certbot will manage these paths after issuance
    # ssl_certificate     /etc/letsencrypt/live/langchain.chiefaiofficer.id/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/langchain.chiefaiofficer.id/privkey.pem;

    client_max_body_size 16m;

    location / {
      proxy_pass http://127.0.0.1:8001;
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

- Enable and reload:
  - `sudo ln -sf /etc/nginx/sites-available/langchain.conf /etc/nginx/sites-enabled/langchain.conf`
  - `sudo nginx -t`
  - `sudo systemctl reload nginx`

**4) Issue HTTPS certificate (Let's Encrypt)**
- Install Certbot plugin:
  - `sudo apt-get install -y certbot python3-certbot-nginx`
- Obtain and configure HTTPS:
  - `sudo certbot --nginx -d langchain.chiefaiofficer.id`
- Verify:
  - `curl -I https://langchain.chiefaiofficer.id/`
- Renewal test:
  - `sudo certbot renew --dry-run`

**5) Firewall and DNS**
- Open required ports:
  - `sudo ufw allow 80,443/tcp` (if UFW is enabled)
- Confirm DNS A record resolves to your server IP.

**6) Validate and troubleshoot**
- App:
  - `sudo systemctl status langchain --no-pager`
  - `sudo journalctl -u langchain -f`
  - `curl -I http://127.0.0.1:8001/`
  - `curl -I http://127.0.0.1:8001/healthz`
- Nginx:
  - `sudo nginx -t`
  - `sudo systemctl reload nginx`
  - `sudo tail -f /var/log/nginx/error.log`
- If you get 500s on `/api_keys/generate`, check DB/migrations and Node PATH:
  - Ensure Node is installed and visible to the service: `which node` and set `Environment=NODE_BIN=/usr/bin/node` in the unit if needed.
  - `npx prisma migrate status --schema database/prisma/schema.prisma`
  - `node database/prisma/agent_service.js ensure_user <<< '{"email":"admin@example.com"}'`
  - `node database/prisma/agent_service.js apikey_create <<< '{"user_id":"<ID>","label":"server","ttl_days":365}'`

**7) Updating the app**
- Pull and restart:
  - `cd /root/backend-langchain && git pull`
  - `sudo systemctl restart langchain`
- If dependencies changed:
  - `source .venv/bin/activate && pip install -r requirements.txt`
  - `cd database/prisma && npm ci --no-audit --no-fund && cd ../..`
  - `npx prisma migrate deploy --schema database/prisma/schema.prisma`

**Notes**
- Keep `.env` at the repo root; `load_dotenv()` reads it at startup.
- Default port in this guide is 8001. Keep Nginx and the service in sync if you change it.
- Health endpoints: `/` and `/healthz` support GET/HEAD (200 OK).
- Remove duplicate Nginx `server_name` entries to avoid conflicts.
