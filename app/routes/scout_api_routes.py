from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import require_scout_api_key
from app.config import ALLOWED_IMAGE_EXTS
from app.db import get_db
from app.models import Brand, Batch, Photo, PhotoVersion
from app.storage import upload_photo

router = APIRouter(prefix="/api/scout", dependencies=[Depends(require_scout_api_key)])

_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}


@router.get("/brands")
async def list_brands(db: Session = Depends(get_db)):
    """I brand si creano solo su Shotcheck: Scout li legge da qui per il menu a tendina."""
    brands = db.query(Brand).order_by(Brand.name).all()
    return [{"id": b.id, "name": b.name} for b in brands]


@router.post("/batches")
async def create_or_get_batch(
    name: str = Form(...),
    brand_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Idempotente sul nome: se un batch con lo stesso nome/brand esiste già
    (es. invio ripetuto da Scout), lo riusa invece di duplicarlo."""
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(400, "Brand non trovato")

    name = name.strip()
    batch = db.query(Batch).filter(Batch.brand_id == brand_id, Batch.name == name).first()
    if not batch:
        batch = Batch(name=name, brand_id=brand.id)
        db.add(batch)
        db.commit()
        db.refresh(batch)
    return {"id": batch.id, "name": batch.name, "published": batch.published}


@router.post("/batch/{batch_id}/upload")
async def upload_photos(
    batch_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Stesso schema SKU.jpg / SKU_1.jpg dell'output Scout. Ricaricare uno sku
    già presente crea automaticamente la versione successiva (come da UI admin)."""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch non trovato")

    uploaded = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            continue
        sku = Path(f.filename).stem

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
        photo.status = "pending"
        uploaded.append({"sku": sku, "version": next_version})

    db.commit()
    return {"batch_id": batch.id, "uploaded": uploaded}
