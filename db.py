"""
db.py
=====
Lapisan data berbasis SQLite (tanpa ORM, portabel). Kandidat disimpan sebagai
dokumen JSON (`doc`) yang bentuknya identik dengan objek kandidat di front-end,
plus beberapa kolom ter-denormalisasi untuk filter & pengurutan cepat.

Tabel:
  candidates         — dokumen kandidat (source of truth)
  assessment_tokens  — token unik per kandidat untuk portal asesmen
  notifications      — log email / WhatsApp terkirim
  video_answers      — jawaban video (path file, transcript, skor) per pertanyaan
"""

import os
import json
import time
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

import config
from catalog import job_by_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    maxy_id      TEXT PRIMARY KEY,
    external_id  TEXT,
    track        TEXT,
    job_id       TEXT,
    name         TEXT,
    email        TEXT,
    phone        TEXT,
    kode_pelaut  TEXT,
    stage        TEXT,
    hr_column    TEXT,
    composite    INTEGER DEFAULT 0,
    self_serve   INTEGER DEFAULT 0,
    created_at   TEXT,
    updated_at   TEXT,
    doc          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cand_external ON candidates(external_id);
CREATE INDEX IF NOT EXISTS idx_cand_track    ON candidates(track);

CREATE TABLE IF NOT EXISTS assessment_tokens (
    token       TEXT PRIMARY KEY,
    maxy_id     TEXT NOT NULL,
    created_at  TEXT,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    maxy_id     TEXT,
    channel     TEXT,
    subject     TEXT,
    body        TEXT,
    status      TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS video_answers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    maxy_id        TEXT NOT NULL,
    q_index        INTEGER,
    question       TEXT,
    video_filename TEXT,
    duration_sec   REAL,
    transcript     TEXT,
    ai_summary     TEXT,
    ai_score       INTEGER,
    video_url      TEXT,
    created_at     TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(_SCHEMA)
    # Migration: tambah kolom baru ke tabel yang sudah ada
    _migrations = [
        "ALTER TABLE video_answers ADD COLUMN video_url TEXT",
    ]
    for sql in _migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # kolom sudah ada, abaikan
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Faktori dokumen kandidat (bentuk sama dengan front-end)
# ---------------------------------------------------------------------------

def blank_candidate_doc(maxy_id: str, track: str, job: dict) -> dict:
    return {
        "id": maxy_id, "track": track, "jobId": job["id"], "jobTitle": job["title"],
        "name": "", "email": "", "phone": "", "appliedAt": now_iso(),
        "cv": {"fileName": None, "objectUrl": None, "fields": {}, "matchScore": 0,
               "summary": "", "aiRecommendation": None},
        "quiz": {"answers": {}, "passFail": None, "qualScore": 0},
        "bundle": {"story": "", "tools": [], "availabilityJoin": None, "salaryExpIdr": None,
                   "disc": {"answers": {}, "tally": {"D": 0, "I": 0, "S": 0, "C": 0}, "type": None, "label": "", "fitScore": 0},
                   "iq": {"answers": {}, "correct": 0, "score": 0}, "formScore": 0},
        "video": {"answers": [], "aiScore": 0},
        "cert": {"status": None, "number": None, "validUntil": None, "screenshotPath": None},
        "composite": 0, "selfServeDone": False,
        "emailLog": [],
        "hr": {"stage1": None, "stage1At": None, "stage1Notes": "",
               "interview": {"scheduledAt": None, "mode": "Online", "location": "", "done": False,
                             "eval": {"komunikasi": 0, "teknis": 0, "sikap": 0, "notes": ""}},
               "bgCheck": {"ref1Name": "", "ref1Result": "", "ref2Name": "", "ref2Result": "",
                           "lastTenure": "", "resignReason": "", "rehire": "", "done": False},
               "mcu": {"uploaded": False, "validUntil": None, "fitForDuty": None, "done": False},
               "final": None, "finalAt": None, "finalNotes": ""},
        "contractGenerated": False,
        # metadata internal (tidak dipakai front-end tapi berguna untuk audit)
        "_meta": {"source": None, "externalId": None, "rawPayload": None},
    }


# ---------------------------------------------------------------------------
# CRUD kandidat
# ---------------------------------------------------------------------------

def _row_to_doc(row: sqlite3.Row) -> dict:
    return json.loads(row["doc"])


def save_candidate(doc: dict):
    """Simpan/replace dokumen kandidat + sinkron kolom denormalisasi."""
    from scoring import hr_column
    maxy_id = doc["id"]
    conn = get_conn()
    conn.execute("""
        INSERT INTO candidates
            (maxy_id, external_id, track, job_id, name, email, phone, kode_pelaut,
             stage, hr_column, composite, self_serve, created_at, updated_at, doc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(maxy_id) DO UPDATE SET
            external_id=excluded.external_id, track=excluded.track, job_id=excluded.job_id,
            name=excluded.name, email=excluded.email, phone=excluded.phone,
            kode_pelaut=excluded.kode_pelaut, stage=excluded.stage, hr_column=excluded.hr_column,
            composite=excluded.composite, self_serve=excluded.self_serve,
            updated_at=excluded.updated_at, doc=excluded.doc
    """, (
        maxy_id,
        (doc.get("_meta") or {}).get("externalId"),
        doc.get("track"), doc.get("jobId"), doc.get("name"), doc.get("email"),
        doc.get("phone"), (doc.get("cv", {}).get("fields", {}) or {}).get("kodePelaut"),
        doc.get("stage", ""), hr_column(doc), int(doc.get("composite", 0) or 0),
        1 if doc.get("selfServeDone") else 0,
        doc.get("appliedAt", now_iso()), now_iso(), json.dumps(doc, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()


def get_candidate(maxy_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT doc FROM candidates WHERE maxy_id=?", (maxy_id,)).fetchone()
    conn.close()
    return _row_to_doc(row) if row else None


def get_by_external_id(external_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT doc FROM candidates WHERE external_id=?", (external_id,)).fetchone()
    conn.close()
    return _row_to_doc(row) if row else None


def list_candidates(track: str | None = None, q: str | None = None) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT doc FROM candidates ORDER BY created_at DESC").fetchall()
    conn.close()
    docs = [_row_to_doc(r) for r in rows]
    if track and track != "all":
        docs = [d for d in docs if d.get("track") == track]
    if q:
        ql = q.lower()
        docs = [d for d in docs if ql in (d.get("name") or "").lower()
                or ql in (d.get("email") or "").lower()
                or ql in (d.get("jobTitle") or "").lower()]
    return docs


def delete_candidate(maxy_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM candidates WHERE maxy_id=?", (maxy_id,))
    conn.execute("DELETE FROM assessment_tokens WHERE maxy_id=?", (maxy_id,))
    conn.execute("DELETE FROM video_answers WHERE maxy_id=?", (maxy_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Token asesmen
# ---------------------------------------------------------------------------

def create_token(maxy_id: str, ttl_days: int = 30) -> str:
    token = secrets.token_urlsafe(18)
    conn = get_conn()
    conn.execute("INSERT INTO assessment_tokens (token, maxy_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (token, maxy_id, now_iso(), (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()))
    conn.commit()
    conn.close()
    return token


def resolve_token(token: str) -> str | None:
    """Kembalikan maxy_id bila token valid & belum kedaluwarsa."""
    conn = get_conn()
    row = conn.execute("SELECT maxy_id, expires_at FROM assessment_tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return None
    except Exception:
        pass
    return row["maxy_id"]


def token_for(maxy_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT token FROM assessment_tokens WHERE maxy_id=? ORDER BY created_at DESC LIMIT 1", (maxy_id,)
    ).fetchone()
    conn.close()
    return row["token"] if row else None


# ---------------------------------------------------------------------------
# Notifikasi
# ---------------------------------------------------------------------------

def log_notification(maxy_id: str, channel: str, subject: str, body: str, status: str = "sent"):
    conn = get_conn()
    conn.execute("INSERT INTO notifications (maxy_id, channel, subject, body, status, created_at) VALUES (?,?,?,?,?,?)",
                 (maxy_id, channel, subject, body, status, now_iso()))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Jawaban video
# ---------------------------------------------------------------------------

def save_video_answer(maxy_id: str, q_index: int, question: str, video_filename: str,
                      duration_sec: float, transcript: str, ai_summary: str, ai_score: int,
                      video_url: str = None) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO video_answers
            (maxy_id, q_index, question, video_filename, duration_sec, transcript, ai_summary, ai_score, video_url, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (maxy_id, q_index, question, video_filename, duration_sec, transcript, ai_summary, ai_score, video_url, now_iso()))
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def video_answers_for(maxy_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM video_answers WHERE maxy_id=? ORDER BY q_index", (maxy_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def expired_videos(days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM video_answers WHERE created_at < ? AND video_filename IS NOT NULL", (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_video_file(video_id: int):
    conn = get_conn()
    conn.execute("UPDATE video_answers SET video_filename=NULL WHERE id=?", (video_id,))
    conn.commit()
    conn.close()
