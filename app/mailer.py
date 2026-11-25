import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import (
    SENDGRID_API_KEY, EMAIL_FROM, ADMIN_EMAIL, BASE_URL,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, NTFY_URL, NTFY_TOKEN,
)

logger = logging.getLogger("shotcheck")


def _send(to: str, subject: str, html: str) -> None:
    if SENDGRID_API_KEY:
        try:
            client = SendGridAPIClient(SENDGRID_API_KEY)
            message = Mail(from_email=EMAIL_FROM, to_emails=to, subject=subject, html_content=html)
            client.send(message)
        except Exception:
            logger.exception("Invio email fallito: %s -> %s", subject, to)
        return
    if SMTP_HOST:
        _send_smtp(to, subject, html)
        return
    logger.warning("Nessun canale email configurato (SendGrid/SMTP): email non inviata (%s -> %s)", subject, to)


def _send_smtp(to: str, subject: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to
    msg.set_content("Apri questa email con un client che legge l'HTML.")
    msg.add_alternative(html, subtype="html")
    try:
        # 465 = SSL dall'inizio (es. Aruba smtps.aruba.it), 587 = STARTTLS (es. Gmail)
        if SMTP_PORT == 465:
            smtp_conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            smtp_conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        with smtp_conn as smtp:
            if SMTP_PORT != 465:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:
        logger.exception("Invio email SMTP fallito: %s -> %s", subject, to)


def _send_ntfy(title: str, body: str, link: str) -> None:
    if not NTFY_URL:
        return
    headers = {"Title": title.encode("utf-8"), "Click": link, "Tags": "inbox_tray"}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    req = urllib.request.Request(NTFY_URL, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        logger.exception("Invio ntfy fallito: %s", title)


def _send_admins(subject: str, html: str) -> None:
    """ADMIN_EMAIL può contenere più indirizzi separati da virgola."""
    for addr in (a.strip() for a in ADMIN_EMAIL.split(",")):
        if addr:
            _send(addr, subject, html)


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
    _send_admins(f"Aggiornamenti revisione — {batch_name}", html)


def send_raw_batch_published(brand_name: str, batch_name: str, n_files: int, raw_batch_id: int) -> None:
    """Avvisa lo staff che un brand ha pubblicato un lotto di bozze.
    Email ad ADMIN_EMAIL (canale principale) + ntfy (secondario): i due canali
    sono indipendenti, se uno fallisce l'altro parte lo stesso."""
    link = f"{BASE_URL}/admin/raw-batches/{raw_batch_id}"
    subject = f"Nuovo lotto da {brand_name} — {batch_name}"
    if ADMIN_EMAIL:
        html = f"""
        <p><b>{brand_name}</b> ha pubblicato il lotto <b>{batch_name}</b> ({n_files} file).</p>
        <p><a href="{link}">Apri il lotto</a></p>
        """
        _send_admins(subject, html)
    _send_ntfy(subject, f"{n_files} file pronti da lavorare", link)
