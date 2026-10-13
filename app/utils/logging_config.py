"""Console logging for the server: one timestamped, key=value line per event."""

import logging
import time

# 2026-10-12T18:04:05.123Z INFO app.audit method=GET path=/ status=200 duration_ms=1.25
LOG_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level=logging.INFO):
    """Send the application's log records to the console with UTC millisecond timestamps.

    Uvicorn keeps its own handlers for its loggers; this configures the root logger, which receives
    the records of every app.* logger.
    """
    handler = logging.StreamHandler()
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    # UTC, so the timestamps line up with the hosting platform's logs whatever the server timezone.
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler])
