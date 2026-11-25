"""Run Alembic migrations from inside the app (serverless hosts have no start script)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def upgrade_head() -> None:
    """Blocking: call it in a worker thread. Safe to repeat: at head it only checks the version table."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    logger.info("database schema is at head")
