"""Logging setup shared by the API and the worker."""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str, *, replace_existing: bool = False) -> None:
    """Send log records from our code (logger names starting with "app.") to stderr.

    Without this, Python only prints WARNING and above, so INFO logs are silently dropped.
    replace_existing=True overrides a setup someone else already made (the SAQ CLI
    configures WARNING); otherwise an existing setup (e.g. pytest's) is left alone.
    """
    logging.basicConfig(level=level, format=LOG_FORMAT, force=replace_existing)
