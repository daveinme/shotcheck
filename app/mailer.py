import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import SENDGRID_API_KEY, EMAIL_FROM, ADMIN_EMAIL, BASE_URL

logger = logging.getLogger("shotcheck")


def _send(to: str, subject: str, html: str) -> None:
    if not SENDGRID_API_KEY:
        logger.warning("SENDGRID_API_KEY non configurata: email non inviata (%s -> %s)", subject, to)
        return
    try:
        client = SendGridAPIClient(SENDGRID_API_KEY)
        message = Mail(from_email=EMAIL_FROM, to_emails=to, subject=subject, html_content=html)
        client.send(message)
    except Exception:
        logger.exception("Invio email fallito: %s -> %s", subject, to)


def send_batch_published(client_email: str, client_name: str, batch_name: str, batch_id: int) -> None:
    link = f"{BASE_URL}/batch/{batch_id}"
    html = f"""
    <p>Ciao {client_name},</p>
    <p>Il set di foto <b>{batch_name}</b> &egrave; pronto per la revisione.</p>
    <p><a href="{link}">Vai alla revisione</a></p>
    """
    _send(client_email, f"Foto pronte per la revisione — {batch_name}", html)


def send_admin_digest(batch_name: str, batch_id: int, events: list[dict]) -> None:
    """events: lista di {kind, photo_sku, summary}"""
    if not ADMIN_EMAIL or not events:
        return
    link = f"{BASE_URL}/admin/batch/{batch_id}"
    rows = "".join(
        f"<li><b>{e['photo_sku']}</b> — {e['summary']}</li>" for e in events
    )
    html = f"""
    <p>Nuovi aggiornamenti sul batch <b>{batch_name}</b>:</p>
    <ul>{rows}</ul>
    <p><a href="{link}">Apri il batch</a></p>
    """
    _send(ADMIN_EMAIL, f"Aggiornamenti revisione — {batch_name}", html)
