import logging

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from jobhunter import __version__
from jobhunter.api.routers.agent import router as agent_router
from jobhunter.api.routers.applications import router as applications_router
from jobhunter.api.routers.health import router as health_router
from jobhunter.api.routers.jobs import router as jobs_router
from jobhunter.api.routers.resume import router as resume_router
from jobhunter.config import Settings, get_settings
from jobhunter.db.migrate import init_database
from jobhunter.db.session import build_engine, session_factory
from jobhunter.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(title="Job Hunter", version=__version__)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    init_database(resolved)
    app.state.settings = resolved
    app.state.engine = build_engine(resolved)
    app.state.session_factory = session_factory(app.state.engine)
    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(applications_router)
    app.include_router(resume_router)
    app.include_router(agent_router)

    @app.middleware("http")
    async def log_request(request, call_next):
        response = await call_next(request)
        logger.info("%s %s %s", request.method, request.url.path, response.status_code)
        return response

    logger.info("api ready on %s:%s", resolved.bind_host(), resolved.port)
    return app
