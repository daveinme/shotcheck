import logging
import json
import secrets
from datetime import datetime
from pathlib import Path

from zipstream import ZipStream

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_staff, require_superadmin
from app.config import ALLOWED_IMAGE_EXTS
from app.db import get_db
from app.mailer import send_batch_published
from app.models import (
    User, UserRole, Brand, Batch, Photo, PhotoFolder, PhotoVersion, Note,
    RawBatch, RawFolder, RawUpload, RawNote,
)
from app.notifications import notify_brand
from app.storage import (
    upload_photo, delete_photo as storage_delete_photo, delete_raw_photo,
    iter_photo_chunks, photo_size, iter_raw_photo_chunks, raw_photo_size,
    raw_sizes_by_batch,
)
from app.config import RAW_RETENTION_DAYS
from app.retention import purge_raw_files, purge_batch_files
from app.templates_env import templates

_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

logger = logging.getLogger("shotcheck")
router = APIRouter(prefix="/admin")


@router.get("", response_class=HTMLResponse)
async def admin_home(request: Request, db: Session = Depends(get_db), staff: User = Depends(require_staff)):
    brands = db.query(Brand).order_by(Brand.name).all()
    users = db.query(User).order_by(User.role, User.name).all()
    batches = db.query(Batch).order_by(Batch.created_at.desc()).all()
    pending_raw_count = (
        db.query(RawBatch)
        .filter(RawBatch.status != "published", RawBatch.published == True)
        .count()
    )
    return templates.TemplateResponse("admin_home.html", {
        "request": request, "staff": staff, "brands": brands, "users": users, "batches": batches,
        "pending_raw_count": pending_raw_count,
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

    # nessuna password/invito da inviare: l'utente si auto-attiva su /activate con la sua email
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
    brand_id: str | None = Form(None),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    name = name.strip()
    brand_id = int(brand_id) if brand_id else None
    if not brand_id:
        # galleria pubblica: nessun brand, link diretto senza login
        batch = Batch(name=name, brand_id=None, public_token=secrets.token_urlsafe(16), published=True)
        db.add(batch)
        db.commit()
        return RedirectResponse(url=f"/admin/batch/{batch.id}", status_code=303)

    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(400, "Brand non trovato")
    batch = Batch(name=name, brand_id=brand.id)
    db.add(batch)
    db.commit()
    return RedirectResponse(url=f"/admin/batch/{batch.id}", status_code=303)


@router.post("/batch/{batch_id}/make-public")
async def make_batch_public(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    if batch.brand_id is not None:
        raise HTTPException(400, "Solo le gallerie senza brand possono avere un link pubblico")
    if not batch.public_token:
        batch.public_token = secrets.token_urlsafe(16)
        batch.published = True
        db.commit()
    return RedirectResponse(url=f"/admin/batch/{batch.id}", status_code=303)


def _sku_from_filename(filename: str) -> str:
    return Path(filename).stem


def _folder_breadcrumb(folder: PhotoFolder | None) -> list[PhotoFolder]:
    trail = []
    while folder is not None:
        trail.append(folder)
        folder = folder.parent
    return list(reversed(trail))


def _photo_json(p: Photo) -> dict:
    return {
        "id": p.id, "sku": p.sku, "status": p.status.value,
        "version_id": p.latest_version.id if p.latest_version else None,
        "version_num": p.latest_version.version_num if p.latest_version else None,
        "notes": [{"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body} for n in p.notes],
    }


def _note_json(n: Note) -> dict:
    return {"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body}


def _raw_upload_json(u: RawUpload) -> dict:
    return {"id": u.id, "filename": u.filename, "stored_filename": u.stored_filename}


def _raw_note_json(n: RawNote) -> dict:
    return {"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body}


@router.get("/batch/{batch_id}", response_class=HTMLResponse)
async def admin_batch_detail(
    batch_id: int, request: Request,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    if batch.files_deleted_at:
        return templates.TemplateResponse("batch_files_deleted.html", {
            "request": request, "batch": batch, "who": staff, "is_staff": True,
        })

    # albero completo delle cartelle (leggero: solo id/nome/parent), le foto
    # di ogni cartella si caricano via JS solo quando la si apre
    all_folders = (
        db.query(PhotoFolder)
        .filter(PhotoFolder.batch_id == batch.id)
        .order_by(PhotoFolder.name)
        .all()
    )
    root_photos = sorted(
        (p for p in batch.photos if p.folder_id is None),
        key=lambda p: (p.status.value != "rejected", p.sku),
    )
    root_notes = (
        db.query(Note)
        .filter(Note.batch_id == batch.id, Note.folder_id.is_(None))
        .order_by(Note.created_at)
        .all()
    )

    return templates.TemplateResponse("admin_batch.html", {
        "request": request, "staff": staff, "batch": batch,
        "all_folders_json": json.dumps([
            {"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in all_folders
        ]),
        "root_photos_json": json.dumps([_photo_json(p) for p in root_photos]),
        "root_notes_json": json.dumps([_note_json(n) for n in root_notes]),
    })


@router.get("/batch/{batch_id}/root/contents")
async def root_contents(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    photos = sorted(
        (p for p in batch.photos if p.folder_id is None),
        key=lambda p: (p.status.value != "rejected", p.sku),
    )
    notes = (
        db.query(Note)
        .filter(Note.batch_id == batch_id, Note.folder_id.is_(None))
        .order_by(Note.created_at)
        .all()
    )
    return JSONResponse({
        "photos": [_photo_json(p) for p in photos],
        "notes": [_note_json(n) for n in notes],
        "total_files": len(batch.photos),
    })


@router.get("/batch/{batch_id}/folder/{folder_id}/contents")
async def folder_contents(
    batch_id: int, folder_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    photos = sorted(folder.photos, key=lambda p: (p.status.value != "rejected", p.sku))
    notes = (
        db.query(Note)
        .filter(Note.batch_id == batch_id, Note.folder_id == folder_id)
        .order_by(Note.created_at)
        .all()
    )
    return JSONResponse({
        "photos": [_photo_json(p) for p in photos],
        "notes": [_note_json(n) for n in notes],
        "total_files": len(batch.photos),
    })


@router.post("/batch/{batch_id}/folders")
async def create_folder(
    batch_id: int,
    name: str = Form(...), parent_id: int | None = Form(None),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")

    name = name.strip()
    if not name:
        raise HTTPException(400, "Assegna un nome alla cartella")

    folder = PhotoFolder(batch_id=batch.id, parent_id=parent_id, name=name)
    db.add(folder)
    db.commit()
    return JSONResponse({"id": folder.id, "name": folder.name, "parent_id": folder.parent_id})


def _folder_subtree_counts(folder: PhotoFolder) -> tuple[int, int]:
    """Ritorna (numero cartelle incluse questa, numero foto totali) nel sottoalbero."""
    folder_count = 1
    photo_count = len(folder.photos)
    for child in folder.children:
        cf, cp = _folder_subtree_counts(child)
        folder_count += cf
        photo_count += cp
    return folder_count, photo_count


def _delete_folder_recursive(folder: PhotoFolder, batch_id: int, db: Session) -> None:
    for child in list(folder.children):
        _delete_folder_recursive(child, batch_id, db)
    for photo in list(folder.photos):
        for v in photo.versions:
            storage_delete_photo(batch_id, v.filename)
        db.delete(photo)
    db.delete(folder)


@router.get("/batch/{batch_id}/folders/{folder_id}/summary")
async def folder_delete_summary(
    batch_id: int, folder_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    folder_count, photo_count = _folder_subtree_counts(folder)
    return JSONResponse({"subfolder_count": folder_count - 1, "photo_count": photo_count})


@router.post("/batch/{batch_id}/folders/{folder_id}/delete")
async def delete_folder(
    batch_id: int, folder_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")

    _delete_folder_recursive(folder, batch_id, db)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/batch/{batch_id}/photos/{photo_id}/move")
async def move_photo(
    batch_id: int, photo_id: int,
    folder_id: int | None = Form(None),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.batch_id == batch_id).first()
    if not photo:
        raise HTTPException(404, "Foto non trovata")
    if folder_id is not None:
        folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
        if not folder:
            raise HTTPException(404, "Cartella non trovata")
    photo.folder_id = folder_id
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/batch/{batch_id}/download")
async def download_batch_zip(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    if batch.files_deleted_at:
        raise HTTPException(410, "Le foto di questo batch sono state eliminate")
    photos = sorted(batch.photos, key=lambda p: p.sku)

    # Streaming: i file passano da R2 al client a blocchi, senza costruire
    # l'intero archivio in memoria.
    zs = ZipStream(sized=True)
    for photo in photos:
        version = photo.latest_version
        if not version:
            continue
        folder_path = "/".join(f.name for f in _folder_breadcrumb(photo.folder))
        arcname = f"{folder_path}/{photo.sku}" if folder_path else photo.sku
        zs.add(
            iter_photo_chunks(batch.id, version.filename),
            f"{arcname}{'.' + version.filename.rsplit('.', 1)[-1]}",
            size=photo_size(batch.id, version.filename),
        )

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in batch.name).strip()
    return StreamingResponse(
        zs, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
            "Content-Length": str(len(zs)),
        },
    )


@router.post("/batch/{batch_id}/upload")
async def upload_photos(
    batch_id: int,
    files: list[UploadFile] = File(...),
    folder_id: int | None = Form(None),
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

        # lo SKU identifica la foto in tutto il batch, indipendentemente dalla cartella:
        # ricaricare lo stesso nome aggiorna la stessa foto ovunque si trovi (nuova versione)
        photo = db.query(Photo).filter(Photo.batch_id == batch.id, Photo.sku == sku).first()
        if not photo:
            photo = Photo(batch_id=batch.id, sku=sku, folder_id=folder_id)
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
    return JSONResponse({"ok": True})


@router.post("/batch/{batch_id}/photo/{photo_id}/replace")
async def replace_photo(
    batch_id: int, photo_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.batch_id == batch_id).first()
    if not photo:
        raise HTTPException(404, "Foto non trovata")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, "Formato file non supportato")

    was_rejected = photo.status.value == "rejected"

    next_version = len(photo.versions) + 1
    stored_name = f"{photo.sku}__v{next_version}{ext}"
    content = await file.read()
    upload_photo(batch.id, stored_name, content, _CONTENT_TYPES.get(ext, "application/octet-stream"))

    version = PhotoVersion(photo_id=photo.id, version_num=next_version, filename=stored_name)
    db.add(version)
    photo.status = "pending"

    if was_rejected:
        notify_brand(
            db, batch.brand_id, "photo_status",
            f'"{photo.sku}" è stata corretta ed è pronta per una nuova revisione — {batch.name}',
            f"/batch/{batch.id}",
        )

    db.commit()
    return JSONResponse(_photo_json(photo))


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
    background: BackgroundTasks,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    batch.published = True
    batch.published_at = datetime.utcnow()
    notify_brand(
        db, batch.brand_id, "batch_status",
        f'"{batch.name}" è pronto per la revisione',
        f"/batch/{batch.id}",
    )
    db.commit()

    # Galleria pubblica (brand_id nullo): nessun brand da avvisare.
    # Email dopo la risposta: un server SMTP lento non blocca lo staff.
    if batch.brand:
        for client in batch.brand.users:
            if client.role == UserRole.user:
                background.add_task(send_batch_published, client.email, client.name, batch.name, batch.id)

    return RedirectResponse(url=f"/admin/batch/{batch_id}", status_code=303)


@router.post("/batch/{batch_id}/delete")
async def delete_batch(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    for photo in batch.photos:
        for v in photo.versions:
            storage_delete_photo(batch.id, v.filename)
    db.delete(batch)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/batch/{batch_id}/purge-files")
async def purge_batch_files_route(
    batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    """Elimina da R2 le foto del batch lasciando la scheda (SKU, stati, note)."""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")
    n = purge_batch_files(db, batch)
    logger.info("Batch %s svuotato a mano da %s: %s file", batch.id, staff.email, n)
    return RedirectResponse(url=f"/admin/batch/{batch.id}", status_code=303)


# ── Bozze (lotti di foto grezze caricati dal brand, non ancora postprodotte) ─

def _require_published_raw_batch(raw_batch_id: int, db: Session) -> RawBatch:
    """Recupera un lotto grezzo visibile allo staff.

    Un lotto con published=False è una bozza che il brand sta ancora
    componendo: per lo staff non esiste (404, non 403 — l'esistenza stessa
    del lotto è informazione del brand). L'unica eccezione sono i lotti
    creati prima di questo campo, che restano visibili: la migrazione li
    marca published=1.
    """
    raw_batch = db.query(RawBatch).filter(RawBatch.id == raw_batch_id).first()
    if not raw_batch or not raw_batch.published:
        raise HTTPException(404, "Lotto non trovato")
    return raw_batch


@router.get("/raw-uploads", response_class=HTMLResponse)
async def raw_uploads_home(request: Request, db: Session = Depends(get_db), staff: User = Depends(require_staff)):
    raw_batches = (
        db.query(RawBatch)
        # published == False: bozza ancora in composizione dal brand, invisibile
        # allo staff finché il brand non la pubblica esplicitamente.
        .filter(RawBatch.status != "published", RawBatch.published == True)
        .order_by(func.coalesce(RawBatch.published_at, RawBatch.uploaded_at).asc())
        .all()
    )
    # priorità alta in cima, poi per data di arrivo (più vecchio prima)
    raw_batches.sort(key=lambda rb: not rb.has_high_priority)

    # Lotti chiusi: prima spariscono dalla vista staff pur avendo ancora i
    # file su R2. Qui restano visibili per monitorare la retention.
    closed = (
        db.query(RawBatch)
        .filter(RawBatch.status == "published", RawBatch.published == True)
        .all()
    )
    # dal più recente al più vecchio (i lotti chiusi prima della retention non
    # hanno closed_at: si usa la data di pubblicazione o di creazione)
    closed.sort(key=lambda rb: rb.closed_at or rb.published_at or rb.uploaded_at, reverse=True)
    try:
        sizes = raw_sizes_by_batch()
    except Exception:
        logger.exception("R2: calcolo spazio lotti RAW fallito")
        sizes = None
    total_closed_bytes = sum(sizes.get(rb.id, 0) for rb in closed) if sizes else None

    return templates.TemplateResponse("admin_raw_uploads.html", {
        "request": request, "staff": staff, "raw_batches": raw_batches,
        "closed_batches": closed, "sizes": sizes,
        "total_closed_bytes": total_closed_bytes, "retention_days": RAW_RETENTION_DAYS,
    })


@router.get("/raw-batches/{raw_batch_id}", response_class=HTMLResponse)
async def admin_raw_batch_detail(
    raw_batch_id: int, request: Request,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)

    all_folders = sorted(raw_batch.folders, key=lambda f: f.name)
    root_uploads = sorted((u for u in raw_batch.uploads if u.folder_id is None), key=lambda u: u.filename)
    root_notes = (
        db.query(RawNote)
        .filter(RawNote.raw_batch_id == raw_batch.id, RawNote.folder_id.is_(None))
        .order_by(RawNote.created_at)
        .all()
    )

    return templates.TemplateResponse("admin_raw_batch.html", {
        "request": request, "staff": staff, "raw_batch": raw_batch,
        "all_folders_json": json.dumps([
            {"id": f.id, "name": f.name, "parent_id": f.parent_id, "priority": f.priority.value}
            for f in all_folders
        ]),
        "root_uploads_json": json.dumps([_raw_upload_json(u) for u in root_uploads]),
        "root_notes_json": json.dumps([_raw_note_json(n) for n in root_notes]),
    })


@router.get("/raw-batches/{raw_batch_id}/root/contents")
async def admin_raw_root_contents(
    raw_batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)
    uploads = sorted((u for u in raw_batch.uploads if u.folder_id is None), key=lambda u: u.filename)
    notes = (
        db.query(RawNote)
        .filter(RawNote.raw_batch_id == raw_batch_id, RawNote.folder_id.is_(None))
        .order_by(RawNote.created_at)
        .all()
    )
    return JSONResponse({
        "uploads": [_raw_upload_json(u) for u in uploads],
        "notes": [_raw_note_json(n) for n in notes],
        "total_files": len(raw_batch.uploads),
    })


@router.get("/raw-batches/{raw_batch_id}/folder/{folder_id}/contents")
async def admin_raw_folder_contents(
    raw_batch_id: int, folder_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)
    folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    uploads = sorted(folder.uploads, key=lambda u: u.filename)
    notes = (
        db.query(RawNote)
        .filter(RawNote.raw_batch_id == raw_batch_id, RawNote.folder_id == folder_id)
        .order_by(RawNote.created_at)
        .all()
    )
    return JSONResponse({
        "uploads": [_raw_upload_json(u) for u in uploads],
        "notes": [_raw_note_json(n) for n in notes],
        "total_files": len(raw_batch.uploads),
    })


@router.get("/raw-batches/{raw_batch_id}/download")
async def download_raw_batch_zip(
    raw_batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)

    # Streaming: è il caso critico, i raw sono spesso TIFF da decine di MB.
    zs = ZipStream(sized=True)
    for up in raw_batch.uploads:
        arcname = f"{up.folder.name}/{up.filename}" if up.folder else up.filename
        zs.add(
            iter_raw_photo_chunks(raw_batch.id, up.stored_filename),
            arcname,
            size=raw_photo_size(raw_batch.id, up.stored_filename),
        )

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in raw_batch.name).strip()
    return StreamingResponse(
        zs, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
            "Content-Length": str(len(zs)),
        },
    )


@router.post("/raw-batches/{raw_batch_id}/status")
async def set_raw_batch_status(
    raw_batch_id: int, status: str = Form(...),
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)
    if status not in ("queued", "processing", "published"):
        raise HTTPException(400, "Stato non valido")

    labels = {"queued": "In coda", "processing": "In lavorazione", "published": "Pubblicato"}
    if raw_batch.status.value != status:
        notify_brand(
            db, raw_batch.brand_id, "raw_batch_status",
            f'"{raw_batch.name}" è ora "{labels[status]}"',
            f"/raw-batches/{raw_batch.id}",
        )

    # Retention: il conto alla rovescia parte quando lo staff chiude il lotto
    # e si azzera se lo riapre (es. chiuso per errore).
    if status == "published" and raw_batch.status.value != "published":
        raw_batch.closed_at = datetime.utcnow()
    elif status != "published":
        raw_batch.closed_at = None

    raw_batch.status = status
    db.commit()
    return RedirectResponse(url="/admin/raw-uploads", status_code=303)


@router.post("/raw-batches/{raw_batch_id}/purge-files")
async def purge_raw_batch_files(
    raw_batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    """Elimina subito i file RAW di un lotto chiuso, lasciando la scheda."""
    raw_batch = _require_published_raw_batch(raw_batch_id, db)
    if raw_batch.status.value != "published":
        raise HTTPException(400, "Si possono svuotare solo i lotti chiusi (stato Pubblicato)")
    n = purge_raw_files(db, raw_batch)
    logger.info("Lotto RAW %s svuotato a mano da %s: %s file", raw_batch.id, staff.email, n)
    return RedirectResponse(url="/admin/raw-uploads#chiusi", status_code=303)


@router.post("/raw-batches/{raw_batch_id}/delete")
async def delete_raw_batch(
    raw_batch_id: int,
    db: Session = Depends(get_db), staff: User = Depends(require_staff),
):
    raw_batch = _require_published_raw_batch(raw_batch_id, db)
    for up in raw_batch.uploads:
        delete_raw_photo(raw_batch.id, up.stored_filename)
    db.delete(raw_batch)
    db.commit()
    return RedirectResponse(url="/admin/raw-uploads", status_code=303)
