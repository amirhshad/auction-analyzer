#!/usr/bin/env bash
# Quick update script — run on VPS after pushing changes
set -euo pipefail

APP_DIR="/opt/auction-analyzer"
cd "$APP_DIR"

echo "Pulling latest changes..."
git pull

echo "Installing dependencies..."
.venv/bin/pip install -r requirements.txt -q

echo "Restarting service..."
sudo systemctl restart auction-analyzer

echo "Done! Check status: sudo systemctl status auction-analyzer"
