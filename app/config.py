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

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "notifiche@example.com")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
# SMTP (es. Gmail con "password per le app"): usato se SENDGRID_API_KEY è vuota
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
# ntfy: URL completo del topic (es. https://ntfy.sh/shotcheck-xyz), token opzionale
NTFY_URL = os.environ.get("NTFY_URL", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# bozze del brand: formati foto grezze più ampi (tiff/avif) + note/istruzioni scritte
ALLOWED_RAW_UPLOAD_EXTS = ALLOWED_IMAGE_EXTS | {".tiff", ".tif", ".avif", ".txt", ".pdf", ".docx"}

# API key dedicata per upload machine-to-machine da Scout (non credenziali utente)
SCOUT_API_KEY = os.environ.get("SCOUT_API_KEY", "")

# Giorni dopo la chiusura di un lotto (stato "Pubblicato") oltre i quali i
# file RAW vengono eliminati da R2 (script purge_raw_scaduti.py, cron notturno)
RAW_RETENTION_DAYS = int(os.environ.get("RAW_RETENTION_DAYS", "60"))
