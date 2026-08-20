import io
import json
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.config import ALLOWED_IMAGE_EXTS, ALLOWED_RAW_UPLOAD_EXTS
from app.db import get_db
from app.mailer import send_admin_digest
from app.models import User, UserRole, Batch, Photo, PhotoFolder, Note, RawBatch, RawFolder, RawUpload, RawUploadStatus, RawNote, Notification
from app.notifications import notify_brand, notify_staff
from app.storage import get_photo_bytes, upload_raw_photo, delete_raw_photo
from app.templates_env import templates

router = APIRouter()

_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
    ".tiff": "image/tiff", ".tif": "image/tiff", ".avif": "image/avif",
    ".txt": "text/plain", ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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


def _raw_upload_json(u: RawUpload) -> dict:
    return {"id": u.id, "filename": u.filename, "stored_filename": u.stored_filename}


def _raw_note_json(n: RawNote) -> dict:
    return {"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body}


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
    raw_batches = (
        db.query(RawBatch)
        .filter(RawBatch.brand_id == user.brand_id)
        .order_by(RawBatch.uploaded_at.desc())
        .all()
    )
    return templates.TemplateResponse("client_home.html", {
        "request": request, "user": user, "batches": batches, "raw_batches": raw_batches,
    })


def _require_client_raw_batch(raw_batch_id: int, user: User, db: Session) -> RawBatch:
    if user.role != UserRole.user:
        raise HTTPException(403, "Area riservata ai client")
    raw_batch = (
        db.query(RawBatch)
        .filter(RawBatch.id == raw_batch_id, RawBatch.brand_id == user.brand_id)
        .first()
    )
    if not raw_batch:
        raise HTTPException(404, "Lotto non trovato")
    return raw_batch


@router.get("/raw-batches/{raw_batch_id}", response_class=HTMLResponse)
async def raw_batch_detail(
    raw_batch_id: int, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    raw_batch = _require_client_raw_batch(raw_batch_id, user, db)
    all_folders = sorted(raw_batch.folders, key=lambda f: f.name)
    root_uploads = sorted((u for u in raw_batch.uploads if u.folder_id is None), key=lambda u: u.filename)
    root_notes = (
        db.query(RawNote)
        .filter(RawNote.raw_batch_id == raw_batch.id, RawNote.folder_id.is_(None))
        .order_by(RawNote.created_at)
        .all()
    )

    return templates.TemplateResponse("client_raw_batch.html", {
        "request": request, "user": user, "raw_batch": raw_batch,
        "all_folders_json": json.dumps([
            {"id": f.id, "name": f.name, "parent_id": f.parent_id, "priority": f.priority.value}
            for f in all_folders
        ]),
        "root_uploads_json": json.dumps([_raw_upload_json(u) for u in root_uploads]),
        "root_notes_json": json.dumps([_raw_note_json(n) for n in root_notes]),
    })


@router.post("/raw-batches/{raw_batch_id}/delete")
async def delete_client_raw_batch(
    raw_batch_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    raw_batch = _require_client_raw_batch(raw_batch_id, user, db)
    if raw_batch.status != RawUploadStatus.queued:
        raise HTTPException(400, "Puoi eliminare solo i lotti ancora in coda")
    for up in raw_batch.uploads:
        delete_raw_photo(raw_batch.id, up.stored_filename)
    db.delete(raw_batch)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/raw-batches")
async def create_raw_batch(
    name: str = Form(...),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    if user.role != UserRole.user:
        raise HTTPException(403, "Area riservata ai client")
    name = name.strip()
    if not name:
        raise HTTPException(400, "Assegna un nome al lotto")

    raw_batch = RawBatch(brand_id=user.brand_id, name=name, uploaded_by_id=user.id)
    db.add(raw_batch)
    db.commit()
    return JSONResponse({"id": raw_batch.id, "name": raw_batch.name})


@router.get("/raw-batches/{raw_batch_id}/root/contents")
async def raw_root_contents(
    raw_batch_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    raw_batch = _require_client_raw_batch(raw_batch_id, user, db)
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
    })


@router.get("/raw-batches/{raw_batch_id}/folder/{folder_id}/contents")
async def raw_folder_contents(
    raw_batch_id: int, folder_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
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
    })


@router.post("/raw-batches/{raw_batch_id}/notes")
async def add_raw_note(
    raw_batch_id: int,
    body: str = Form(...), folder_id: int | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    body = body.strip()
    if not body:
        raise HTTPException(400, "La nota non può essere vuota")

    if user.role == UserRole.user:
        raw_batch = _require_client_raw_batch(raw_batch_id, user, db)
    else:
        raw_batch = db.query(RawBatch).filter(RawBatch.id == raw_batch_id).first()
        if not raw_batch:
            raise HTTPException(404, "Lotto non trovato")

    if folder_id is not None:
        folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
        if not folder:
            raise HTTPException(404, "Cartella non trovata")

    note = RawNote(raw_batch_id=raw_batch.id, folder_id=folder_id, author_id=user.id, body=body)
    db.add(note)

    if user.role == UserRole.user:
        notify_staff(
            db, "note",
            f'{user.name} ha scritto una nota su "{raw_batch.name}"',
            f"/admin/raw-batches/{raw_batch.id}",
        )
    else:
        notify_brand(
            db, raw_batch.brand_id, "note",
            f'{user.name} ha scritto una nota su "{raw_batch.name}"',
            f"/raw-batches/{raw_batch.id}",
        )

    db.commit()

    return JSONResponse(_raw_note_json(note))


@router.post("/raw-notes/{note_id}/delete")
async def delete_raw_note(
    note_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    note = db.query(RawNote).filter(RawNote.id == note_id).first()
    if not note:
        raise HTTPException(404, "Nota non trovata")
    if note.author_id != user.id:
        raise HTTPException(403, "Puoi eliminare solo le tue note")

    db.delete(note)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/raw-batches/{raw_batch_id}/folders")
async def create_raw_folder(
    raw_batch_id: int,
    name: str = Form(...), parent_id: int | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    raw_batch = _require_client_raw_batch(raw_batch_id, user, db)
    name = name.strip()
    if not name:
        raise HTTPException(400, "Assegna un nome alla cartella")

    folder = RawFolder(raw_batch_id=raw_batch.id, parent_id=parent_id, name=name)
    db.add(folder)
    db.commit()
    return JSONResponse({"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "priority": folder.priority.value})


def _raw_folder_subtree_counts(folder: RawFolder) -> tuple[int, int]:
    folder_count = 1
    upload_count = len(folder.uploads)
    for child in folder.children:
        cf, cu = _raw_folder_subtree_counts(child)
        folder_count += cf
        upload_count += cu
    return folder_count, upload_count


def _delete_raw_folder_recursive(folder: RawFolder, raw_batch_id: int, db: Session) -> None:
    for child in list(folder.children):
        _delete_raw_folder_recursive(child, raw_batch_id, db)
    for upload in list(folder.uploads):
        delete_raw_photo(raw_batch_id, upload.stored_filename)
        db.delete(upload)
    db.delete(folder)


@router.get("/raw-batches/{raw_batch_id}/folders/{folder_id}/summary")
async def raw_folder_delete_summary(
    raw_batch_id: int, folder_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
    folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    folder_count, upload_count = _raw_folder_subtree_counts(folder)
    return JSONResponse({"subfolder_count": folder_count - 1, "file_count": upload_count})


@router.post("/raw-batches/{raw_batch_id}/folders/{folder_id}/delete")
async def delete_raw_folder(
    raw_batch_id: int, folder_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
    folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")

    _delete_raw_folder_recursive(folder, raw_batch_id, db)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/raw-batches/{raw_batch_id}/folders/{folder_id}/priority")
async def set_raw_folder_priority(
    raw_batch_id: int, folder_id: int,
    priority: str = Form(...),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
    if priority not in ("normal", "high"):
        raise HTTPException(400, "Priorità non valida")
    folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    folder.priority = priority
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/raw-batches/{raw_batch_id}/uploads/{upload_id}/move")
async def move_raw_upload(
    raw_batch_id: int, upload_id: int,
    folder_id: int | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
    upload = db.query(RawUpload).filter(RawUpload.id == upload_id, RawUpload.raw_batch_id == raw_batch_id).first()
    if not upload:
        raise HTTPException(404, "File non trovato")
    if folder_id is not None:
        folder = db.query(RawFolder).filter(RawFolder.id == folder_id, RawFolder.raw_batch_id == raw_batch_id).first()
        if not folder:
            raise HTTPException(404, "Cartella non trovata")
    upload.folder_id = folder_id
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/raw-batches/{raw_batch_id}/upload")
async def upload_raw_files(
    raw_batch_id: int,
    files: list[UploadFile] = File(...),
    folder_id: int | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    raw_batch = _require_client_raw_batch(raw_batch_id, user, db)

    created = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_RAW_UPLOAD_EXTS:
            continue
        content = await f.read()
        stored_name = f"{uuid.uuid4().hex}{ext}"
        upload_raw_photo(raw_batch.id, stored_name, content, _CONTENT_TYPES.get(ext, "application/octet-stream"))
        upload = RawUpload(
            raw_batch_id=raw_batch.id, folder_id=folder_id,
            filename=f.filename, stored_filename=stored_name,
        )
        db.add(upload)
        created.append(upload)

    db.commit()
    return JSONResponse({"ok": True, "uploads": [_raw_upload_json(u) for u in created]})


@router.post("/raw-batches/{raw_batch_id}/uploads/{upload_id}/delete")
async def delete_raw_upload(
    raw_batch_id: int, upload_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_raw_batch(raw_batch_id, user, db)
    upload = db.query(RawUpload).filter(RawUpload.id == upload_id, RawUpload.raw_batch_id == raw_batch_id).first()
    if not upload:
        raise HTTPException(404, "File non trovato")
    delete_raw_photo(raw_batch_id, upload.stored_filename)
    db.delete(upload)
    db.commit()
    return JSONResponse({"ok": True})


def _client_photo_json(p: Photo) -> dict:
    return {
        "id": p.id, "sku": p.sku, "status": p.status.value,
        "version_id": p.latest_version.id if p.latest_version else None,
        "version_num": p.latest_version.version_num if p.latest_version else None,
        "notes": [{"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body} for n in p.notes],
    }


def _note_json(n: Note) -> dict:
    return {"id": n.id, "author": n.author.name, "author_id": n.author_id, "body": n.body}


@router.get("/batch/{batch_id}", response_class=HTMLResponse)
async def client_batch_detail(
    batch_id: int, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)

    all_folders = sorted(batch.folders, key=lambda f: f.name)
    root_photos = sorted((p for p in batch.photos if p.folder_id is None), key=lambda p: p.sku)
    root_notes = (
        db.query(Note)
        .filter(Note.batch_id == batch.id, Note.folder_id.is_(None))
        .order_by(Note.created_at)
        .all()
    )

    return templates.TemplateResponse("client_batch.html", {
        "request": request, "user": user, "batch": batch,
        "all_folders_json": json.dumps([
            {"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in all_folders
        ]),
        "root_photos_json": json.dumps([_client_photo_json(p) for p in root_photos]),
        "root_notes_json": json.dumps([_note_json(n) for n in root_notes]),
    })


@router.get("/batch/{batch_id}/root/contents")
async def client_root_contents(
    batch_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    batch = _require_client_batch(batch_id, user, db)
    photos = sorted((p for p in batch.photos if p.folder_id is None), key=lambda p: p.sku)
    notes = (
        db.query(Note)
        .filter(Note.batch_id == batch_id, Note.folder_id.is_(None))
        .order_by(Note.created_at)
        .all()
    )
    return JSONResponse({
        "photos": [_client_photo_json(p) for p in photos],
        "notes": [_note_json(n) for n in notes],
    })


@router.get("/batch/{batch_id}/folder/{folder_id}/contents")
async def client_folder_contents(
    batch_id: int, folder_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    _require_client_batch(batch_id, user, db)
    folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
    if not folder:
        raise HTTPException(404, "Cartella non trovata")
    photos = sorted(folder.photos, key=lambda p: p.sku)
    notes = (
        db.query(Note)
        .filter(Note.batch_id == batch_id, Note.folder_id == folder_id)
        .order_by(Note.created_at)
        .all()
    )
    return JSONResponse({
        "photos": [_client_photo_json(p) for p in photos],
        "notes": [_note_json(n) for n in notes],
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

    if status == "rejected":
        notify_staff(
            db, "photo_status",
            f'"{photo.sku}" segnata da correggere da {user.name} — {batch.name}',
            f"/admin/batch/{batch.id}",
        )

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
        raise HTTPException(400, "La nota non può essere vuota")

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

    if user.role == UserRole.user:
        notify_staff(
            db, "note",
            f'{user.name} ha scritto una nota su "{photo.sku}" — {batch.name}',
            f"/admin/batch/{batch.id}",
        )
    else:
        notify_brand(
            db, batch.brand_id, "note",
            f'{user.name} ha scritto una nota su "{photo.sku}" — {batch.name}',
            f"/batch/{batch.id}",
        )

    db.commit()

    if user.role == UserRole.user:
        send_admin_digest(batch.name, batch.id, [{
            "kind": "note", "photo_sku": photo.sku, "summary": f'"{body[:120]}" — {user.name}',
        }])

    return JSONResponse(_note_json(note))


@router.post("/notes/{note_id}/delete")
async def delete_note(
    note_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(404, "Nota non trovata")
    if note.author_id != user.id:
        raise HTTPException(403, "Puoi eliminare solo le tue note")

    db.delete(note)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/batch/{batch_id}/notes")
async def add_batch_note(
    batch_id: int,
    body: str = Form(...), folder_id: int | None = Form(None),
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    body = body.strip()
    if not body:
        raise HTTPException(400, "La nota non può essere vuota")

    if user.role == UserRole.user:
        batch = _require_client_batch(batch_id, user, db)
    else:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            raise HTTPException(404, "Batch non trovato")

    if folder_id is not None:
        folder = db.query(PhotoFolder).filter(PhotoFolder.id == folder_id, PhotoFolder.batch_id == batch_id).first()
        if not folder:
            raise HTTPException(404, "Cartella non trovata")

    note = Note(batch_id=batch.id, folder_id=folder_id, author_id=user.id, body=body)
    db.add(note)

    if user.role == UserRole.user:
        send_admin_digest(batch.name, batch.id, [{
            "kind": "note", "photo_sku": "generale", "summary": f'"{body[:120]}" — {user.name}',
        }])
        notify_staff(
            db, "note",
            f'{user.name} ha scritto una nota su "{batch.name}"',
            f"/admin/batch/{batch.id}",
        )
    else:
        notify_brand(
            db, batch.brand_id, "note",
            f'{user.name} ha scritto una nota su "{batch.name}"',
            f"/batch/{batch.id}",
        )

    db.commit()

    return JSONResponse(_note_json(note))


def _notification_json(n: Notification) -> dict:
    return {
        "id": n.id, "kind": n.kind, "summary": n.summary, "link": n.link,
        "read": n.read, "created_at": n.created_at.isoformat(),
    }


@router.get("/notifications")
async def list_notifications(
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    query = db.query(Notification)
    query = query.filter(Notification.brand_id == user.brand_id) if user.role == UserRole.user \
        else query.filter(Notification.brand_id.is_(None))
    notifications = query.order_by(Notification.created_at.desc()).limit(50).all()
    unread_count = sum(1 for n in notifications if not n.read)
    return JSONResponse({
        "notifications": [_notification_json(n) for n in notifications],
        "unread_count": unread_count,
    })


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notification:
        raise HTTPException(404, "Notifica non trovata")
    is_owner = (
        notification.brand_id == user.brand_id if user.role == UserRole.user
        else notification.brand_id is None
    )
    if not is_owner:
        raise HTTPException(403, "Non autorizzato")
    notification.read = True
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    query = db.query(Notification)
    query = query.filter(Notification.brand_id == user.brand_id) if user.role == UserRole.user \
        else query.filter(Notification.brand_id.is_(None))
    query.filter(Notification.read == False).update({"read": True})  # noqa: E712
    db.commit()
    return JSONResponse({"ok": True})
