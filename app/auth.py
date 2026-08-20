import hmac

from fastapi import Request, HTTPException, Depends, Header
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import SECRET_KEY, SCOUT_API_KEY
from app.db import get_db
from app.models import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="shotcheck-session")

SESSION_COOKIE = "shotcheck_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 giorni


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        return None


class RedirectToLogin(HTTPException):
    """Sollevata quando manca una sessione valida; gestita da un exception
    handler dedicato che restituisce un vero redirect 303 a /login."""

    def __init__(self):
        super().__init__(status_code=303, detail="redirect-to-login")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user_id = read_session_token(token)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """Qualunque ruolo autenticato (superadmin, admin, user)."""
    user = get_current_user(request, db)
    if not user:
        raise RedirectToLogin()
    return user


def require_staff(request: Request, db: Session = Depends(get_db)) -> User:
    """Superadmin o Admin: accesso all'area di gestione (crea batch, carica foto, invita client)."""
    user = require_login(request, db)
    if user.role not in (UserRole.superadmin, UserRole.admin):
        raise HTTPException(status_code=403, detail="Accesso riservato allo studio")
    return user


def require_superadmin(request: Request, db: Session = Depends(get_db)) -> User:
    """Solo Superadmin: gestione account Admin, cancellazione account."""
    user = require_login(request, db)
    if user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Accesso riservato al superadmin")
    return user


def require_scout_api_key(x_api_key: str = Header(...)) -> None:
    """Upload machine-to-machine da Scout: API key dedicata, non credenziali utente."""
    if not SCOUT_API_KEY or not hmac.compare_digest(x_api_key, SCOUT_API_KEY):
        raise HTTPException(status_code=401, detail="API key non valida")
