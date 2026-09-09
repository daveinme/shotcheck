#!/usr/bin/env python3
"""Aggiunge raw_batches.published / published_at.

Un RawBatch con published=0 è una bozza privata del brand: lo staff non la
vede finché il brand non la pubblica. I lotti già esistenti vengono marcati
published=1 — erano visibili allo staff prima di questa modifica, e farli
sparire di colpo sarebbe una regressione per lavori già in corso.

Idempotente: eseguirlo più volte non fa danni.

    venv/bin/python3 migrate_raw_batch_published.py
"""
import sqlite3
import sys

from app.config import DATABASE_URL

if not DATABASE_URL.startswith("sqlite:///"):
    sys.exit(f"Questo script gestisce solo SQLite, non {DATABASE_URL}")

db_path = DATABASE_URL[len("sqlite:///"):]
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cols = {row[1] for row in cur.execute("PRAGMA table_info(raw_batches)")}
if not cols:
    sys.exit(f"Tabella raw_batches non trovata in {db_path}")

added = []
if "published" not in cols:
    cur.execute("ALTER TABLE raw_batches ADD COLUMN published BOOLEAN DEFAULT 0")
    # I lotti preesistenti erano già visibili allo staff: restano tali.
    cur.execute("UPDATE raw_batches SET published = 1")
    added.append("published")
if "published_at" not in cols:
    cur.execute("ALTER TABLE raw_batches ADD COLUMN published_at DATETIME")
    added.append("published_at")

conn.commit()
n = cur.execute("SELECT COUNT(*) FROM raw_batches WHERE published = 1").fetchone()[0]
tot = cur.execute("SELECT COUNT(*) FROM raw_batches").fetchone()[0]
conn.close()

print(f"DB: {db_path}")
print(f"Colonne aggiunte: {', '.join(added) if added else 'nessuna (già presenti)'}")
print(f"Lotti pubblicati: {n}/{tot}")
