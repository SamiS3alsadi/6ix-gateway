import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import dashboard, payments, refunds, webhooks
from app.core.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting %s in %s mode", settings.app_name, settings.app_env)
    yield
    logger.info("shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    return {"status": "ok"}


app.include_router(payments.router)
app.include_router(webhooks.router)
app.include_router(refunds.router)
app.include_router(dashboard.router)
