import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.auth import RedirectToLogin
from app.config import R2_BUCKET
from app.db import Base, SessionLocal, engine
from app.routes import auth_routes, admin_routes, client_routes, photo_files, scout_api_routes
from app.storage import _client as r2_client

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


@app.get("/health")
async def health():
    checks = {}

    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        r2_client.head_bucket(Bucket=R2_BUCKET)
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(checks, status_code=200 if healthy else 503)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
