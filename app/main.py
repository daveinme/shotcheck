import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import RedirectToLogin
from app.db import Base, engine
from app.routes import auth_routes, admin_routes, client_routes, photo_files, scout_api_routes

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Shotcheck")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(client_routes.router)
app.include_router(photo_files.router)
app.include_router(scout_api_routes.router)


@app.exception_handler(RedirectToLogin)
async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url="/login", status_code=303)


@app.get("/")
async def root():
    return RedirectResponse(url="/login")
