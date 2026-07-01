"""
links.py
========
Pembuatan tautan asesmen unik per kandidat + pengiriman notifikasi (WhatsApp & Email).

Alur: begitu kandidat masuk dari crewing → buat token → bentuk URL portal MAXY
(PUBLIC_BASE_URL/a/<token>) → kirim ke kandidat via WA & Email.

Provider bersifat opsional & pluggable:
  - Email : SMTP (SMTP_HOST dst). Jika kosong → hanya dicatat (console/DB).
  - WA    : WA_PROVIDER = fonnte | generic | none. Jika none → hanya dicatat.

Semua notifikasi dicatat ke tabel notifications dan ke emailLog dokumen kandidat
sehingga muncul di tab "Komunikasi" dashboard HR.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.utils import formataddr

import config
import db

log = logging.getLogger("maxy.links")


def assessment_url(token: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/a/{token}"


def ensure_link(maxy_id: str) -> str:
    """Pastikan kandidat punya token; kembalikan URL asesmen."""
    token = db.token_for(maxy_id) or db.create_token(maxy_id)
    return assessment_url(token)


# ---------------------------------------------------------------------------
# Pengiriman
# ---------------------------------------------------------------------------

def send_email(to_email: str, subject: str, body: str) -> str:
    """Kirim email via SMTP. Return status: 'sent' | 'logged' | 'error'."""
    if not (config.SMTP_HOST and to_email):
        log.info(f"[EMAIL/log] ke={to_email} subj={subject!r}")
        return "logged"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((config.COMPANY_SHORT_NAME, config.SMTP_FROM))
        msg["To"] = to_email
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            if config.SMTP_TLS:
                s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASS)
            s.sendmail(config.SMTP_FROM, [to_email], msg.as_string())
        log.info(f"Email terkirim ke {to_email}")
        return "sent"
    except Exception as e:
        log.error(f"Gagal kirim email ke {to_email}: {e}")
        return "error"


def send_whatsapp(to_phone: str, body: str) -> str:
    """Kirim WhatsApp via provider terkonfigurasi. Return status."""
    provider = config.WA_PROVIDER.lower()
    phone = _normalize_phone(to_phone)
    if provider == "none" or not phone:
        log.info(f"[WA/log] ke={phone} pesan={body[:60]!r}")
        return "logged"
    import requests
    try:
        if provider == "fonnte":
            r = requests.post(config.WA_API_URL,
                              headers={"Authorization": config.WA_API_TOKEN},
                              data={"target": phone, "message": body}, timeout=20)
        else:  # generic JSON gateway
            r = requests.post(config.WA_API_URL,
                              headers={"Authorization": f"Bearer {config.WA_API_TOKEN}",
                                       "Content-Type": "application/json"},
                              json={"to": phone, "message": body}, timeout=20)
        if r.status_code in (200, 201):
            log.info(f"WhatsApp terkirim ke {phone}")
            return "sent"
        log.error(f"WA HTTP {r.status_code}: {r.text[:160]}")
        return "error"
    except Exception as e:
        log.error(f"Gagal kirim WA ke {phone}: {e}")
        return "error"


def _normalize_phone(phone: str) -> str:
    """08xxx → 628xxx (format internasional tanpa +)."""
    p = "".join(ch for ch in (phone or "") if ch.isdigit())
    if p.startswith("0"):
        p = "62" + p[1:]
    return p


# ---------------------------------------------------------------------------
# Notifikasi terpadu (email + WA + catat ke kandidat)
# ---------------------------------------------------------------------------

def notify(doc: dict, subject: str, body: str, channels=("email", "wa")) -> dict:
    """
    Kirim notifikasi multi-kanal & catat di DB + emailLog kandidat.
    `doc` = dokumen kandidat (akan diubah in-place, caller bertanggung jawab save).
    """
    maxy_id = doc["id"]
    statuses = {}
    if "email" in channels and doc.get("email"):
        statuses["email"] = send_email(doc["email"], subject, body)
        db.log_notification(maxy_id, "email", subject, body, statuses["email"])
    if "wa" in channels and doc.get("phone"):
        wa_body = f"*{subject}*\n\n{body}"
        statuses["wa"] = send_whatsapp(doc["phone"], wa_body)
        db.log_notification(maxy_id, "whatsapp", subject, body, statuses["wa"])

    channel_label = " & ".join(
        {"email": "Email", "wa": "WhatsApp"}[c] for c in channels if c in ("email", "wa")
    )
    doc.setdefault("emailLog", []).append({
        "subject": subject, "body": body,
        "sentAt": db.now_iso(), "channel": channel_label,
    })
    return statuses


def send_assessment_link(doc: dict) -> str:
    """Buat link asesmen & kirim ke kandidat (dipanggil saat ingest)."""
    url = ensure_link(doc["id"])
    nama = doc.get("name") or "Calon Kandidat"
    posisi = doc.get("jobTitle") or "posisi yang Anda lamar"
    subject = "Link Asesmen Anda — Lintas Maritim"
    body = (
        f"Halo {nama},\n\n"
        f"Terima kasih telah mendaftar untuk posisi {posisi} melalui Lintas Maritim.\n"
        f"Silakan lanjutkan proses asesmen mandiri Anda (kuis kualifikasi & wawancara video) "
        f"melalui tautan pribadi berikut:\n\n{url}\n\n"
        f"Tautan ini khusus untuk Anda. Selesaikan setiap tahap agar lamaran dapat diproses tim kami.\n\n"
        f"Salam,\nTim Rekrutmen {config.COMPANY_SHORT_NAME}"
    )
    notify(doc, subject, body, channels=("email", "wa"))
    return url
