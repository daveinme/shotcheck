"""Elimina da R2 i file RAW dei lotti chiusi da più di RAW_RETENTION_DAYS.

Lascia la scheda del lotto (nome, cartelle, note) e segna la data di
eliminazione. I lotti chiusi prima della retention (closed_at nullo) sono
esclusi: si svuotano solo a mano dalla pagina admin.

Uso (sul server, dalla cartella del progetto):
    venv/bin/python3 purge_raw_scaduti.py            # prova: elenca e basta
    venv/bin/python3 purge_raw_scaduti.py --esegui   # cancella davvero

Cron notturno (crontab -e), alle 03:30:
    30 3 * * * cd /var/www/shotcheck && venv/bin/python3 purge_raw_scaduti.py --esegui >> storage/purge_raw.log 2>&1
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")

from app.config import RAW_RETENTION_DAYS  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import RawBatch  # noqa: E402
from app.retention import purge_raw_files  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esegui", action="store_true", help="cancella davvero (senza: solo elenco)")
    args = ap.parse_args()

    limite = datetime.utcnow() - timedelta(days=RAW_RETENTION_DAYS)
    db = SessionLocal()
    try:
        scaduti = (
            db.query(RawBatch)
            .filter(
                RawBatch.status == "published",
                RawBatch.closed_at.isnot(None),
                RawBatch.closed_at <= limite,
                RawBatch.files_deleted_at.is_(None),
            )
            .all()
        )
        ora = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not scaduti:
            print(f"[{ora}] nessun lotto scaduto")
            return
        for rb in scaduti:
            n_file = len(rb.uploads)
            if args.esegui:
                n = purge_raw_files(db, rb)
                esito = "OK" if rb.files_deleted_at else "PARZIALE (riprovo domani)"
                print(f"[{ora}] n.{rb.id} {rb.brand.name} '{rb.name}': {n}/{n_file} file eliminati — {esito}")
            else:
                print(f"[{ora}] DA ELIMINARE n.{rb.id} {rb.brand.name} '{rb.name}': "
                      f"{n_file} file, chiuso il {rb.closed_at:%d/%m/%Y}")
        if not args.esegui:
            print("Prova: niente è stato cancellato. Aggiungi --esegui per cancellare.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
