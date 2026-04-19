#!/usr/bin/env bash
# Deploy observability agents to localai01 and localai02.
# Run from the root of the observability-stack repo.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Auto-detect this host's IP reachable from remote hosts, or use LOKI_HOST env
if [[ -z "${LOKI_HOST:-}" ]]; then
  LOKI_HOST=$(hostname -I | awk '{print $1}')
fi

deploy_host() {
  local host="$1"
  local agent_dir="$2"
  local remote_dir="/opt/observability-agent"

  echo ""
  echo "══════════════════════════════════════════"
  echo "  Deploying agents to ${host}"
  echo "══════════════════════════════════════════"

  ssh "ckennedy@${host}" "sudo mkdir -p ${remote_dir} && sudo chown ckennedy:ckennedy ${remote_dir}"
  rsync -av --delete \
    "${REPO_ROOT}/agents/${agent_dir}/" \
    "ckennedy@${host}:${remote_dir}/"

  ssh "ckennedy@${host}" bash <<EOF
    set -euo pipefail
    cd ${remote_dir}
    export LOKI_HOST=${LOKI_HOST}

    # Add ckennedy to docker group if not already (needs re-login to take effect)
    if ! groups ckennedy | grep -q docker; then
      sudo usermod -aG docker ckennedy
      echo "  → Added ckennedy to docker group (re-login required for full effect)"
    fi

    echo "  → Pulling latest images..."
    sudo docker compose pull

    echo "  → Starting agent stack..."
    sudo docker compose up -d --remove-orphans

    echo "  → Status:"
    sudo docker compose ps
EOF
  echo "  ✓ ${host} done"
}

install_amd_gpu_exporter() {
  local host="172.31.25.75"
  local remote_dir="/opt/observability-agent"
  echo ""
  echo "══════════════════════════════════════════"
  echo "  Installing AMD GPU exporter on ${host}"
  echo "══════════════════════════════════════════"
  ssh "ckennedy@${host}" bash <<EOF
    sudo systemctl stop amd-gpu-exporter 2>/dev/null || true
    sudo cp ${remote_dir}/amd-exporter/amd-gpu-exporter.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now amd-gpu-exporter
    sleep 2
    curl -sf http://localhost:2021/metrics | head -5
    echo "  ✓ AMD GPU exporter running"
EOF
}

# ── Deploy ────────────────────────────────────────────────────────────────────
deploy_host "172.31.25.75" "localai01"
deploy_host "10.25.25.67"  "localai02"
install_amd_gpu_exporter

# ── Deploy LocalAI OTel overrides ─────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "  Enabling LocalAI OTel on localai01"
echo "══════════════════════════════════════════"
LOCALAI_DIR_01=$(ssh ckennedy@172.31.25.75 "find /home /opt /srv -name 'docker-compose.yml' 2>/dev/null | xargs grep -l 'local-ai' 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo ''")
if [[ -n "${LOCALAI_DIR_01}" ]]; then
  scp "${REPO_ROOT}/agents/localai01/localai-otel-override.yml" "ckennedy@172.31.25.75:/tmp/"
  ssh ckennedy@172.31.25.75 "sudo cp /tmp/localai-otel-override.yml ${LOCALAI_DIR_01}/ && cd ${LOCALAI_DIR_01} && sudo docker compose -f docker-compose.yml -f localai-otel-override.yml up -d"
  echo "  ✓ LocalAI OTel override applied on localai01"
else
  echo "  ⚠ Could not find LocalAI compose dir on localai01 — apply localai-otel-override.yml manually"
fi

echo ""
echo "══════════════════════════════════════════"
echo "  Enabling LocalAI OTel on localai02"
echo "══════════════════════════════════════════"
LOCALAI_DIR_02=$(ssh ckennedy@10.25.25.67 "find /home /opt /srv -name 'docker-compose.yml' 2>/dev/null | sudo xargs grep -l 'local-ai' 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo ''")
if [[ -n "${LOCALAI_DIR_02}" ]]; then
  scp "${REPO_ROOT}/agents/localai02/localai-otel-override.yml" "ckennedy@10.25.25.67:/tmp/"
  ssh ckennedy@10.25.25.67 "sudo cp /tmp/localai-otel-override.yml ${LOCALAI_DIR_02}/ && cd ${LOCALAI_DIR_02} && sudo docker compose -f docker-compose.yml -f localai-otel-override.yml up -d"
  echo "  ✓ LocalAI OTel override applied on localai02"
else
  echo "  ⚠ Could not find LocalAI compose dir on localai02 — apply localai-otel-override.yml manually"
fi

echo ""
echo "All agents deployed. Verify in Grafana at http://localhost:3000"
