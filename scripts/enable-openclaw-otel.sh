#!/usr/bin/env bash
# Enables Openclaw's native OTel diagnostics plugin, pointing it at the
# local OTel Collector running on port 4318.
set -euo pipefail

OPENCLAW_JSON="${HOME}/.openclaw/openclaw.json"
BACKUP="${OPENCLAW_JSON}.pre-otel.$(date +%Y%m%dT%H%M%S)"

if [[ ! -f "${OPENCLAW_JSON}" ]]; then
  echo "ERROR: ${OPENCLAW_JSON} not found"
  exit 1
fi

echo "Backing up openclaw.json → ${BACKUP}"
cp "${OPENCLAW_JSON}" "${BACKUP}"

echo "Patching diagnostics.otel config..."
python3 - <<'PYEOF'
import json, sys

path = f"{__import__('os').environ['HOME']}/.openclaw/openclaw.json"
with open(path) as f:
    cfg = json.load(f)

cfg.setdefault("diagnostics", {})
cfg["diagnostics"]["enabled"] = True
cfg["diagnostics"]["otel"] = {
    "enabled":     True,
    "endpoint":    "http://localhost:4318",
    "serviceName": "openclaw-gateway",
    "traces":      True,
    "metrics":     True,
    "logs":        True
}

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)

print("  ✓ openclaw.json updated")
PYEOF

echo ""
echo "Restart Openclaw to apply changes:"
echo "  openclaw restart   (or restart via your process manager)"
