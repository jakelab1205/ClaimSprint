#!/usr/bin/env bash
# One-time setup script for ClaimSprint on a raw Ubuntu EC2 instance.
# Run as the ubuntu user: bash deploy/setup.sh
set -euo pipefail

REPO_DIR=/srv/claimsprint
DOMAIN=team-solo-1-workshop.i2go.io

# -- API key ----------------------------------------------------------------
read -rsp "Enter ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
echo
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "Error: API key cannot be empty." >&2
    exit 1
fi

# -- System packages --------------------------------------------------------
sudo apt-get update -qq
sudo apt-get install -y python3.12 python3.12-venv nginx certbot python3-certbot-nginx

# -- App directory ----------------------------------------------------------
# If this script is run from inside the repo (e.g. after scp), move the repo
# to /srv/claimsprint. Otherwise clone it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SRC="$(dirname "$SCRIPT_DIR")"

if [[ "$APP_SRC" != "$REPO_DIR" ]]; then
    sudo mkdir -p "$REPO_DIR"
    sudo chown ubuntu:ubuntu "$REPO_DIR"
    cp -r "$APP_SRC/." "$REPO_DIR/"
fi

# -- Environment file -------------------------------------------------------
echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" > "$REPO_DIR/.env"
chmod 600 "$REPO_DIR/.env"

# -- Python virtualenv ------------------------------------------------------
python3.12 -m venv "$REPO_DIR/venv"
"$REPO_DIR/venv/bin/pip" install --upgrade pip -q
"$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt" -q

# -- Static files -----------------------------------------------------------
"$REPO_DIR/venv/bin/python" "$REPO_DIR/manage.py" collectstatic --noinput -v 0

# -- systemd service --------------------------------------------------------
sudo cp "$REPO_DIR/deploy/claimsprint.service" /etc/systemd/system/claimsprint.service
sudo systemctl daemon-reload
sudo systemctl enable claimsprint
sudo systemctl start claimsprint

# -- Nginx ------------------------------------------------------------------
sudo cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/sites-available/claimsprint
sudo ln -sf /etc/nginx/sites-available/claimsprint /etc/nginx/sites-enabled/claimsprint
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# -- TLS (Let's Encrypt) ----------------------------------------------------
sudo certbot --nginx -d "$DOMAIN" \
    --non-interactive --agree-tos \
    -m admin@i2go.io \
    --redirect

echo ""
echo "Setup complete. Visit https://$DOMAIN"
