"""
ingest.py
=========
Mengambil pendaftar dari crewing.lintasmaritim.com (website klien yang sudah ada)
dan mengubahnya menjadi kandidat MAXY. Dua mode:

  PUSH (disarankan): crewing mengirim webhook ke /webhook/crewing setiap pendaftar
                     baru. Tangani lewat ingest_payload().
  POLL  (cadangan) : MAXY menarik berkala dari admin/API crewing (butuh cookie/
                     API key). Tangani lewat poll_crewing().

Begitu kandidat dibuat: ekstrak CV → hitung skor awal → buat link asesmen unik →
kirim ke kandidat (WA & Email) → (opsional) verifikasi sertifikat Kemenhub.
"""

import re
import os
import base64
import uuid
import logging
import threading
from datetime import datetime, timedelta

import config
import db
import links
from catalog import JOBS, job_by_id
from ai import extract_cv_fields, generate_cv_summary
from scoring import cv_match, recompute

log = logging.getLogger("maxy.ingest")


# ---------------------------------------------------------------------------
# Pemetaan field crewing → MAXY
# ---------------------------------------------------------------------------

def _pick(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _resolve_job(payload: dict) -> dict:
    """Tentukan posisi (job) dari payload crewing."""
    jid = _pick(payload, "job_id", "jobId", "posisi_id")
    if jid:
        j = job_by_id(jid)
        if j:
            return j
    title = (_pick(payload, "posisi_dilamar", "posisi", "position", "jabatan_dilamar", default="") or "").lower()
    # cocokkan berdasarkan kata kunci judul
    for j in JOBS:
        if j["title"].lower() in title or title in j["title"].lower():
            return j
    keyword_map = [
        (("able seaman", "ab", "deck"), "mar-ab"),
        (("engineer", "masinis"), "mar-2nd-eng"),
        (("chief officer", "mualim", "officer"), "mar-chief-officer"),
        (("finance", "keuangan", "accounting", "akuntan"), "bo-finance"),
        (("hr", "human resource", "recruit", "rekrut"), "bo-hr"),
        (("admin", "operation", "operasi"), "bo-opsadmin"),
    ]
    for words, jid in keyword_map:
        if any(w in title for w in words):
            return job_by_id(jid)
    # fallback: tebak track dari kata kunci maritim
    is_maritime = any(w in title for w in ("kapal", "vessel", "pelaut", "marine", "deck", "engine"))
    return job_by_id("mar-ab") if is_maritime else job_by_id("bo-opsadmin")


def normalize_payload(payload: dict) -> dict:
    """Bentuk dokumen kandidat MAXY dari payload mentah crewing."""
    job = _resolve_job(payload)
    maxy_id = "C" + uuid.uuid4().hex[:8].upper()
    doc = db.blank_candidate_doc(maxy_id, job["track"], job)

    doc["name"] = _pick(payload, "nama_lengkap", "nama", "name", "full_name", default="")
    doc["email"] = _pick(payload, "email", "e_mail", default="")
    doc["phone"] = _pick(payload, "no_telepon", "phone", "no_hp", "whatsapp", "telepon", default="")
    doc["appliedAt"] = _pick(payload, "applied_at", "created_at", default=db.now_iso())

    external_id = _pick(payload, "id", "applicant_id", "external_id",
                        default=(doc["email"] or doc["phone"] or maxy_id))
    doc["_meta"] = {"source": "crewing.lintasmaritim.com", "externalId": str(external_id),
                    "rawPayload": payload}

    # kode pelaut (untuk verifikasi sertifikat)
    kode = _pick(payload, "kode_pelaut", "seaman_code", "nomor_pelaut", default=None)
    if kode:
        doc["cv"]["fields"]["kodePelaut"] = kode

    # Data registrasi mentah dari form klien (ditarik via API/webhook, TIDAK diisi ulang
    # oleh kandidat di MAXY). Disimpan agar HR bisa melihat profil lengkap.
    doc["registration"] = {
        "ktp": _pick(payload, "nomor_ktp", "ktp", "nik", default=None),
        "birthPlace": _pick(payload, "tempat_lahir", default=None),
        "birthDate": _pick(payload, "tanggal_lahir", "tgl_lahir", default=None),
        "gender": _pick(payload, "jenis_kelamin", "gender", default=None),
        "domicile": _pick(payload, "domisili", "domicile", "alamat", default=None),
        "lastVessel": _pick(payload, "jenis_kapal_terakhir", default=None),
        "lastRank": _pick(payload, "jabatan_terakhir", default=None),
        "appliedVessel": _pick(payload, "jenis_kapal_dilamar", default=None),
        "certName": _pick(payload, "sertifikat", default=None),
        # Format baru: list [{judul, nomor_sertifikat}] dari webhook crewing
        "sertifikatList": (payload.get("sertifikat") if isinstance(payload.get("sertifikat"), list) else None),
        "education": _pick(payload, "pendidikan", "edu", default=None),
        "major": _pick(payload, "jurusan", "major", default=None),
    }

    # CV: bisa berupa teks atau URL
    cv_text = _pick(payload, "cv_text", "resume_text", "cv_content", default="")
    cv_url = _pick(payload, "cv_url", "resume_url", "cv", default=None)
    if cv_url:
        doc["cv"]["fileName"] = cv_url.split("/")[-1]
        doc["cv"]["objectUrl"] = cv_url
        if not cv_text:
            cv_text = _fetch_cv_text(cv_url)
    elif doc["name"]:
        doc["cv"]["fileName"] = f"CV_{doc['name'].replace(' ', '_')}.pdf"

    # Ekstrak field CV (AI atau heuristik) + skor kecocokan
    if cv_text:
        extracted = extract_cv_fields(cv_text, job["track"])
        if kode:
            extracted["kodePelaut"] = kode
        doc["cv"]["fields"].update(extracted)

    # Field eksplisit dari form klien lebih akurat daripada hasil ekstraksi CV.
    reg = doc.get("registration", {})
    if job["track"] == "backoffice":
        if reg.get("education"):
            doc["cv"]["fields"]["edu"] = reg["education"]
        try:
            age = _pick(payload, "usia", "age", default=None)
            if age is not None:
                doc["cv"]["fields"]["age"] = int(age)
        except (TypeError, ValueError):
            pass
    else:
        if reg.get("certName"):
            certs = set(doc["cv"]["fields"].get("certs", []) or [])
            certs.add(reg["certName"])
            doc["cv"]["fields"]["certs"] = list(certs)
        if reg.get("appliedVessel"):
            doc["cv"]["fields"].setdefault("vessel", reg["appliedVessel"])

    if doc["cv"]["fields"]:
        doc["cv"]["matchScore"] = cv_match(job, doc["cv"]["fields"])
        doc["cv"]["summary"] = generate_cv_summary(cv_text, doc["cv"]["fields"], job["track"], job["title"])
        recompute(doc)

    doc["stage"] = "assessment"  # menunggu kandidat menyelesaikan asesmen mandiri
    return doc


def _fetch_cv_text(cv_url: str) -> str:
    """Coba unduh & ekstrak teks dari CV (PDF/teks). Best-effort."""
    try:
        import requests
        r = requests.get(cv_url, timeout=20)
        if r.status_code != 200:
            return ""
        ctype = r.headers.get("Content-Type", "")
        if "pdf" in ctype or cv_url.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
                from io import BytesIO
                reader = PdfReader(BytesIO(r.content))
                return "\n".join((p.extract_text() or "") for p in reader.pages)[:8000]
            except Exception:
                return ""
        return r.text[:8000]
    except Exception as e:
        log.warning(f"Gagal unduh CV {cv_url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Ingest satu kandidat (PUSH / webhook)
# ---------------------------------------------------------------------------

def ingest_payload(payload: dict, send_link: bool = True, verify_cert: bool | None = None) -> dict:
    """
    Proses satu pendaftar baru. Idempoten terhadap external_id (tidak menduplikasi).
    Return dokumen kandidat.
    """
    job = _resolve_job(payload)
    external_id = str(_pick(payload, "id", "applicant_id", "external_id",
                            default=(_pick(payload, "email", "no_telepon", default="") or uuid.uuid4().hex)))
    existing = db.get_by_external_id(external_id)
    if existing:
        log.info(f"Pendaftar {external_id} sudah ada ({existing['id']}), dilewati.")
        return existing

    doc = normalize_payload(payload)
    db.save_candidate(doc)
    log.info(f"Kandidat baru: {doc['name'] or '(tanpa nama)'} [{doc['id']}] → {doc['jobTitle']}")

    if send_link:
        url = links.send_assessment_link(doc)
        db.save_candidate(doc)  # simpan emailLog
        log.info(f"Link asesmen dikirim: {url}")

    do_verify = config.ENABLE_CERT_VERIFY if verify_cert is None else verify_cert
    kode = doc["cv"]["fields"].get("kodePelaut")
    # Ambil daftar sertifikat dari registration (jika ada) atau dari cv fields
    sertifikat_list = (
        doc.get("registration", {}).get("sertifikatList")
        or doc.get("_meta", {}).get("rawPayload", {}).get("sertifikat")
        or []
    )
    if do_verify and (kode or sertifikat_list):
        threading.Thread(
            target=_verify_cert_bg,
            args=(doc["id"], kode, sertifikat_list, doc.get("name", "")),
            daemon=True,
        ).start()

    return doc


_CERT_MAX_RETRIES   = 3
_CERT_RETRY_HOURS   = 3


def _save_cert_screenshot(maxy_id: str, result: dict) -> str | None:
    """Simpan screenshot verifikasi sertifikat ke disk. Return path relatif atau None."""
    b64 = result.get("screenshot_base64")
    if not b64:
        return None
    try:
        fname = f"{maxy_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
        path = os.path.join(config.SCREENSHOT_DIR, fname)
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        return os.path.join("cert_screenshots", fname)
    except Exception as e:
        log.warning(f"Gagal simpan screenshot sertifikat {maxy_id}: {e}")
        return None


def _verify_cert_bg(maxy_id: str, kode_pelaut: str, sertifikat_list: list = None,
                    nama: str = "", _retry: int = 0):
    """
    Verifikasi sertifikat di background lalu perbarui dokumen + composite.

    Mendukung dua mode:
    - sertifikat_list ada  → verify_candidate_certs (full: tiap sertif + title match)
    - sertifikat_list kosong → verify_single dengan kode_pelaut saja (backward compat)

    Bila hasilnya error jaringan (Kemenhub tidak bisa diakses), dijadwalkan ulang
    otomatis setiap _CERT_RETRY_HOURS jam sampai _CERT_MAX_RETRIES kali.
    """
    def _schedule_retry(reason: str):
        if _retry >= _CERT_MAX_RETRIES:
            log.error(f"Verifikasi {maxy_id}: menyerah setelah {_CERT_MAX_RETRIES} retry. "
                      f"Alasan terakhir: {reason}")
            return
        next_retry = _retry + 1
        delay_sec  = _CERT_RETRY_HOURS * 3600
        retry_at   = datetime.now() + timedelta(hours=_CERT_RETRY_HOURS)
        log.warning(f"Verifikasi {maxy_id} gagal ({reason}). "
                    f"Retry ke-{next_retry}/{_CERT_MAX_RETRIES} dijadwalkan pukul "
                    f"{retry_at.strftime('%H:%M')} ({_CERT_RETRY_HOURS} jam lagi).")
        # Catat jadwal retry di dokumen agar HR bisa lihat di dashboard
        try:
            _doc = db.get_candidate(maxy_id)
            if _doc:
                _doc.setdefault("cert", {}).update({
                    "status":    "error",
                    "retryAt":   retry_at.isoformat(),
                    "retryCount": next_retry,
                })
                db.save_candidate(_doc)
        except Exception:
            pass
        threading.Timer(
            delay_sec, _verify_cert_bg,
            args=(maxy_id, kode_pelaut, sertifikat_list, nama, next_retry),
        ).start()

    try:
        doc = db.get_candidate(maxy_id)
        if not doc:
            return

        if sertifikat_list:
            # Mode penuh: verifikasi semua sertifikat + pencocokan judul
            from cert_verifier import verify_candidate_certs
            laporan = verify_candidate_certs(
                sertifikat_list,
                nama_kandidat=nama or doc.get("name", ""),
            )
            ringkasan_status = laporan["ringkasan"]  # LULUS / GAGAL / PERLU PERIKSA
            status_map = {"LULUS": "valid", "GAGAL": "not_found", "PERLU PERIKSA": "needs_review"}
            cert_status = status_map.get(ringkasan_status, "error")
            doc["cert"] = {
                "status":        cert_status,
                "ringkasan":     ringkasan_status,
                "total":         laporan["total"],
                "lulus":         laporan["lulus"],
                "gagal":         laporan["gagal"],
                "perlu_periksa": laporan.get("perlu_periksa", 0),
                "detail":        laporan["detail"],
                "verifiedAt":    laporan["verified_at"],
            }
            log.info(f"Verifikasi sertifikat {maxy_id}: {ringkasan_status} "
                     f"({laporan['lulus']}/{laporan['total']} lulus)")
        else:
            # Mode lama (backward compat): satu kode pelaut
            from cert_verifier import verify_single
            result = verify_single(kode_pelaut)
            status_map = {"Valid": "valid", "Kadaluarsa": "expired",
                          "Tidak Ditemukan": "not_found", "Error Jaringan": "error"}
            cert_status = status_map.get(result["status"], "error")
            ss_path = _save_cert_screenshot(maxy_id, result)
            doc["cert"] = {
                "status":         cert_status,
                "number":         result.get("nomor_sertifikat") or kode_pelaut,
                "validUntil":     result.get("tanggal_berlaku"),
                "verifiedAt":     result.get("verified_at"),
                "screenshotPath": ss_path,
            }
            log.info(f"Verifikasi sertifikat {maxy_id}: {result['status']}")

        recompute(doc)
        db.save_candidate(doc)

        # Kalau hasilnya error jaringan → jadwalkan retry otomatis
        if cert_status == "error":
            _schedule_retry("error jaringan dari Kemenhub")

    except Exception as e:
        log.error(f"Verifikasi sertifikat exception untuk {maxy_id}: {e}")
        _schedule_retry(str(e))


# ---------------------------------------------------------------------------
# Poll crewing (PULL)
# ---------------------------------------------------------------------------

def fetch_crewing_applicants() -> list[dict]:
    """
    Tarik daftar pendaftar dari crewing. Mendukung respons JSON; bila HTML,
    serahkan ke parser tabel (perlu disesuaikan dengan markup nyata crewing).
    """
    if not config.CREWING_POLL_URL:
        log.warning("CREWING_POLL_URL belum diset — poll dilewati.")
        return []
    import requests
    headers = {"Accept": "application/json", "User-Agent": "MAXY-Integration/1.0"}
    if config.CREWING_API_KEY:
        headers["Authorization"] = f"Bearer {config.CREWING_API_KEY}"
    if config.CREWING_SESSION_COOKIE:
        headers["Cookie"] = config.CREWING_SESSION_COOKIE
    try:
        r = requests.get(config.CREWING_POLL_URL, headers=headers, timeout=25)
        if r.status_code != 200:
            log.error(f"Poll crewing HTTP {r.status_code}")
            return []
        if "application/json" in r.headers.get("Content-Type", ""):
            data = r.json()
            return data.get("data", data.get("applicants", data if isinstance(data, list) else [])) or []
        return _parse_html_applicants(r.text)
    except Exception as e:
        log.error(f"Gagal poll crewing: {e}")
        return []


def _parse_html_applicants(html: str) -> list[dict]:
    """
    Parser tabel HTML cadangan. SELESAIKAN selector sesuai markup admin crewing.
    Default: kosong (agar tidak salah-baca data).
    """
    log.warning("Respons crewing berupa HTML — sesuaikan _parse_html_applicants() "
                "dengan struktur tabel admin crewing untuk scraping.")
    return []


def poll_crewing(send_link: bool = True) -> list[dict]:
    """Tarik & proses semua pendaftar baru dari crewing. Return kandidat baru."""
    applicants = fetch_crewing_applicants()
    new_docs = []
    for a in applicants:
        doc = ingest_payload(a, send_link=send_link)
        new_docs.append(doc)
    if new_docs:
        log.info(f"Poll selesai: {len(new_docs)} pendaftar diproses.")
    return new_docs
