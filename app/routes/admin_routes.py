import io
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_staff, require_superadmin, create_invite_token
from app.config import ALLOWED_IMAGE_EXTS
from app.db import get_db
from app.email import send_batch_published, send_account_invite
from app.models import User, UserRole, Brand, Batch, Photo, PhotoVersion, Note
from app.storage import upload_photo, delete_photo as storage_delete_photo, get_photo_bytes
from app.templates_env import templates

_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
async def admin_home(request: Request, db: Session = Depends(get_db), staff: User = Depends(require_staff)):
    brands = db.query(Brand).order_by(Brand.name).all()
    users = db.query(User).order_by(User.role, User.name).all()
    batches = db.query(Batch).order_by(Batch.created_at.desc()).all()
    return templates.TemplateResponse("admin_home.html", {
        "request": request, "staff": staff, "brands": brands, "users": users, "batches": batches,
    })


# ── Gestione brand ──────────────────────────────────────────────────────────

@router.post("/brands")
async def create_brand(
    name: str = Form(...),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    name = name.strip()
    if db.query(Brand).filter(Brand.name == name).first():
        raise HTTPException(400, "Esiste già un brand con questo nome")
    db.add(Brand(name=name))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ── Gestione account ─────────────────────────────────────────────────────────
# Superadmin crea Admin e User; Admin crea solo User (invita client).

@router.post("/users")
async def create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    brand_id: int | None = Form(None),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    role = UserRole(role)
    if role in (UserRole.superadmin, UserRole.admin) and staff.role != UserRole.superadmin:
        raise HTTPException(403, "Solo il superadmin può creare account Admin/Superadmin")
    if role == UserRole.user and not brand_id:
        raise HTTPException(400, "Seleziona il brand per un account cliente")

    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Esiste già un utente con questa email")

    user = User(
        email=email, name=name.strip(), role=role,
        brand_id=brand_id if role == UserRole.user else None,
        password_hash=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_invite_token(user.id)
    send_account_invite(user.email, user.name, token)

    return RedirectResponse(url="/admin", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db), superadmin: User = Depends(require_superadmin),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "Utente non trovato")
    if target.id == superadmin.id:
        raise HTTPException(400, "Non puoi eliminare il tuo stesso account")
    db.delete(target)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ── Gestione batch ───────────────────────────────────────────────────────────

@router.post("/batches")
async def create_batch(
    name: str = Form(...),
    brand_id: int = Form(...),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(400, "Brand non trovato")
    batch = Batch(name=name.strip(), brand_id=brand.id)
    db.add(batch)
    db.commit()
    return RedirectResponse(url=f"/admin/batch/{batch.id}", status_code=303)


def _sku_from_filename(filename: str) -> str:
    return Path(filename).stem


@router.get("/batch/{batch_id}", response_class=HTMLResponse)
async def admin_batch_detail(
    batch_id: int, request: Request,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    photos = sorted(batch.photos, key=lambda p: p.sku)
    return templates.TemplateResponse("admin_batch.html", {
        "request": request, "staff": staff, "batch": batch, "photos": photos,
    })


@router.get("/batch/{batch_id}/download")
async def download_batch_zip(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    photos = sorted(batch.photos, key=lambda p: p.sku)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for photo in photos:
            version = photo.latest_version
            if not version:
                continue
            content = get_photo_bytes(batch.id, version.filename)
            zf.writestr(f"{photo.sku}{'.' + version.filename.rsplit('.', 1)[-1]}", content)
    buffer.seek(0)

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in batch.name).strip()
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )


@router.post("/batch/{batch_id}/upload")
async def upload_photos(
    batch_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            continue
        sku = _sku_from_filename(f.filename)

        photo = db.query(Photo).filter(Photo.batch_id == batch.id, Photo.sku == sku).first()
        if not photo:
            photo = Photo(batch_id=batch.id, sku=sku)
            db.add(photo)
            db.flush()

        next_version = len(photo.versions) + 1
        stored_name = f"{sku}__v{next_version}{ext}"
        content = await f.read()
        upload_photo(batch.id, stored_name, content, _CONTENT_TYPES.get(ext, "application/octet-stream"))

        version = PhotoVersion(photo_id=photo.id, version_num=next_version, filename=stored_name)
        db.add(version)

        # una nuova versione richiede una nuova revisione del cliente
        photo.status = "pending"

    db.commit()
    return RedirectResponse(url=f"/admin/batch/{batch_id}", status_code=303)


@router.post("/batch/{batch_id}/photo/{photo_id}/delete")
async def delete_photo(
    batch_id: int, photo_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.batch_id == batch_id).first()
    if not photo:
        raise HTTPException(404, "Foto non trovata")
    for v in photo.versions:
        storage_delete_photo(batch_id, v.filename)
    db.delete(photo)
    db.commit()
    return RedirectResponse(url=f"/admin/batch/{batch_id}", status_code=303)


@router.post("/batch/{batch_id}/publish")
async def publish_batch(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    batch.published = True
    batch.published_at = datetime.utcnow()
    db.commit()

    for client in batch.brand.users:
        if client.role == UserRole.user:
            send_batch_published(client.email, client.name, batch.name, batch.id)

    return RedirectResponse(url=f"/admin/batch/{batch_id}", status_code=303)
