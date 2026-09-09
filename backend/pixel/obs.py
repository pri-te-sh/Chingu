"""Observability: structured JSON logs (structlog) and Prometheus metrics."""
import logging, os, sys, time
import structlog
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

def setup_logging():
    level = logging.DEBUG if os.environ.get("PIXEL_ENV") == "dev" else logging.INFO
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level), logger_factory=structlog.PrintLoggerFactory(sys.stdout))

log = structlog.get_logger("pixel")

TURNS = Counter("pixel_turns_total", "conversation turns", ["device_type", "outcome"])
TOOL_CALLS = Counter("pixel_tool_calls_total", "tool calls", ["tool"])
STAGE = Histogram("pixel_turn_stage_seconds", "latency per stage", ["stage"], buckets=(.1, .25, .5, .75, 1, 1.5, 2, 3, 5, 8, 13))
WS_SESSIONS = Gauge("pixel_ws_sessions", "open device websockets")
STT_DROPPED = Counter("pixel_stt_dropped_total", "utterances dropped as junk/short")

def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
