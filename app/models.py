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


class Brand(Base):
    """Un brand/cliente (es. Scout, Zani del Fra', futuri altri).
    Più User possono appartenere allo stesso Brand e vedono gli stessi batch."""
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="brand")
    batches = relationship("Batch", back_populates="brand", cascade="all, delete-orphan")


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


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    sku = Column(String, nullable=False)  # nome file senza estensione, identifica il capo tra versioni
    status = Column(Enum(PhotoStatus), nullable=False, default=PhotoStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow)

    batch = relationship("Batch", back_populates="photos")
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
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    photo = relationship("Photo", back_populates="notes")
    author = relationship("User")


class PendingNotification(Base):
    """Coda di eventi (nota/rifiuto) da riassumere in un'unica email digest all'admin."""
    __tablename__ = "pending_notifications"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    kind = Column(String, nullable=False)  # "note" | "rejected"
    photo_sku = Column(String, nullable=False)
    summary = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
