"""Crea il primo account Superadmin. Uso: python3 bootstrap_superadmin.py"""
import getpass

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.models import User, UserRole

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    if db.query(User).filter(User.role == UserRole.superadmin).first():
        print("Esiste già un account superadmin.")
    else:
        name = input("Nome: ").strip()
        email = input("Email: ").strip().lower()
        password = getpass.getpass("Password: ")
        user = User(name=name, email=email, role=UserRole.superadmin, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        print(f"Superadmin creato: {email}")
finally:
    db.close()
