"""API ZAYNB (FastAPI) — point d'entrée : uvicorn app.main:app."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import analysis, assistant, jobs, system
from config.settings import setup_logging
from src import __version__


def create_app() -> FastAPI:
    setup_logging()
    application = FastAPI(
        title="ZAYNB Genomic Backend",
        description=(
            "Panel héréditaire cancer du sein : appel de variants GATK/Parabricks, annotation ClinVar, "
            "risque par règles explicites, orchestration multi-agent LangGraph."
        ),
        version=__version__,
    )
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]
    application.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )
    for module in (system, analysis, jobs, assistant):
        application.include_router(module.router)
    return application


app = create_app()
