"""One JSON-lines handler per process, with secret redaction applied to every emitted line."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_SECRET_RUN = re.compile(r"(?i)(token|secret|api_key)(\W{0,6})([A-Za-z0-9]{30,})")


class Redactor:
    def __init__(self, secrets: tuple[str, ...] = ()):
        self.secrets = tuple(s for s in secrets if s)

    def __call__(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return _SECRET_RUN.sub(r"\1\2***", text)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, redact: Redactor):
        super().__init__()
        self.service, self.redact = service, redact

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            line.update(fields)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return self.redact(json.dumps(line, default=str))


def setup(service: str, logs_dir: str | Path, secrets: tuple[str, ...] = (), level=logging.INFO) -> logging.Logger:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter(service, Redactor(secrets))
    logger = logging.getLogger("zt")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(level)
    file_handler = TimedRotatingFileHandler(logs_dir / f"{service}.jsonl", when="midnight", backupCount=14)
    stream_handler = logging.StreamHandler(sys.stderr)
    for h in (file_handler, stream_handler):
        h.setFormatter(formatter)
        logger.addHandler(h)
    return logger


def event(name: str, level: int = logging.INFO, **fields) -> None:
    logging.getLogger("zt").log(level, name, extra={"fields": fields})
