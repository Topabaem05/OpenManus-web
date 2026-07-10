#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-topabaem@100.99.113.107}"
REMOTE_PORT="${REMOTE_PORT:-22}"
SSH_PASS="${SSH_PASS:-1234}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

echo "=== OpenManus-web Podman Deployment ==="
echo "Target: ${REMOTE_HOST}"

SSH_CMD="sshpass -p '${SSH_PASS}' ssh ${SSH_OPTS} -p ${REMOTE_PORT} ${REMOTE_HOST}"

echo "=== Step 1: Checking remote connectivity ==="
eval "${SSH_CMD} 'echo OK; uname -a; cat /etc/os-release 2>/dev/null | head -3'" || {
  echo "ERROR: Cannot connect to ${REMOTE_HOST}"
  exit 1
}

echo "=== Step 2: Install podman + git on remote ==="
eval "${SSH_CMD}" 'bash -s' << 'REMOTE_SCRIPT'
set -euo pipefail
echo "Checking podman..."
if command -v podman &>/dev/null; then
  echo "Podman already installed: $(podman --version)"
else
  echo "Installing podman..."
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq podman git curl
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y podman git curl
  elif command -v yum &>/dev/null; then
    sudo yum install -y podman git curl
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm podman git curl
  else
    echo "ERROR: Cannot detect package manager"
    exit 1
  fi
  echo "Podman: $(podman --version)"
fi

if ! command -v git &>/dev/null; then
  sudo apt-get install -y -qq git 2>/dev/null || true
fi
echo "Git: $(git --version)"

echo "=== Step 3: Clone/update repo ==="
if [ -d /opt/OpenManus-web ]; then
  cd /opt/OpenManus-web && git pull origin main 2>/dev/null || true
else
  git clone https://github.com/Topabaem05/OpenManus-web /opt/OpenManus-web
fi
cd /opt/OpenManus-web
echo "Repo at $(git rev-parse --short HEAD)"

echo "=== Step 4: Create config.toml if missing ==="
if [ ! -f config/config.toml ]; then
  cp config/config.example.toml config/config.toml
  echo "Created config.toml from example - EDIT IT with your API key"
fi

echo "=== Step 5: Build container image ==="
podman build -t openmanus-web:latest -f Containerfile.web . 2>&1 | tail -20

echo "=== Step 6: Stop old container ==="
podman stop openmanus-web 2>/dev/null || true
podman rm openmanus-web 2>/dev/null || true

echo "=== Step 7: Run container ==="
podman run -d \
  --name openmanus-web \
  -p 9000:9000 \
  -v /opt/OpenManus-web/config:/app/OpenManus/config:Z \
  -v /opt/OpenManus-web/workspace:/app/OpenManus/workspace:Z \
  --restart unless-stopped \
  openmanus-web:latest

echo "=== Step 8: Verify ==="
sleep 8
podman ps
echo "--- Health check ---"
curl -sf http://localhost:9000/api/sessions | head -c 300 || echo "API not responding yet"
echo ""
echo "=== Deployment complete ==="
echo "Service URL: http://100.99.113.107:9000"
REMOTE_SCRIPT

echo "=== Done ==="
