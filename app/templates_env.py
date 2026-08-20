from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import APP_NAME, BASE_URL

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["base_url"] = BASE_URL
# cache-busting: forza il browser a ricaricare style.css quando cambia, invece
# di tenere in cache la versione servita alla prima visita
templates.env.globals["asset_version"] = int((STATIC_DIR / "style.css").stat().st_mtime)
