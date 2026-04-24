#!/usr/bin/env bash
# Auction Analyzer — Hetzner VPS deployment script
# Run this on your VPS: bash deploy/setup.sh
set -euo pipefail

APP_DIR="/opt/auction-analyzer"
APP_USER="auction"
PYTHON_VERSION="3.12"

echo "=== Auction Analyzer VPS Setup ==="

# 1. System dependencies
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev \
    nginx certbot git curl

# 2. Create app user
echo "[2/7] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home "$APP_DIR" --shell /bin/bash "$APP_USER"
fi

# 3. Clone or update repo
echo "[3/7] Setting up application directory..."
if [ -d "$APP_DIR/.git" ]; then
    echo "  Repo exists, pulling latest..."
    cd "$APP_DIR"
    git pull
else
    echo "  Enter your git repo URL (or press Enter to copy files manually later):"
    read -r REPO_URL
    if [ -n "$REPO_URL" ]; then
        git clone "$REPO_URL" "$APP_DIR"
    else
        mkdir -p "$APP_DIR"
        echo "  Copy your project files to $APP_DIR manually."
    fi
fi

cd "$APP_DIR"

# 4. Python virtual environment
echo "[4/7] Setting up Python environment..."
python${PYTHON_VERSION} -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

# Install Playwright browsers (needed for scraping)
echo "  Installing Playwright browsers..."
.venv/bin/playwright install chromium
.venv/bin/playwright install-deps chromium

# 5. Environment file
echo "[5/7] Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || cat > .env << 'ENVEOF'
DATABASE_URL=sqlite:////opt/auction-analyzer/data/auction_analyzer.db
SCRAPING_DELAY_SECONDS=2.5
OPENAI_API_KEY=
ENVEOF
    echo "  Created .env — edit it with your API keys: nano $APP_DIR/.env"
fi

# Create data directory
mkdir -p data .tmp
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# 6. Systemd service
echo "[6/7] Installing systemd service..."
cat > /etc/systemd/system/auction-analyzer.service << SERVICEEOF
[Unit]
Description=Auction Analyzer Web Dashboard
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/.venv/bin:/usr/bin:/bin
ExecStart=$APP_DIR/.venv/bin/uvicorn execution.webapp.app:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable auction-analyzer
systemctl restart auction-analyzer

# 7. Nginx reverse proxy
echo "[7/7] Configuring nginx..."
cat > /etc/nginx/sites-available/auction-analyzer << 'NGINXEOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for SSE (Server-Sent Events)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/auction-analyzer /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=== Setup Complete ==="
echo ""
echo "  App running at:  http://$(curl -s ifconfig.me)"
echo "  App directory:   $APP_DIR"
echo "  Logs:            journalctl -u auction-analyzer -f"
echo "  Restart:         systemctl restart auction-analyzer"
echo "  Edit config:     nano $APP_DIR/.env"
echo ""
echo "  To update later: cd $APP_DIR && git pull && systemctl restart auction-analyzer"
