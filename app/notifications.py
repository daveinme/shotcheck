from sqlalchemy.orm import Session

from app.models import Notification


def notify_brand(db: Session, brand_id: int, kind: str, summary: str, link: str) -> None:
    db.add(Notification(brand_id=brand_id, kind=kind, summary=summary, link=link))


def notify_staff(db: Session, kind: str, summary: str, link: str) -> None:
    db.add(Notification(brand_id=None, kind=kind, summary=summary, link=link))
