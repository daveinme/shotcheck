"""Eliminazione dei file RAW dei lotti chiusi.

Cancella gli oggetti su R2 e le righe RawUpload, ma lascia la scheda del
lotto (RawBatch, cartelle, note) come storico, con la data di eliminazione.
Usata sia dal pulsante admin sia dallo script notturno purge_raw_scaduti.py.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import RawBatch
from app.storage import delete_raw_photo

logger = logging.getLogger("shotcheck")


def purge_raw_files(db: Session, raw_batch: RawBatch) -> int:
    """Elimina i file RAW del lotto. Ritorna quanti file sono stati eliminati.
    Se un file su R2 non si cancella, resta nel database: al giro dopo si
    riprova, e il lotto non viene segnato come svuotato."""
    deleted = 0
    failed = 0
    for up in list(raw_batch.uploads):
        try:
            delete_raw_photo(raw_batch.id, up.stored_filename)
        except Exception:
            failed += 1
            logger.exception("R2: eliminazione fallita %s/%s", raw_batch.id, up.stored_filename)
            continue
        db.delete(up)
        deleted += 1
    raw_batch.files_deleted_count = (raw_batch.files_deleted_count or 0) + deleted
    if failed == 0:
        raw_batch.files_deleted_at = datetime.utcnow()
    db.commit()
    return deleted
