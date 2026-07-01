"""
config.py
=========
Konfigurasi terpusat MAXY. Semua nilai dibaca dari environment (.env),
dengan default aman untuk pengembangan lokal.

Yang WAJIB diisi saat produksi:
  - ANTHROPIC_API_KEY        : untuk AI (CV match, analisis video, CAPTCHA)
  - PUBLIC_BASE_URL          : domain portal MAXY, dipakai membentuk link asesmen
  - CREWING_* / WEBHOOK_*    : sumber data pendaftar dari crewing.lintasmaritim.com
  - Provider notifikasi (WA / Email) jika ingin kirim link otomatis
  - Provider transkripsi video (STT) jika ingin transcript otomatis
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Identitas & jaringan ---------------------------------------------------
APP_NAME        = "MAXY — Lintas Maritim"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5050").rstrip("/")
SECRET_KEY      = os.getenv("SECRET_KEY", "maxy-dev-secret-change-me")

# --- AI provider ------------------------------------------------------------
# AI_PROVIDER: auto | anthropic | gemini | openai
#   auto      : urutan prioritas → anthropic → gemini → openai → fallback heuristik
#   anthropic : paksa pakai Anthropic Claude
#   gemini    : paksa pakai Google Gemini
#   openai    : paksa pakai OpenAI GPT
AI_PROVIDER       = os.getenv("AI_PROVIDER", "auto")

# Anthropic (Claude)
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL        = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-sonnet-4-6")

# Google Gemini
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL        = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")

# OpenAI (GPT)
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# --- Penyimpanan ------------------------------------------------------------
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.getenv("DB_PATH", os.path.join(BASE_DIR, "maxy.db"))
STORAGE_DIR  = os.getenv("STORAGE_DIR", os.path.join(BASE_DIR, "storage"))
VIDEO_DIR      = os.path.join(STORAGE_DIR, "videos")
CONTRACT_DIR   = os.path.join(STORAGE_DIR, "contracts")
TEST_DIR       = os.path.join(STORAGE_DIR, "tests")
SCREENSHOT_DIR = os.path.join(STORAGE_DIR, "cert_screenshots")
for _d in (STORAGE_DIR, VIDEO_DIR, CONTRACT_DIR, TEST_DIR, SCREENSHOT_DIR):
    os.makedirs(_d, exist_ok=True)

# Retensi video (hari) — sesuai konsep "dihapus otomatis sesuai retensi"
VIDEO_RETENTION_DAYS = int(os.getenv("VIDEO_RETENTION_DAYS", "90"))

# --- Sumber data: crewing.lintasmaritim.com --------------------------------
# Dua cara mengambil data pendaftar:
#   1. PUSH  — crewing mengirim webhook ke /webhook/crewing (disarankan)
#   2. POLL  — MAXY menarik berkala dari admin/API crewing (butuh cookie/API key)
WEBHOOK_SECRET          = os.getenv("WEBHOOK_SECRET", "maxy-secret-2024")
CREWING_POLL_URL        = os.getenv("CREWING_POLL_URL", "")          # mis. https://crewing.lintasmaritim.com/api/applicants
CREWING_SESSION_COOKIE  = os.getenv("CREWING_SESSION_COOKIE", "")     # cookie admin (jika scraping/API butuh login)
CREWING_API_KEY         = os.getenv("CREWING_API_KEY", "")
CREWING_POLL_INTERVAL   = int(os.getenv("CREWING_POLL_INTERVAL", "300"))  # detik

# --- Verifikasi sertifikat (Kemenhub) --------------------------------------
ENABLE_CERT_VERIFY = _bool("ENABLE_CERT_VERIFY", False)  # True hanya jika server bisa akses pelaut.dephub.go.id

# --- Tes DISC/MBTI & IQ (back office) — platform EKSTERNAL ------------------
# DISC/MBTI dan IQ dikerjakan di platform pihak ketiga, BUKAN di portal MAXY.
# Dua mode:
#   "api"    : MAXY menarik hasil otomatis dari API provider tes (kandidat tidak
#              perlu mengunggah apa pun). Butuh TEST_PROVIDER_API_URL + TOKEN.
#   "upload" : (default) kandidat mengerjakan tes di tautan eksternal lalu
#              MENGUNGGAH berkas hasilnya ke MAXY untuk ditinjau HR.
TEST_RESULT_MODE      = os.getenv("TEST_RESULT_MODE", "upload")   # api | upload
TEST_PROVIDER         = os.getenv("TEST_PROVIDER", "none")        # none | generic
TEST_PROVIDER_API_URL = os.getenv("TEST_PROVIDER_API_URL", "")
TEST_PROVIDER_API_TOKEN = os.getenv("TEST_PROVIDER_API_TOKEN", "")
DISC_TEST_URL = os.getenv("DISC_TEST_URL", "https://www.123test.com/disc-personality-test/")
IQ_TEST_URL   = os.getenv("IQ_TEST_URL", "https://test.mensa.no/")

# --- Notifikasi: Email (SMTP) ----------------------------------------------
SMTP_HOST   = os.getenv("SMTP_HOST", "")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER", "")
SMTP_PASS   = os.getenv("SMTP_PASS", "")
SMTP_FROM   = os.getenv("SMTP_FROM", "rekrutmen@lintasmaritim.com")
SMTP_TLS    = _bool("SMTP_TLS", True)

# --- Notifikasi: WhatsApp ---------------------------------------------------
# Provider generik berbasis HTTP. Default mendukung pola Fonnte (populer di ID),
# tapi bisa diarahkan ke gateway apa pun lewat WA_API_URL + WA_API_TOKEN.
WA_PROVIDER  = os.getenv("WA_PROVIDER", "none")   # none | fonnte | generic
WA_API_URL   = os.getenv("WA_API_URL", "https://api.fonnte.com/send")
WA_API_TOKEN = os.getenv("WA_API_TOKEN", "")

# --- Transkripsi video (Speech-to-Text) ------------------------------------
# Claude tidak mentranskripsi audio; pakai provider STT eksternal lalu AI menilai teksnya.
#   openai  : OpenAI Whisper API   (OPENAI_API_KEY)
#   groq    : Groq Whisper API     (GROQ_API_KEY) — cepat & murah
#   local   : faster-whisper lokal (pip install faster-whisper)
#   stub    : transcript contoh (untuk demo/uji tanpa biaya)
STT_PROVIDER   = os.getenv("STT_PROVIDER", "stub")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
WHISPER_MODEL  = os.getenv("WHISPER_MODEL", "base")  # untuk provider local

# --- Identitas perusahaan (untuk kontrak) ----------------------------------
COMPANY_LEGAL_NAME = os.getenv("COMPANY_LEGAL_NAME", "PT Lintas Maritim Indonesia")
COMPANY_SHORT_NAME = os.getenv("COMPANY_SHORT_NAME", "Lintas Maritim Indonesia")
COMPANY_CITY       = os.getenv("COMPANY_CITY", "Jakarta")


def masked_summary() -> dict:
    """Ringkasan konfigurasi (tanpa membocorkan rahasia) untuk endpoint /health."""
    def has(v): return bool(v)
    return {
        "public_base_url": PUBLIC_BASE_URL,
        "claude_model": CLAUDE_MODEL,
        "ai_provider": AI_PROVIDER,
        "anthropic_key_set": has(ANTHROPIC_API_KEY),
        "gemini_key_set": has(GEMINI_API_KEY),
        "cert_verify_enabled": ENABLE_CERT_VERIFY,
        "test_result_mode": TEST_RESULT_MODE,
        "test_provider": TEST_PROVIDER,
        "crewing_poll_configured": has(CREWING_POLL_URL),
        "stt_provider": STT_PROVIDER,
        "wa_provider": WA_PROVIDER,
        "smtp_configured": has(SMTP_HOST),
        "video_retention_days": VIDEO_RETENTION_DAYS,
    }
