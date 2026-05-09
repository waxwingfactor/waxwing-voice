"""
Structured logging configuration.

Uses structlog for JSON-structured logs in production and
pretty console output for local development.

Uses structlog's stdlib bridge so all log records (from structlog.get_logger()
AND from standard logging.getLogger()) go through a single StreamHandler with
consistent formatting.

PII policy: never log caller phone numbers, emails, names, or
transcript text at INFO or above. Use DEBUG only for local dev,
and redact before shipping to a log aggregator.
"""

import logging
import os

import structlog


def configure_logging() -> None:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "json").lower()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Processors shared between structlog and the stdlib foreign-pre-chain.
    # add_logger_name requires a stdlib logger (provided by stdlib.LoggerFactory).
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    # Route structlog through stdlib so add_logger_name works correctly.
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)
