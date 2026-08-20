from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    verify_password, hash_password, create_session_token, get_current_user,
    SESSION_COOKIE, SESSION_MAX_AGE,
)
from app.db import get_db
from app.models import User, UserRole
from app.templates_env import templates

router = APIRouter()


def _post_login_dest(user: User) -> str:
    if user.role in (UserRole.superadmin, UserRole.admin):
        return "/admin"
    return "/client"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(url=_post_login_dest(user), status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Email o password non corretti"},
            status_code=401,
        )
    token = create_session_token(user.id)
    resp = RedirectResponse(url=_post_login_dest(user), status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax",
    )
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/activate", response_class=HTMLResponse)
async def activate_page(request: Request):
    return templates.TemplateResponse(
        "activate.html", {"request": request, "user_email": None, "error": None},
    )


@router.post("/activate")
async def activate_find_account(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or user.password_hash:
        return templates.TemplateResponse(
            "activate.html",
            {"request": request, "user_email": None,
             "error": "Nessun account da attivare trovato per questa email. Contatta lo studio."},
            status_code=400,
        )
    return templates.TemplateResponse(
        "activate.html", {"request": request, "user_email": user.email, "error": None},
    )


@router.post("/activate/set-password")
async def activate_set_password(
    request: Request,
    email: str = Form(...), password: str = Form(...), password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or user.password_hash:
        return templates.TemplateResponse(
            "activate.html",
            {"request": request, "user_email": None,
             "error": "Nessun account da attivare trovato per questa email. Contatta lo studio."},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "activate.html",
            {"request": request, "user_email": user.email, "error": "La password deve avere almeno 8 caratteri"},
            status_code=400,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            "activate.html",
            {"request": request, "user_email": user.email, "error": "Le password non coincidono"},
            status_code=400,
        )

    user.password_hash = hash_password(password)
    db.commit()

    session_token = create_session_token(user.id)
    resp = RedirectResponse(url=_post_login_dest(user), status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, session_token, max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax",
    )
    return resp
