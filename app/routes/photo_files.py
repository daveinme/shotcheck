from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.models import User, UserRole, PhotoVersion
from app.storage import presigned_url

router = APIRouter()


@router.get("/files/version/{version_id}")
async def serve_photo_version(
    version_id: int,
    db: Session = Depends(get_db), user: User = Depends(require_login),
):
    version = db.query(PhotoVersion).filter(PhotoVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "File non trovato")
    photo = version.photo
    batch = photo.batch

    if user.role == UserRole.user:
        if batch.brand_id != user.brand_id or not batch.published:
            raise HTTPException(404, "File non trovato")

    url = presigned_url(batch.id, version.filename)
    return RedirectResponse(url=url, status_code=307)
