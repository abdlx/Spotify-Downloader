from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

from app.config import LOG_LEVEL


REDACTION_PATTERNS = (
    (re.compile(r"(?i)\b(https?|socks5h?)://([^\s/:@]+)(?::[^\s/@]*)?@"), r"\1://***:***@"),
    (re.compile(r"(?i)(\b(?:authorization|proxy-authorization)\b[\"']?\s*[:=]\s*[\"']?)(?:Bearer\s+)?[^\s,;\"'}]+"), r"\1***"),
    (re.compile(r"(?i)(\b(?:password|proxy_password|client_secret|access_token|refresh_token|cookie)\b[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"), r"\1***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer ***"),
)


def redact_secrets(value: object) -> str:
    text = str(value)
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", "log"),
            "message": redact_secrets(record.getMessage()),
        }
        for key in ("job_id", "track_id", "item_id", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(LOG_LEVEL)
