from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_login
from app.db import get_db
from app.models import User, UserRole, PhotoVersion, RawUpload
from app.storage import presigned_url, raw_presigned_url

router = APIRouter()


@router.get("/files/version/{version_id}")
async def serve_photo_version(
    version_id: int,
    db: Session = Depends(get_db), user: User | None = Depends(get_current_user),
):
    version = db.query(PhotoVersion).filter(PhotoVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "File non trovato")
    photo = version.photo
    batch = photo.batch

    if batch.public_token:
        pass  # galleria pubblica: accessibile senza login
    elif not user:
        raise HTTPException(404, "File non trovato")
    elif user.role == UserRole.user:
        if batch.brand_id != user.brand_id or not batch.published:
            raise HTTPException(404, "File non trovato")

    url = presigned_url(batch.id, version.filename)
    return RedirectResponse(url=url, status_code=307)


@router.get("/files/raw/{upload_id}")
async def serve_raw_upload(
    upload_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    upload = db.query(RawUpload).filter(RawUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(404, "File non trovato")
    raw_batch = upload.raw_batch

    if user.role == UserRole.user and raw_batch.brand_id != user.brand_id:
        raise HTTPException(404, "File non trovato")

    url = raw_presigned_url(raw_batch.id, upload.stored_filename)
    return RedirectResponse(url=url, status_code=307)
