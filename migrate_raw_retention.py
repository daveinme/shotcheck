"""Aggiunge raw_batches.closed_at / files_deleted_at / files_deleted_count.

I lotti già chiusi restano con closed_at nullo: non entrano nella
cancellazione automatica, si svuotano solo a mano dalla pagina admin
(sezione "Lotti chiusi"). Il brand vede l'avviso "eliminati a breve".
Idempotente.

Uso (sul server, dalla cartella del progetto):
    venv/bin/python3 migrate_raw_retention.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import DATABASE_URL  # noqa: E402

db_path = DATABASE_URL.replace("sqlite:///", "", 1)
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cols = {row[1] for row in cur.execute("PRAGMA table_info(raw_batches)")}

added = []
for name, sql_type in (("closed_at", "DATETIME"), ("files_deleted_at", "DATETIME"),
                       ("files_deleted_count", "INTEGER")):
    if name not in cols:
        cur.execute(f"ALTER TABLE raw_batches ADD COLUMN {name} {sql_type}")
        added.append(name)
conn.commit()
conn.close()
print("Colonne aggiunte:", ", ".join(added) if added else "nessuna (già presenti)")
