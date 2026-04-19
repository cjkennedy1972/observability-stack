#!/usr/bin/env python3
"""
LocalAI Backend Trace Shipper
------------------------------
Polls /api/backend-traces on the local LocalAI instance, converts each
inference record to an OTLP/JSON span, and ships to the central OTel
Collector (which forwards to Tempo).

The endpoint returns a ring buffer of the last ~100 backend calls.
We track the latest timestamp seen so we only ship new records each cycle.

OTLP HTTP JSON format is used — no protobuf dependency needed.
"""
import json, os, time, hashlib, struct, urllib.request, urllib.error, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("localai-trace-shipper")

LOCALAI_URL   = os.environ.get("LOCALAI_URL",   "http://localhost:8080")
OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://10.25.25.80:4318")
HOST_NAME     = os.environ.get("HOST_NAME",      "localai01")
SERVICE_NAME  = os.environ.get("SERVICE_NAME",   f"localai-{HOST_NAME}")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
LOCALAI_API_KEY = os.environ.get("LOCALAI_API_KEY", "")

# Cursor: ISO timestamp string of the last record we shipped
_last_seen_ts: str = ""


def make_trace_id(ts: str, model: str, summary: str) -> str:
    """Generate a stable 16-byte (32 hex char) trace ID from record fields."""
    h = hashlib.sha256(f"{ts}|{model}|{summary[:64]}".encode()).digest()
    return h[:16].hex()


def make_span_id(ts: str, model: str) -> str:
    """Generate a stable 8-byte (16 hex char) span ID."""
    h = hashlib.sha256(f"span|{ts}|{model}".encode()).digest()
    return h[:8].hex()


def iso_to_unix_nano(ts: str) -> int:
    """Convert ISO-8601 timestamp to Unix nanoseconds."""
    import datetime
    ts = ts.rstrip("Z")
    if "." in ts:
        dt = datetime.datetime.fromisoformat(ts)
    else:
        dt = datetime.datetime.fromisoformat(ts)
    epoch = datetime.datetime(1970, 1, 1)
    delta = dt - epoch
    return int(delta.total_seconds() * 1e9)


def duration_ns(ns: int) -> int:
    return int(ns)


def str_attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": str(value)}}


def int_attr(key: str, value: int) -> dict:
    return {"key": key, "value": {"intValue": str(value)}}


def dbl_attr(key: str, value: float) -> dict:
    return {"key": key, "value": {"doubleValue": value}}


def record_to_span(record: dict) -> dict:
    ts        = record.get("timestamp", "")
    duration  = record.get("duration", 0)          # nanoseconds
    rtype     = record.get("type", "unknown")
    model     = record.get("model_name", "unknown")
    backend   = record.get("backend", "unknown")
    summary   = record.get("summary", "")
    data      = record.get("data", {})

    start_ns  = iso_to_unix_nano(ts)
    end_ns    = start_ns + duration_ns(duration)
    trace_id  = make_trace_id(ts, model, summary)
    span_id   = make_span_id(ts, model)

    # Determine span name
    span_name = f"localai.{rtype}.inference"

    # Core attributes
    attrs = [
        str_attr("localai.model",   model),
        str_attr("localai.backend", backend),
        str_attr("localai.type",    rtype),
        str_attr("host.name",       HOST_NAME),
    ]

    # Duration in ms as a human-readable attribute
    attrs.append(dbl_attr("localai.duration_ms", duration / 1e6))

    # Summary (truncated — may contain prompt content)
    if summary and summary != "HEARTBEAT_OK":
        attrs.append(str_attr("localai.summary", summary[:256]))

    # data fields
    if isinstance(data, dict):
        # token_usage block (primary source — present in most LLM calls)
        token_usage = data.get("token_usage", {})
        if isinstance(token_usage, dict):
            prompt_tokens     = token_usage.get("prompt", 0)
            completion_tokens = token_usage.get("completion", 0)
            total_tokens      = prompt_tokens + completion_tokens
            if total_tokens > 0:
                attrs.append(int_attr("localai.tokens.prompt",     int(prompt_tokens)))
                attrs.append(int_attr("localai.tokens.completion",  int(completion_tokens)))
                attrs.append(int_attr("localai.tokens.total",       int(total_tokens)))
                # tokens-per-second (useful throughput metric)
                if duration > 0:
                    tps = completion_tokens / (duration / 1e9)
                    attrs.append(dbl_attr("localai.tokens_per_second", round(tps, 2)))

        # Legacy flat fields (fallback)
        if "tokens_count" in data:
            attrs.append(int_attr("localai.tokens.total",      int(data["tokens_count"])))
        if "prompt_tokens_count" in data:
            attrs.append(int_attr("localai.tokens.prompt",     int(data["prompt_tokens_count"])))
        if "completion_tokens_count" in data:
            attrs.append(int_attr("localai.tokens.completion", int(data["completion_tokens_count"])))

        if "chat_deltas" in data and isinstance(data["chat_deltas"], dict):
            deltas = data["chat_deltas"]
            if "total_deltas" in deltas:
                attrs.append(int_attr("localai.chat.total_deltas", int(deltas["total_deltas"])))
        if "images_count" in data and data["images_count"]:
            attrs.append(int_attr("localai.images_count", int(data["images_count"])))
        if "audios_count" in data and data["audios_count"]:
            attrs.append(int_attr("localai.audios_count", int(data["audios_count"])))

    # Status: HEARTBEAT_OK = unset, others = ok
    span_status = {"code": 1}  # STATUS_CODE_OK

    return {
        "traceId":              trace_id,
        "spanId":               span_id,
        "name":                 span_name,
        "kind":                 2,          # SPAN_KIND_SERVER
        "startTimeUnixNano":    str(start_ns),
        "endTimeUnixNano":      str(end_ns),
        "attributes":           attrs,
        "status":               span_status,
    }


def build_otlp_payload(spans: list) -> dict:
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    str_attr("service.name",    SERVICE_NAME),
                    str_attr("host.name",       HOST_NAME),
                    str_attr("service.version", "v4.1.3"),
                ]
            },
            "scopeSpans": [{
                "scope": {
                    "name":    "localai.backend-traces",
                    "version": "1.0",
                },
                "spans": spans,
            }]
        }]
    }


def fetch_traces() -> list:
    url = f"{LOCALAI_URL}/api/backend-traces"
    try:
        req = urllib.request.Request(url)
        if LOCALAI_API_KEY:
            req.add_header("Authorization", f"Bearer {LOCALAI_API_KEY}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"Failed to fetch traces: {e}")
        return []


def ship_spans(spans: list) -> bool:
    if not spans:
        return True
    payload = json.dumps(build_otlp_payload(spans)).encode()
    url = f"{OTLP_ENDPOINT}/v1/traces"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _ = r.read()
        return True
    except urllib.error.HTTPError as e:
        log.error(f"OTLP ship failed HTTP {e.code}: {e.read()[:200]}")
        return False
    except Exception as e:
        log.error(f"OTLP ship failed: {e}")
        return False


def poll_once():
    global _last_seen_ts

    records = fetch_traces()
    if not records:
        return

    # Filter to records newer than cursor, skip HEARTBEAT_OK
    new_records = [
        r for r in records
        if r.get("timestamp", "") > _last_seen_ts
        and r.get("summary", "") != "HEARTBEAT_OK"
    ]

    if not new_records:
        # Still advance cursor even if all were heartbeats
        latest = max((r.get("timestamp", "") for r in records), default="")
        if latest > _last_seen_ts:
            _last_seen_ts = latest
        return

    spans = []
    for record in new_records:
        try:
            spans.append(record_to_span(record))
        except Exception as e:
            log.warning(f"Skipping malformed record: {e}")

    if spans:
        ok = ship_spans(spans)
        if ok:
            log.info(f"Shipped {len(spans)} span(s) to Tempo")

    # Advance cursor to newest timestamp we processed
    latest = max(r.get("timestamp", "") for r in new_records)
    if latest > _last_seen_ts:
        _last_seen_ts = latest


def main():
    log.info(f"LocalAI trace shipper starting")
    log.info(f"  LocalAI: {LOCALAI_URL}")
    log.info(f"  OTLP:    {OTLP_ENDPOINT}/v1/traces")
    log.info(f"  Service: {SERVICE_NAME}")
    log.info(f"  Poll:    every {POLL_INTERVAL}s")

    # On first run, seed cursor to "now" so we don't replay old history
    records = fetch_traces()
    if records:
        _last_seen_ts_init = max(r.get("timestamp", "") for r in records)
        global _last_seen_ts
        _last_seen_ts = _last_seen_ts_init
        log.info(f"  Cursor seeded to {_last_seen_ts} ({len(records)} existing records skipped)")

    while True:
        try:
            poll_once()
        except Exception as e:
            log.error(f"Unexpected error in poll loop: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
