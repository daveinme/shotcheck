import io
import zipfile

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.email import send_admin_digest
from app.models import User, UserRole, Batch, Photo, Note
from app.storage import get_photo_bytes
from app.templates_env import templates

router = APIRouter()


def _require_client_batch(batch_id: int, user: User, db: Session) -> Batch:
    if user.role != UserRole.user:
        raise HTTPException(403, "Area riservata ai client")
    batch = (
        db.query(Batch)
        .filter(Batch.id == batch_id, Batch.brand_id == user.brand_id, Batch.published == True)  # noqa: E712
        .first()
    )
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    return batch


@router.get("/client", response_class=HTMLResponse)
async def client_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    if user.role != UserRole.user:
        return RedirectResponse(url="/admin", status_code=303)
    batches = (
        db.query(Batch)
        .filter(Batch.brand_id == user.brand_id, Batch.published == True)  # noqa: E712
        .order_by(Batch.published_at.desc())
        .all()
    )
    return templates.TemplateResponse("client_home.html", {
        "request": request, "user": user, "batches": batches,
    })


@router.get("/batch/{batch_id}", response_class=HTMLResponse)
async def client_batch_detail(
    batch_id: int, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)
    photos = sorted(batch.photos, key=lambda p: p.sku)
    return templates.TemplateResponse("client_batch.html", {
        "request": request, "user": user, "batch": batch, "photos": photos,
    })


@router.get("/batch/{batch_id}/download")
async def download_batch_zip(
    batch_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)
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


@router.post("/batch/{batch_id}/approve-all")
async def approve_all_photos(
    batch_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)
    for photo in batch.photos:
        photo.status = "approved"
    db.commit()
    return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)


@router.post("/batch/{batch_id}/photo/{photo_id}/status")
async def set_photo_status(
    batch_id: int, photo_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "Stato non valido")
    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.batch_id == batch.id).first()
    if not photo:
        raise HTTPException(404, "Foto non trovata")
    photo.status = status
    db.commit()

    if status == "rejected":
        send_admin_digest(batch.name, batch.id, [{
            "kind": "rejected", "photo_sku": photo.sku, "summary": f"segnata da correggere da {user.name}",
        }])

    return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)


@router.post("/batch/{batch_id}/photo/{photo_id}/notes")
async def add_note(
    batch_id: int, photo_id: int,
    body: str = Form(...),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    body = body.strip()
    if not body:
        return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)

    if user.role == UserRole.user:
        batch = _require_client_batch(batch_id, user, db)
    else:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            raise HTTPException(404, "Batch non trovato")

    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.batch_id == batch.id).first()
    if not photo:
        raise HTTPException(404, "Foto non trovata")

    note = Note(photo_id=photo.id, author_id=user.id, body=body)
    db.add(note)
    db.commit()

    if user.role == UserRole.user:
        send_admin_digest(batch.name, batch.id, [{
            "kind": "note", "photo_sku": photo.sku, "summary": f'"{body[:120]}" — {user.name}',
        }])

    dest = f"/batch/{batch_id}" if user.role == UserRole.user else f"/admin/batch/{batch_id}"
    return RedirectResponse(url=dest, status_code=303)
