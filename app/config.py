import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "Shotcheck"

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR}/storage/shotcheck.db")

# Cloudflare R2 (S3-compatible) — storage delle foto
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "shotcheck-photos")
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
)
R2_PRESIGNED_TTL = int(os.environ.get("R2_PRESIGNED_TTL", "900"))  # secondi

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Shotcheck <notifiche@example.com>")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# API key dedicata per upload machine-to-machine da Scout (non credenziali utente)
SCOUT_API_KEY = os.environ.get("SCOUT_API_KEY", "")
