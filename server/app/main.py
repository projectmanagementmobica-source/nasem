"""FastAPI entrypoint:  uvicorn app.main:app --reload"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.approvals.router import router as approvals_router
from app.modules.auth.router import router as auth_router
from app.core.config import settings
from app.modules.gateway.router import router as gateway_router
from app.modules.guides.router import router as guides_router
from app.modules.push.router import router as push_router
from app.modules.access.router import router as access_router
from app.modules.family.router import router as family_router
from app.modules.market.router import router as market_router
from app.modules.backup.router import router as backup_router
from app.modules.internal.router import router as internal_router
from app.modules.access.service import ensure_admin
from app.modules.push.service import push_loop
from app.db.session import SessionLocal
from sqlalchemy.exc import IntegrityError
from app.modules.reports.router import router as reports_router
from app.modules.orchestrator.router import router as orchestrator_router
from app.shared.api import register_exception_handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log = logging.getLogger(__name__)
    if settings.migrate_on_start:
        try:
            from app.db.migrate import upgrade_head
            await asyncio.to_thread(upgrade_head)
        except Exception:
            log.exception("migrations on start failed (another instance may be running them)")
    try:
        async with SessionLocal() as s:
            await ensure_admin(s)
    except IntegrityError:
        log.info("admin already created by a parallel instance")
    except Exception:
        log.exception("admin bootstrap failed")
    if settings.serverless:
        # No always-on process: QStash calls /v1/internal/tick at each reminder's exact time, plus hourly.
        try:
            from app.modules.scheduler.service import ensure_hourly_schedule
            await ensure_hourly_schedule()
        except Exception:
            log.exception("could not set the hourly schedule")
        yield
        return
    stop = asyncio.Event()
    # The loop also runs the retention purge, so it starts even if push isn't configured yet.
    task = asyncio.create_task(push_loop(SessionLocal, stop))
    yield
    stop.set()
    if task is not None:
        await task


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, title="NASEM AI", version="0.9.0")
    register_exception_handlers(app)
    if settings.cors_origins:  # the installed app runs on its own domain
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
            allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type"],
        )
    app.include_router(auth_router)
    app.include_router(orchestrator_router)
    app.include_router(approvals_router)
    app.include_router(guides_router)
    app.include_router(reports_router)
    app.include_router(gateway_router)
    app.include_router(push_router)
    app.include_router(access_router)
    app.include_router(family_router)
    app.include_router(market_router)
    app.include_router(backup_router)
    app.include_router(internal_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
