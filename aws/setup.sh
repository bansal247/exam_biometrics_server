#!/bin/bash
# setup.sh — One-time EC2 instance setup.
#
# Run this ONCE after first launching the EC2 instance.
# Assumes the full project has already been uploaded/cloned to ~/exam_biometrics_full/
#
# Usage (from EC2 home dir):
#   bash ~/exam_biometrics_full/exam_biometrics_server/aws/setup.sh <EFS_DNS_NAME>
#
# Example:
#   bash ~/exam_biometrics_full/exam_biometrics_server/aws/setup.sh \
#       fs-0abc1234.efs.ap-south-1.amazonaws.com

set -euo pipefail

EFS_DNS="${1:?Error: EFS DNS name required. Usage: setup.sh <EFS_DNS_NAME>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"   # exam_biometrics_full/
APP_DIR="$PROJECT_ROOT/exam_biometrics_server"
IRIS_SRC="$PROJECT_ROOT/iris_server"
IRIS_DEST="/opt/iris_server"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $*"; }
waiting() { echo -e "${YELLOW}[…]${NC} $*"; }
die()     { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo "════════════════════════════════════════════"
echo "  Exam Biometrics — EC2 First-Time Setup"
echo "════════════════════════════════════════════"
echo "  Project:  $PROJECT_ROOT"
echo "  EFS DNS:  $EFS_DNS"
echo "════════════════════════════════════════════"
echo ""

# ── 1. System packages ───────────────────────────────────────────────────
waiting "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    ca-certificates curl gnupg lsb-release \
    nfs-common \
    openjdk-21-jre-headless \
    nginx \
    rsync
info "System packages installed"

# ── 2. Docker ────────────────────────────────────────────────────────────
waiting "Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker ubuntu
    info "Docker installed — NOTE: log out and back in (or run 'newgrp docker') for group to take effect"
else
    info "Docker already installed ($(docker --version))"
fi

# ── 3. Mount EFS ─────────────────────────────────────────────────────────
waiting "Mounting EFS at /mnt/efs..."
sudo mkdir -p /mnt/efs/biometrics

if ! grep -q "$EFS_DNS" /etc/fstab 2>/dev/null; then
    echo "$EFS_DNS:/ /mnt/efs efs defaults,_netdev,tls 0 0" | sudo tee -a /etc/fstab > /dev/null
fi

sudo mount -a 2>/dev/null || true

if mountpoint -q /mnt/efs; then
    sudo chown -R ubuntu:ubuntu /mnt/efs
    info "EFS mounted and writable at /mnt/efs"
else
    die "EFS mount failed. Check security group (port 2049 NFS from EC2) and EFS DNS name."
fi

# ── 4. Install iris server ───────────────────────────────────────────────
waiting "Installing iris server to $IRIS_DEST..."
[ -d "$IRIS_SRC" ] || die "iris_server source not found at $IRIS_SRC"

sudo mkdir -p "$IRIS_DEST"
sudo rsync -a "$IRIS_SRC/" "$IRIS_DEST/"

# Compile if not already compiled
if [ ! -f "$IRIS_DEST/out/IrisApiServer.class" ]; then
    waiting "Compiling IrisApiServer.java..."
    mkdir -p "$IRIS_DEST/out"
    javac -cp "$IRIS_DEST/Bin/Java/*" -d "$IRIS_DEST/out" "$IRIS_DEST/IrisApiServer.java" \
        || die "Compile failed — check Java classpath and Neurotec SDK jars in $IRIS_DEST/Bin/Java/"
    info "IrisApiServer compiled"
else
    info "IrisApiServer already compiled"
fi

# Install systemd service template
sudo cp "$IRIS_DEST/systemd/iris-api@.service" /etc/systemd/system/
sudo systemctl daemon-reload
info "Iris server installed at $IRIS_DEST"

# ── 5. Host nginx — iris load-balancer (port 8080) ───────────────────────
waiting "Configuring host nginx for iris server..."

# Server block for port 8080. The upstream block (iris_backends) is generated
# by iris-setup.sh into /etc/nginx/conf.d/iris_upstream.conf, which nginx
# includes automatically via the default nginx.conf conf.d glob.
sudo tee /etc/nginx/conf.d/iris-server.conf > /dev/null << 'NGINX_EOF'
server {
    listen 8080;

    # Allow Docker containers (host-gateway) and loopback only.
    # Docker bridge IPs are in 172.16.0.0/12; 10.0.0.0/8 covers VPC-internal.
    allow 127.0.0.0/8;
    allow 172.16.0.0/12;
    allow 10.0.0.0/8;
    deny  all;

    client_max_body_size 10M;
    proxy_read_timeout    60s;
    proxy_connect_timeout  5s;
    proxy_send_timeout    10s;

    location / {
        proxy_pass       http://iris_backends;
        proxy_set_header Host      $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX_EOF

info "Host nginx iris config written"

# ── 6. iris-setup.sh — spawn one instance per CPU ────────────────────────
waiting "Running iris-setup.sh ($(nproc) CPU(s) detected)..."
sudo bash "$IRIS_DEST/scripts/iris-setup.sh"
info "Iris server instances configured"

# ── 7. Docker image pre-build ────────────────────────────────────────────
waiting "Pre-building Docker images (takes a few minutes on first run)..."
cd "$APP_DIR"

[ -f ".env.aws" ] || {
    echo ""
    echo "  NOTE: .env.aws not found — skipping image build."
    echo "  Copy .env.aws.example → .env.aws, fill in values, then run:"
    echo "    cd $APP_DIR && docker compose --env-file .env.aws -f docker-compose.aws.yml build"
    echo ""
}

if [ -f ".env.aws" ]; then
    docker compose --env-file .env.aws -f docker-compose.aws.yml build
    info "Docker images built"
fi

# ── Done ─────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
info "Setup complete!"
echo ""
echo "Next steps:"
if [ ! -f "$APP_DIR/.env.aws" ]; then
echo "  1. Fill in secrets:"
echo "       cp $APP_DIR/.env.aws.example $APP_DIR/.env.aws"
echo "       nano $APP_DIR/.env.aws"
echo "  2. Build Docker images:"
echo "       cd $APP_DIR && docker compose --env-file .env.aws -f docker-compose.aws.yml build"
fi
echo "  3. From your laptop, run exam-start.sh to bring everything up"
echo "  4. Verify Neurotec iris license is activated:"
echo "       systemctl status 'iris-api@*'"
echo "════════════════════════════════════════════"
