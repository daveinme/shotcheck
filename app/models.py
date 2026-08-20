import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship

from app.db import Base


class UserRole(str, enum.Enum):
    superadmin = "superadmin"  # accesso totale, unico livello che gestisce gli Admin
    admin = "admin"            # accesso a tutte le cartelle/batch, invita gli User
    user = "user"               # cliente: vede solo i batch del proprio brand


class PhotoStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class RawUploadStatus(str, enum.Enum):
    queued = "queued"           # in coda
    processing = "processing"   # in lavorazione
    published = "published"     # pubblicato (il batch risultante è online)


class RawUploadPriority(str, enum.Enum):
    normal = "normal"
    high = "high"


class Brand(Base):
    """Un brand/cliente (es. Scout, Zani del Fra', futuri altri).
    Più User possono appartenere allo stesso Brand e vedono gli stessi batch."""
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="brand")
    batches = relationship("Batch", back_populates="brand", cascade="all, delete-orphan")
    raw_batches = relationship("RawBatch", back_populates="brand", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    # null finché l'utente non completa l'invito e sceglie la propria password
    password_hash = Column(String, nullable=True)
    name = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.user)
    # solo per role=user: il brand a cui appartiene. Superadmin/Admin non hanno brand (vedono tutto).
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand", back_populates="users")


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    brand = relationship("Brand", back_populates="batches")
    photos = relationship("Photo", back_populates="batch", cascade="all, delete-orphan")
    folders = relationship("PhotoFolder", back_populates="batch", cascade="all, delete-orphan")
    notes = relationship(
        "Note", back_populates="batch", cascade="all, delete-orphan",
        order_by="Note.created_at",
    )


class PhotoFolder(Base):
    """Cartella dentro un Batch, in stile Google Drive: albero libero,
    senza significato speciale nei nomi. parent_id nullo = sottocartella
    diretta della root del batch."""
    __tablename__ = "photo_folders"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("photo_folders.id"), nullable=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="folders")
    parent = relationship("PhotoFolder", remote_side=[id], back_populates="children")
    children = relationship("PhotoFolder", back_populates="parent", cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="folder")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("photo_folders.id"), nullable=True)  # null = root del batch
    sku = Column(String, nullable=False)  # nome file senza estensione, identifica il capo tra versioni
    status = Column(Enum(PhotoStatus), nullable=False, default=PhotoStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="photos")
    folder = relationship("PhotoFolder", back_populates="photos")
    versions = relationship(
        "PhotoVersion", back_populates="photo",
        cascade="all, delete-orphan", order_by="PhotoVersion.version_num",
    )
    notes = relationship("Note", back_populates="photo", cascade="all, delete-orphan")

    @property
    def latest_version(self):
        return self.versions[-1] if self.versions else None


class PhotoVersion(Base):
    __tablename__ = "photo_versions"

    id = Column(Integer, primary_key=True)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False)
    version_num = Column(Integer, nullable=False)
    filename = Column(String, nullable=False)  # nome file su disco
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    photo = relationship("Photo", back_populates="versions")


class Note(Base):
    """Una nota appartiene o a una singola Photo o a un Batch (mutuamente
    esclusivo). Le note "generali" (batch_id valorizzato) sono legate alla
    cartella corrente tramite folder_id: folder_id nullo = note della root
    del batch, isolate esattamente come quelle di ogni altra sottocartella."""
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    folder_id = Column(Integer, ForeignKey("photo_folders.id"), nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    photo = relationship("Photo", back_populates="notes")
    batch = relationship("Batch", back_populates="notes")
    folder = relationship("PhotoFolder")
    author = relationship("User")


class RawBatch(Base):
    """Un lotto di bozze caricato dal brand (nome scelto dal brand stesso),
    prima ancora della postproduzione Scout. Contiene file sfusi e/o
    sottocartelle; una sottocartella marcata come priority=high segnala
    al team che quel sottoinsieme va lavorato per primo."""
    __tablename__ = "raw_batches"

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    name = Column(String, nullable=False)
    status = Column(Enum(RawUploadStatus), nullable=False, default=RawUploadStatus.queued)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand", back_populates="raw_batches")
    uploaded_by = relationship("User")
    uploads = relationship("RawUpload", back_populates="raw_batch", cascade="all, delete-orphan")
    folders = relationship("RawFolder", back_populates="raw_batch", cascade="all, delete-orphan")

    @property
    def has_high_priority(self) -> bool:
        return any(f.priority == RawUploadPriority.high for f in self.folders)


class RawFolder(Base):
    """Cartella dentro un RawBatch, stessa logica ad albero di PhotoFolder.
    La priorità (segnala urgenza al team) si imposta sull'intera cartella."""
    __tablename__ = "raw_folders"

    id = Column(Integer, primary_key=True)
    raw_batch_id = Column(Integer, ForeignKey("raw_batches.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("raw_folders.id"), nullable=True)
    name = Column(String, nullable=False)
    priority = Column(Enum(RawUploadPriority), nullable=False, default=RawUploadPriority.normal)
    created_at = Column(DateTime, default=datetime.utcnow)

    raw_batch = relationship("RawBatch", back_populates="folders")
    parent = relationship("RawFolder", remote_side=[id], back_populates="children")
    children = relationship("RawFolder", back_populates="parent", cascade="all, delete-orphan")
    uploads = relationship("RawUpload", back_populates="folder")


class RawUpload(Base):
    """Singolo file dentro un RawBatch. folder_id nullo indica un file
    sfuso nella root del lotto."""
    __tablename__ = "raw_uploads"

    id = Column(Integer, primary_key=True)
    raw_batch_id = Column(Integer, ForeignKey("raw_batches.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("raw_folders.id"), nullable=True)
    filename = Column(String, nullable=False)         # nome file originale, mostrato in UI
    stored_filename = Column(String, nullable=False)  # chiave oggetto su R2
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    raw_batch = relationship("RawBatch", back_populates="uploads")
    folder = relationship("RawFolder", back_populates="uploads")


class RawNote(Base):
    """Nota generale lasciata dal brand su un RawBatch, legata alla cartella
    corrente tramite folder_id: folder_id nullo = note della root del lotto,
    isolate esattamente come quelle di ogni altra sottocartella."""
    __tablename__ = "raw_notes"

    id = Column(Integer, primary_key=True)
    raw_batch_id = Column(Integer, ForeignKey("raw_batches.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("raw_folders.id"), nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    raw_batch = relationship("RawBatch")
    folder = relationship("RawFolder")
    author = relationship("User")


class Notification(Base):
    """Evento mostrato nel pannello notifiche interno (sostituisce le email,
    mai configurate con un provider reale). brand_id valorizzato = visibile
    agli User di quel Brand; brand_id nullo = visibile a tutto lo staff
    (Admin/Superadmin). link è il path relativo dove portare il click."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    kind = Column(String, nullable=False)  # "photo_status" | "batch_status" | "raw_batch_status" | "note"
    summary = Column(String, nullable=False)
    link = Column(String, nullable=False)
    read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")
