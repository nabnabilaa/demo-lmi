"""
tests_pipeline.py
=================
Penanganan hasil tes DISC/MBTI & IQ yang dikerjakan kandidat di PLATFORM EKSTERNAL
(pihak ketiga), bukan di portal MAXY. Dua mode (lihat config.TEST_RESULT_MODE):

  "api"    : MAXY menarik hasil otomatis dari API provider tes. Kandidat cukup
             mengerjakan tes via tautan; tidak perlu mengunggah apa pun.
  "upload" : Kandidat mengerjakan tes di tautan eksternal lalu MENGUNGGAH berkas
             hasilnya (PDF/gambar) ke MAXY. HR meninjau berkas tersebut.

Tujuan: tidak ada pertanyaan DISC/IQ yang ditampilkan/diisi ulang di MAXY.
"""

import os
import logging
from datetime import datetime

import config
import db
from scoring import disc_from_result, iq_from_result, recompute

log = logging.getLogger("maxy.tests")

_ALLOWED_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx")


def save_uploaded_test_file(maxy_id: str, kind: str, file_storage) -> dict:
    """Simpan berkas hasil tes (DISC/IQ) yang diunggah kandidat.

    kind: "disc" | "iq". Return {fileName, url}.
    """
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        ext = ".pdf"
    fname = f"{maxy_id}_{kind}_{int(datetime.now().timestamp())}{ext}"
    path = os.path.join(config.TEST_DIR, fname)
    file_storage.save(path)
    log.info(f"Hasil tes {kind} tersimpan: {fname} ({os.path.getsize(path)} bytes)")
    return {"fileName": fname, "url": f"/media/test/{fname}"}


def pull_results_from_provider(doc: dict) -> dict:
    """Mode 'api': ambil hasil DISC & IQ dari API provider tes pihak ketiga.

    Implementasi nyata memanggil config.TEST_PROVIDER_API_URL dengan kunci
    kandidat (mis. email) lalu memetakan respons ke {discType, iqScore}.
    Tanpa kredensial provider (default), kembalikan hasil contoh deterministik
    agar demo tetap berjalan.
    """
    if config.TEST_PROVIDER != "none" and config.TEST_PROVIDER_API_URL and config.TEST_PROVIDER_API_TOKEN:
        try:
            import requests
            r = requests.get(
                config.TEST_PROVIDER_API_URL,
                params={"email": doc.get("email"), "ref": doc["id"]},
                headers={"Authorization": f"Bearer {config.TEST_PROVIDER_API_TOKEN}"},
                timeout=25,
            )
            if r.status_code == 200:
                data = r.json()
                return {"discType": data.get("disc_type") or data.get("discType"),
                        "iqScore": data.get("iq_score") or data.get("iqScore")}
            log.error(f"Provider tes HTTP {r.status_code}")
        except Exception as e:
            log.error(f"Gagal tarik hasil tes dari provider: {e}")

    # Fallback contoh (deterministik dari id) — hanya untuk demo tanpa provider.
    seed = sum(ord(ch) for ch in doc["id"])
    disc_type = ["D", "I", "S", "C"][seed % 4]
    iq_score = 70 + (seed % 26)  # 70..95
    return {"discType": disc_type, "iqScore": iq_score}


def apply_test_results(doc: dict, *, source: str, disc_type=None, iq_score=None,
                       disc_file: dict | None = None, iq_file: dict | None = None) -> dict:
    """Terapkan hasil tes eksternal ke dokumen kandidat lalu hitung ulang composite."""
    b = doc["bundle"]
    b["testSource"] = source

    disc = disc_from_result(disc_type)
    if disc_file:
        disc["resultFileName"] = disc_file["fileName"]
        disc["resultUrl"] = disc_file["url"]
    else:
        disc.setdefault("resultFileName", b.get("disc", {}).get("resultFileName"))
        disc.setdefault("resultUrl", b.get("disc", {}).get("resultUrl"))
    b["disc"] = disc

    iq = iq_from_result(iq_score)
    if iq_file:
        iq["resultFileName"] = iq_file["fileName"]
        iq["resultUrl"] = iq_file["url"]
    else:
        iq.setdefault("resultFileName", b.get("iq", {}).get("resultFileName"))
        iq.setdefault("resultUrl", b.get("iq", {}).get("resultUrl"))
    b["iq"] = iq

    recompute(doc)
    db.save_candidate(doc)
    return doc
