"""FastAPI application for the web simulation MVP."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config_routes import router as config_router
from .routes import router
from .snapshot_routes import router as snapshot_router

app = FastAPI(title="CelLab API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition", "X-Config-Hash"],
)
app.include_router(router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(snapshot_router, prefix="/api")
