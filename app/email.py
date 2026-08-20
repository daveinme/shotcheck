import logging

import resend

from app.config import RESEND_API_KEY, EMAIL_FROM, ADMIN_EMAIL, BASE_URL

logger = logging.getLogger("shotcheck")

resend.api_key = RESEND_API_KEY


def _send(to: str, subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY non configurata: email non inviata (%s -> %s)", subject, to)
        return
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        })
    except Exception:
        logger.exception("Invio email fallito: %s -> %s", subject, to)


def send_account_invite(email: str, name: str, token: str) -> None:
    link = f"{BASE_URL}/invite/{token}"
    html = f"""
    <p>Ciao {name},</p>
    <p>&Egrave; stato creato per te un account su Shotcheck.</p>
    <p><a href="{link}">Imposta la tua password</a> per attivarlo.</p>
    <p>Il link scade tra 48 ore.</p>
    """
    _send(email, "Attiva il tuo account Shotcheck", html)


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
