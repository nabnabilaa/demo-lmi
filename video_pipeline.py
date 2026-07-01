"""
video_pipeline.py
=================
Orkestrasi jawaban video wawancara. Dua mode pengiriman:

  MODE FILE   : kandidat upload langsung → disimpan di server → transkripsi → hapus opsional
  MODE GDRIVE : kandidat kirim link GDrive → download sementara → transkripsi → hapus temp
                HR nonton langsung dari GDrive kandidat. Storage server = 0.

Alur:
  1. Terima file atau URL video dari kandidat
  2. Ekstrak audio (ffmpeg) → transkripsi (STT)
  3. Analisis transcript (AI) → skor + ringkasan
  4. Simpan ke DB. File temp (GDrive) langsung dihapus setelah transkripsi.
"""

import os
import re
import logging
import threading
from datetime import datetime, timezone

import config
import db
from ai import analyze_video_answer, overall_video_score
from transcription import transcribe, TranscriptionRateLimited
from scoring import recompute

log = logging.getLogger("maxy.video")

_STT_RETRY_HOURS = 2
_STT_MAX_RETRIES = 4


# ---------------------------------------------------------------------------
# Helper: Google Drive URL → direct download URL
# ---------------------------------------------------------------------------

def _gdrive_direct_url(url: str) -> str | None:
    """
    Konversi URL sharing GDrive ke URL download langsung.
    Input : https://drive.google.com/file/d/FILE_ID/view?usp=sharing
    Output: https://drive.google.com/uc?export=download&id=FILE_ID
    """
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return None


def check_gdrive_public(gdrive_url: str) -> dict:
    """
    Cek apakah link GDrive bisa diakses tanpa login.

    Return:
        {"accessible": True,  "reason": "OK"}
        {"accessible": False, "reason": "<pesan untuk kandidat>"}
    """
    import requests

    direct = _gdrive_direct_url(gdrive_url)
    if not direct:
        return {"accessible": False, "reason": "Format URL Google Drive tidak valid. Pastikan link berbentuk drive.google.com/file/d/..."}

    try:
        r = requests.get(direct, allow_redirects=True, timeout=15, stream=True)

        # Redirect ke halaman login Google → file private
        if "accounts.google.com" in r.url or "ServiceLogin" in r.url:
            return {
                "accessible": False,
                "reason": "Video bersifat private. Buka Google Drive → klik kanan video → Share → ubah ke 'Anyone with the link' → salin link baru."
            }

        if r.status_code == 403:
            return {
                "accessible": False,
                "reason": "Akses ditolak (403). Pastikan sharing diset ke 'Anyone with the link can view'."
            }

        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "")
            # File video / binary → langsung bisa diunduh
            if any(t in content_type for t in ("video", "octet-stream", "binary")):
                return {"accessible": True, "reason": "OK"}
            # Halaman HTML → bisa jadi halaman konfirmasi virus-scan (file besar = masih public)
            # atau halaman login (private)
            chunk = b""
            try:
                chunk = next(r.iter_content(chunk_size=2048), b"")
            except Exception:
                pass
            if b"accounts.google.com" in chunk or b"ServiceLogin" in chunk or b"Sign in" in chunk:
                return {
                    "accessible": False,
                    "reason": "Video bersifat private. Ubah sharing ke 'Anyone with the link'."
                }
            # Halaman konfirmasi download GDrive (file besar) → tetap public
            if b"download_warning" in chunk or b"confirm=" in chunk or b"export=download" in r.url.encode():
                return {"accessible": True, "reason": "OK"}
            # Tidak bisa dipastikan → anggap tidak accessible
            return {
                "accessible": False,
                "reason": "Tidak dapat memverifikasi akses video. Pastikan link diset ke 'Anyone with the link'."
            }

        return {
            "accessible": False,
            "reason": f"Tidak dapat mengakses video (HTTP {r.status_code}). Periksa link dan setting sharing."
        }

    except Exception as e:
        log.warning(f"check_gdrive_public error untuk {gdrive_url}: {e}")
        return {"accessible": False, "reason": f"Gagal memeriksa link: {e}"}


def download_gdrive_video(maxy_id: str, q_index: int, gdrive_url: str) -> str | None:
    """
    Download video dari GDrive ke file temp lokal untuk transkripsi.
    Return path file temp, atau None jika gagal.
    File ini HARUS dihapus setelah transkripsi selesai.
    """
    import requests
    direct = _gdrive_direct_url(gdrive_url)
    if not direct:
        log.error(f"URL GDrive tidak valid: {gdrive_url}")
        return None
    try:
        # GDrive kadang redirect untuk file besar — ikuti redirect
        session = requests.Session()
        r = session.get(direct, stream=True, timeout=60)
        # Deteksi halaman konfirmasi virus-scan GDrive (file besar)
        if "confirm=" in r.url or b"download_warning" in r.content[:500]:
            token_match = re.search(rb"confirm=([0-9A-Za-z_-]+)", r.content[:1000])
            if token_match:
                confirm = token_match.group(1).decode()
                r = session.get(f"{direct}&confirm={confirm}", stream=True, timeout=120)
        r.raise_for_status()
        fname = f"{maxy_id}_q{q_index}_gdrive_tmp.mp4"
        path  = os.path.join(config.VIDEO_DIR, fname)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        size_mb = os.path.getsize(path) / 1_048_576
        log.info(f"GDrive video diunduh: {fname} ({size_mb:.1f} MB)")
        return path
    except Exception as e:
        log.error(f"Gagal download GDrive {gdrive_url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Upload file langsung (mode lama)
# ---------------------------------------------------------------------------

def save_uploaded_video(maxy_id: str, q_index: int, file_storage) -> str:
    """Simpan file upload ke storage. Return nama file relatif."""
    ext = os.path.splitext(file_storage.filename or "")[1].lower() or ".webm"
    if ext not in (".webm", ".mp4", ".mov", ".mkv"):
        ext = ".webm"
    fname = f"{maxy_id}_q{q_index}_{int(datetime.now().timestamp())}{ext}"
    path  = os.path.join(config.VIDEO_DIR, fname)
    file_storage.save(path)
    log.info(f"Video tersimpan: {fname} ({os.path.getsize(path)} bytes)")
    return fname


# ---------------------------------------------------------------------------
# Proses video (file lokal atau GDrive)
# ---------------------------------------------------------------------------

def process_video_answer(maxy_id: str, q_index: int, question: str,
                         video_filename: str, duration_sec: float, track: str,
                         _retry: int = 0, gdrive_url: str = None) -> dict:
    """
    Transkripsi + penilaian satu jawaban video.

    gdrive_url : bila diisi, video diunduh sementara dari GDrive lalu dihapus
                 setelah transkripsi. video_filename tetap dipakai sebagai label
                 di DB; HR nonton via gdrive_url langsung.
    """
    is_gdrive = bool(gdrive_url)

    if is_gdrive:
        video_path = download_gdrive_video(maxy_id, q_index, gdrive_url)
        if not video_path:
            log.error(f"Tidak bisa download GDrive video untuk {maxy_id} q{q_index}")
            return {"error": True, "qIndex": q_index}
    else:
        video_path = os.path.join(config.VIDEO_DIR, video_filename)

    transcript = ""
    try:
        transcript = transcribe(video_path, language="id")
    except TranscriptionRateLimited as e:
        if is_gdrive:
            _safe_delete(video_path)
        if _retry < _STT_MAX_RETRIES:
            next_retry = _retry + 1
            log.warning(
                f"STT rate limit {video_filename} — "
                f"retry ke-{next_retry}/{_STT_MAX_RETRIES} dalam {_STT_RETRY_HOURS} jam."
            )
            threading.Timer(
                _STT_RETRY_HOURS * 3600, process_video_answer,
                args=(maxy_id, q_index, question, video_filename,
                      duration_sec, track, next_retry, gdrive_url),
            ).start()
        else:
            log.error(f"STT menyerah setelah {_STT_MAX_RETRIES} retry: {video_filename}")
        return {"pending": True, "qIndex": q_index}
    except Exception as e:
        log.error(f"Transkripsi gagal {video_filename}: {e}")
    finally:
        # File temp GDrive selalu dihapus setelah transkripsi (berhasil atau tidak)
        if is_gdrive and video_path and os.path.exists(video_path):
            _safe_delete(video_path)

    analysis = analyze_video_answer(question, transcript, track)
    score, summary = analysis["score"], analysis["summary"]

    # Untuk GDrive: simpan URL asli sebagai videoUrl agar HR bisa nonton langsung
    stored_url = gdrive_url if is_gdrive else None
    db.save_video_answer(maxy_id, q_index, question,
                         video_filename if not is_gdrive else None,
                         duration_sec, transcript, summary, score,
                         video_url=stored_url)

    _refresh_candidate_video_summary(maxy_id)

    watch_url = gdrive_url if is_gdrive else f"/media/video/{video_filename}"
    return {"question": question, "videoUrl": watch_url, "durationSec": duration_sec,
            "transcript": transcript, "summary": summary, "aiScore": score, "qIndex": q_index}


def _safe_delete(path: str):
    try:
        os.remove(path)
        log.info(f"File temp dihapus: {path}")
    except Exception as e:
        log.warning(f"Gagal hapus file temp {path}: {e}")


def _refresh_candidate_video_summary(maxy_id: str):
    """Sinkron daftar jawaban video + skor video rata-rata ke dokumen kandidat."""
    doc = db.get_candidate(maxy_id)
    if not doc:
        return
    rows = db.video_answers_for(maxy_id)
    answers = [{
        "question": r["question"],
        "mode": "real",
        "durationSec": r["duration_sec"],
        "transcript": r["transcript"],
        "summary": r["ai_summary"],
        "aiScore": r["ai_score"],
        "videoUrl": (r["video_url"] if r.get("video_url")
                     else (f"/media/video/{r['video_filename']}" if r["video_filename"] else None)),
    } for r in rows]
    doc["video"]["answers"] = answers
    doc["video"]["aiScore"] = overall_video_score(answers)
    recompute(doc)
    db.save_candidate(doc)


def purge_expired_videos(retention_days: int | None = None) -> int:
    """Hapus file video yang melewati masa retensi. Metadata & skor tetap tersimpan."""
    days = retention_days if retention_days is not None else config.VIDEO_RETENTION_DAYS
    removed = 0
    for row in db.expired_videos(days):
        path = os.path.join(config.VIDEO_DIR, row["video_filename"])
        try:
            if os.path.exists(path):
                os.remove(path)
            db.clear_video_file(row["id"])
            removed += 1
        except Exception as e:
            log.error(f"Gagal hapus video {row['video_filename']}: {e}")
    if removed:
        log.info(f"Retensi: {removed} file video dihapus (> {days} hari).")
    return removed
