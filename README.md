# Observability Stack

Full observability stack for **Openclaw** and two **LocalAI** GPU inference hosts.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  openclaw (this host)                                           │
│                                                                 │
│  Openclaw Gateway ──OTLP──► OTel Collector ──► Tempo (traces)  │
│                                           ├──► Prometheus (metrics)
│                                           └──► Loki (logs)     │
│                                                                 │
│  Grafana ◄── Prometheus / Loki / Tempo                         │
│  Alertmanager ◄── Prometheus                                   │
└─────────────────────────────────────────────────────────────────┘
         ▲ scrape metrics            ▲ push logs
         │                           │
┌────────┴──────────────┐   ┌────────┴──────────────┐
│  localai01            │   │  localai02             │
│  172.31.25.75         │   │  10.25.25.67           │
│  2x AMD R9700 (RDNA4) │   │  NVIDIA RTX 5070 Ti    │
│                       │   │                        │
│  LocalAI :8080/metrics│   │  LocalAI :8080/metrics │
│  node_exporter :9100  │   │  node_exporter :9100   │
│  amd_smi_exporter:2021│   │  nvidia_gpu_exp.:9835  │
│  Alloy (log shipper)  │   │  Alloy (log shipper)   │
└───────────────────────┘   └────────────────────────┘
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3000 | Dashboards & alerting UI |
| Prometheus | 9090 | Metrics storage & scraping |
| Loki | 3100 | Log aggregation |
| Tempo | 3200 | Distributed tracing |
| OTel Collector | 4317/4318 | OTLP ingest from Openclaw & LocalAI |
| Alertmanager | 9093 | Alert routing |
| node-exporter | 9100 | Host metrics (this host) |

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
$EDITOR .env   # set GRAFANA_PASSWORD, LOKI_HOST

# 2. Start the core stack
docker compose up -d

# 3. Enable Openclaw OTel diagnostics
bash scripts/enable-openclaw-otel.sh
openclaw restart

# 4. Deploy agents to remote hosts
bash scripts/deploy-agents.sh

# 5. Open Grafana
open http://localhost:3000   # admin / <GRAFANA_PASSWORD>
```

## Dashboards

- **LocalAI — Overview**: API rate, p95 latency, GPU VRAM, GPU temperature & utilization for both hosts
- **Openclaw — Gateway Overview**: Token usage, cost, agent turn latency, traces
- **Host Resources — All Nodes**: CPU, memory, disk, network for all three hosts

## GPU Exporters

| Host | GPU | Exporter | Port |
|------|-----|----------|------|
| localai01 | 2x AMD Radeon AI PRO R9700 (gfx1201) | `ghcr.io/amd/amd_smi_exporter` | 2021 |
| localai02 | NVIDIA RTX 5070 Ti | `utkuozdemir/nvidia_gpu_exporter` | 9835 |

## Alert Rules

- **LocalAI**: instance down, high latency, high error rate
- **GPU**: high temperature (>85°C), VRAM >90% full, exporter down
- **Openclaw**: gateway unreachable, token cost >$1/hr, slow agent turns (p95>60s)
- **Infrastructure**: CPU >90%, memory >90%, disk >85%

## LocalAI OTel Tracing

Each LocalAI host has a `localai-otel-override.yml` docker compose override that adds OTLP
environment variables to the LocalAI container, enabling trace shipping to the central
OTel Collector. Applied automatically by `deploy-agents.sh`.
