"""
cert_verifier.py
================
Verifikasi sertifikat pelaut otomatis ke pelaut.dephub.go.id/verifikasi
menggunakan Playwright (browser automation) + AI Vision (CAPTCHA solving).

Alur:
  1. Playwright buka halaman, ambil session cookie
  2. Download image CAPTCHA dengan session cookie yang sama
  3. Kirim ke AI Vision (Gemini / Claude) untuk dibaca teksnya
  4. Isi form: Nomor Kode Pelaut (10 digit) + Nomor Sertifikasi (full) + CAPTCHA
  5. Parsing hasil verifikasi → return dict terstruktur
  6. Cocokkan jenis sertifikat dari Kemenhub dengan judul di CV (fuzzy match)

Penggunaan:
  # Verifikasi satu nomor
  python cert_verifier.py --kode 6212316902MC3123

  # Verifikasi batch (list nomor saja)
  python cert_verifier.py --batch kode_pelaut_test.json --output hasil.json

  # Testing lengkap: trigger alur dari CV kandidat (judul + nomor)
  python cert_verifier.py --test-cv test_kandidat_sample.json

  # Test dengan data default (kode_pelaut_test.json)
  python cert_verifier.py --test-cv kode_pelaut_test.json --nama "Budi Santoso"
"""

import os
import re
import sys
import json
import time
import base64
import logging
import argparse
import requests
from io import BytesIO
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout
import anthropic

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("cert_verifier")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
AI_PROVIDER       = os.getenv("AI_PROVIDER", "auto")  # auto | gemini | anthropic
DEPHUB_URL        = "https://pelaut.dephub.go.id/verifikasi"
MAX_CAPTCHA_RETRY = 4          # maksimal percobaan CAPTCHA per sertifikat
HEADLESS          = True       # False → tampilkan browser (debug)

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None

try:
    import google.genai as _genai_lib
except ImportError:
    _genai_lib = None


def _active_vision_provider() -> str:
    """Pilih provider vision aktif untuk solve CAPTCHA."""
    p = AI_PROVIDER.strip().lower()
    if p == "gemini" and GEMINI_API_KEY:
        return "gemini"
    if p == "anthropic" and ANTHROPIC_API_KEY:
        return "anthropic"
    # auto: prefer Gemini
    if GEMINI_API_KEY and _genai_lib:
        return "gemini"
    if ANTHROPIC_API_KEY and _anthropic_lib:
        return "anthropic"
    return "none"

# Mapping status hasil ke bahasa Indonesia standar
STATUS_MAP = {
    "valid":      "Valid",
    "expired":    "Kadaluarsa",
    "not found":  "Tidak Ditemukan",
    "tidak ada":  "Tidak Ditemukan",
    "error":      "Error Jaringan",
}


def _launch_browser(pw):
    """
    Luncurkan browser dengan fallback chain:
      1. System Chrome  (channel='chrome')   — sudah di-whitelist AppControl
      2. System Edge    (channel='msedge')   — sudah di-whitelist AppControl
      3. Playwright Chromium bundled         — mungkin diblokir AppControl
    """
    LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
    channels = ["chrome", "msedge"]
    for ch in channels:
        try:
            browser = pw.chromium.launch(
                channel=ch,
                headless=HEADLESS,
                args=LAUNCH_ARGS,
            )
            log.info(f"Browser diluncurkan via channel='{ch}'")
            return browser
        except Exception as e:
            log.debug(f"channel='{ch}' tidak tersedia: {e}")
    log.info("Fallback ke Playwright bundled Chromium")
    return pw.chromium.launch(headless=HEADLESS, args=LAUNCH_ARGS)

# ---------------------------------------------------------------------------
# Kamus singkatan sertifikat maritim → judul lengkap
# ---------------------------------------------------------------------------
MARITIME_ACRONYMS = {
    "BST":    "BASIC SAFETY TRAINING",
    "SCRB":   "PROFICIENCY IN SURVIVAL CRAFT AND RESCUE BOAT",
    "AFF":    "ADVANCED FIRE FIGHTING",
    "PSCRB":  "PROFICIENCY IN SURVIVAL CRAFT AND RESCUE BOAT",
    "GMDSS":  "GLOBAL MARITIME DISTRESS AND SAFETY SYSTEM",
    "SDSD":   "SECURITY AWARENESS TRAINING FOR SEAFARERS",
    "PFSO":   "PORT FACILITY SECURITY OFFICER",
    "SSO":    "SHIP SECURITY OFFICER",
    "CSO":    "COMPANY SECURITY OFFICER",
    "MEFA":   "MEDICAL FIRST AID",
    "MCare":  "MEDICAL CARE",
    "ECDIS":  "ELECTRONIC CHART DISPLAY AND INFORMATION SYSTEM",
    "BRM":    "BRIDGE RESOURCE MANAGEMENT",
    "ERM":    "ENGINE ROOM RESOURCE MANAGEMENT",
    "HELM":   "HIGH EFFICIENCY LIQUID MANAGEMENT",
    "RADW":   "RATING AS ABLE DECK WATCH",
    "RASEW":  "RATING AS ABLE SEAFARER ENGINE WATCH",
    "RATEW":  "RATING AS ABLE SEAFARER ENGINE WATCH",
    "RATDW":  "RATING AS ABLE DECK WATCH",
}


# ===========================================================================
# CAPTCHA Solver (AI Vision)
# ===========================================================================

def solve_captcha_with_ai(captcha_image_bytes: bytes, attempt: int = 1) -> str:
    """
    Kirim gambar CAPTCHA ke AI Vision (Gemini atau Claude), dapatkan teks CAPTCHA.
    Mengembalikan string teks CAPTCHA (contoh: 'A3f7Xt').
    """
    hints = [
        "Baca teks di gambar CAPTCHA ini dengan tepat. Balas HANYA teks CAPTCHA-nya saja (huruf dan angka), tanpa spasi, tanpa penjelasan.",
        "Perhatikan baik-baik setiap karakter. Teks CAPTCHA mungkin campuran huruf kapital, huruf kecil, dan angka. Balas HANYA teks CAPTCHA-nya.",
        "Gambar ini adalah CAPTCHA keamanan. Tulis ulang semua karakter yang terlihat tepat seperti di gambar. Balas HANYA teks CAPTCHA tersebut.",
        "Fokus pada karakter alfanumerik di gambar ini. Balas dengan HANYA teks yang terlihat di CAPTCHA, case-sensitive.",
    ]
    prompt = hints[min(attempt - 1, len(hints) - 1)]
    img_b64 = base64.b64encode(captcha_image_bytes).decode()

    provider = _active_vision_provider()
    raw = ""

    if provider == "gemini":
        try:
            import PIL.Image
            from io import BytesIO as _BytesIO
            client = _genai_lib.Client(api_key=GEMINI_API_KEY)
            img = PIL.Image.open(_BytesIO(captcha_image_bytes))
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[prompt, img],
            )
            raw = resp.text.strip()
        except Exception as e:
            log.warning(f"  Gemini Vision CAPTCHA gagal: {e}")

    elif provider == "anthropic":
        try:
            client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=30,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": prompt}
                ]}]
            )
            raw = resp.content[0].text.strip()
        except Exception as e:
            log.warning(f"  Claude Vision CAPTCHA gagal: {e}")

    else:
        log.error("Tidak ada AI Vision provider yang tersedia untuk solve CAPTCHA.")
        return ""

    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw)[:10]
    log.info(f"  {provider.upper()} CAPTCHA (attempt {attempt}): raw='{raw}' → cleaned='{cleaned}'")
    return cleaned


# Alias untuk backward compatibility
def solve_captcha_with_claude(captcha_image_bytes: bytes, attempt: int = 1) -> str:
    return solve_captcha_with_ai(captcha_image_bytes, attempt)


# ===========================================================================
# Fuzzy Title Matching
# ===========================================================================

def _expand_acronym(text: str) -> str:
    """Ekspansi singkatan ke judul lengkap jika diketahui."""
    upper = text.strip().upper()
    return MARITIME_ACRONYMS.get(upper, text)


def _normalize_title(title: str) -> list:
    """Normalisasi judul: uppercase, hapus tanda baca, pisah token."""
    return re.sub(r"[^A-Z0-9 ]", " ", title.upper()).split()


def match_cert_title(cv_title: str, kemenhub_title: str) -> dict:
    """
    Bandingkan judul sertifikat dari CV dengan yang dikembalikan Kemenhub.

    Strategi (dari paling kuat ke paling lemah):
      1. Exact match (setelah normalisasi) → skor 1.0
      2. Salah satu adalah singkatan dari yang lain → skor 0.95
      3. Token overlap ≥ 60%  → skor proporsional
      4. Substring match → skor 0.7
      5. Tidak cocok → skor < 0.5

    Returns:
      {
        "match": bool | None,   # True jika cocok, False jika tidak, None jika tidak bisa dinilai
        "score": float,         # 0.0–1.0
        "cv_title": str,
        "kemenhub_title": str,
        "method": str,          # cara pencocokan yang berhasil
        "note": str,
      }
    """
    result = {
        "match": False,
        "score": 0.0,
        "cv_title": cv_title,
        "kemenhub_title": kemenhub_title or "",
        "method": "none",
        "note": "",
    }

    if not kemenhub_title:
        result["note"] = "Kemenhub tidak mengembalikan jenis sertifikat"
        result["match"] = None  # tidak bisa dinilai
        return result

    # Coba ekspansi singkatan untuk kedua sisi
    cv_expanded   = _expand_acronym(cv_title)
    kem_expanded  = _expand_acronym(kemenhub_title)

    cv_tokens  = _normalize_title(cv_expanded)
    kem_tokens = _normalize_title(kem_expanded)

    cv_str  = " ".join(cv_tokens)
    kem_str = " ".join(kem_tokens)

    # 1. Exact match
    if cv_str == kem_str:
        result.update(score=1.0, match=True, method="exact",
                      note="Judul identik")
        return result

    # 2. Singkatan → full (setelah ekspansi sudah sama)
    cv_exp_tokens = _normalize_title(_expand_acronym(cv_title))
    if " ".join(cv_exp_tokens) == kem_str:
        result.update(score=0.95, match=True, method="acronym_expansion",
                      note=f"'{cv_title}' dikenali sebagai '{kemenhub_title}'")
        return result

    kem_exp_tokens = _normalize_title(_expand_acronym(kemenhub_title))
    if cv_str == " ".join(kem_exp_tokens):
        result.update(score=0.95, match=True, method="acronym_expansion",
                      note=f"'{kemenhub_title}' dikenali sebagai '{cv_title}'")
        return result

    # 3. Token overlap
    cv_set  = set(cv_tokens)
    kem_set = set(kem_tokens)
    if cv_set and kem_set:
        intersection = cv_set & kem_set
        # Jaccard-like: intersection / union, tapi dengan bias ke sisi yang lebih pendek
        overlap_ratio = len(intersection) / min(len(cv_set), len(kem_set))
        if overlap_ratio >= 0.6:
            result.update(score=round(overlap_ratio * 0.9, 3), match=True,
                          method="token_overlap",
                          note=f"Token overlap {overlap_ratio:.0%}: {', '.join(intersection)}")
            return result
        elif overlap_ratio >= 0.3:
            result.update(score=round(overlap_ratio * 0.9, 3), match=False,
                          method="token_overlap_partial",
                          note=f"Overlap rendah ({overlap_ratio:.0%}), perlu periksa manual")
            return result

    # 4. Substring match
    if cv_str in kem_str or kem_str in cv_str:
        score = 0.75 if len(cv_str) > 4 and len(kem_str) > 4 else 0.6
        result.update(score=score, match=True, method="substring",
                      note="Salah satu adalah bagian dari yang lain")
        return result

    # 5. Tidak cocok
    result.update(score=0.0, match=False, method="no_match",
                  note="Judul tidak cocok — periksa manual")
    return result


# ===========================================================================
# Playwright - Verifikasi ke Kemenhub
# ===========================================================================

def _extract_kode_and_cert(nomor_sertifikat: str) -> tuple:
    """
    Pisah nomor sertifikat menjadi:
      - kode_pelaut   : 10 digit pertama (alfanumerik)
      - nomor_full    : nomor lengkap apa adanya (untuk field Nomor Sertifikasi)

    Contoh: "6212316902MC3123" → ("6212316902", "6212316902MC3123")
    Contoh: "6212316902"       → ("6212316902", "6212316902")
    """
    alnum = re.sub(r"[^A-Za-z0-9]", "", nomor_sertifikat)
    kode_pelaut = alnum[:10]
    return kode_pelaut, nomor_sertifikat.strip()


def verify_seafarer_cert(nomor_sertifikat: str, page: Page, context: BrowserContext) -> dict:
    """
    Verifikasi satu sertifikat pelaut di pelaut.dephub.go.id.

    Mengisi KEDUA field form Kemenhub:
      - Field 1: Nomor Kode Pelaut  = 10 digit pertama dari nomor_sertifikat
      - Field 2: Nomor Sertifikasi  = nomor lengkap

    Returns dict:
    {
        "kode_pelaut": str,              # 10 digit
        "nomor_sertifikat_input": str,   # input asli
        "status": "Valid" | "Kadaluarsa" | "Tidak Ditemukan" | "Error Jaringan",
        "nama": str | None,
        "nomor_sertifikat": str | None,  # dari hasil Kemenhub
        "jenis_sertifikat": str | None,
        "tanggal_terbit": str | None,
        "tanggal_berlaku": str | None,
        "verified_at": str (ISO 8601),
        "screenshot_base64": str | None,
        "raw_html": str | None,
    }
    """
    kode_pelaut, nomor_full = _extract_kode_and_cert(nomor_sertifikat)

    result = {
        "kode_pelaut": kode_pelaut,
        "nomor_sertifikat_input": nomor_full,
        "status": "Error Jaringan",
        "nama": None,
        "nomor_sertifikat": None,
        "jenis_sertifikat": None,
        "tanggal_terbit": None,
        "tanggal_berlaku": None,
        "verified_at": datetime.now().isoformat(),
        "screenshot_base64": None,
        "raw_html": None,
    }

    for attempt in range(1, MAX_CAPTCHA_RETRY + 1):
        try:
            log.info(f"[{nomor_full}] Attempt {attempt}/{MAX_CAPTCHA_RETRY} — navigating...")
            page.goto(DEPHUB_URL, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(1500)

            # ---- 1. Isi field Nomor Kode Pelaut (10 digit) ----
            kode_selectors = [
                "input[name*='kode']", "input[name*='pelaut']", "input[name*='seaman']",
                "input[placeholder*='Kode']", "input[placeholder*='Kode Pelaut']",
                "#kode_pelaut", "#nomor_pelaut", "#seaman_number",
            ]
            kode_input = _find_input(page, kode_selectors)
            if kode_input:
                kode_input.click()
                kode_input.fill(kode_pelaut)
                log.info(f"[{nomor_full}] Field 'Kode Pelaut' diisi: {kode_pelaut}")
            else:
                log.warning(f"[{nomor_full}] Field 'Kode Pelaut' tidak ditemukan, coba field generic...")

            # ---- 2. Isi field Nomor Sertifikasi (full) ----
            sertifikasi_selectors = [
                "input[name*='sertifik']", "input[name*='certificate']",
                "input[placeholder*='Sertifikasi']", "input[placeholder*='Sertifikat']",
                "#nomor_sertifikasi", "#no_sertifikat",
            ]
            sert_input = _find_input(page, sertifikasi_selectors)
            if sert_input:
                sert_input.click()
                sert_input.fill(nomor_full)
                log.info(f"[{nomor_full}] Field 'Nomor Sertifikasi' diisi: {nomor_full}")
            else:
                # Fallback: kalau hanya satu field ditemukan, isi dengan nomor full
                if not kode_input:
                    generic_selectors = [
                        "input[name*='nomor']", "input[placeholder*='Nomor']", "form input[type='text']",
                    ]
                    fallback = _find_input(page, generic_selectors)
                    if fallback:
                        fallback.click()
                        fallback.fill(nomor_full)
                        log.info(f"[{nomor_full}] Fallback generic field diisi: {nomor_full}")
                    else:
                        log.warning(f"[{nomor_full}] Tidak ada input field yang ditemukan, dump HTML...")
                        result["raw_html"] = page.content()[:3000]
                        result["status"] = "Error Jaringan"
                        break
                else:
                    log.warning(f"[{nomor_full}] Field 'Nomor Sertifikasi' tidak ditemukan, lanjut dengan kode saja...")

            # ---- 3. Ambil CAPTCHA ----
            captcha_img_el = _find_element(page, [
                "img[src*='captcha']",
                "img[src*='security']",
                "img[src*='kode']",
                ".captcha img",
                "#captcha img",
                "form img",
            ])
            if not captcha_img_el:
                log.warning(f"[{nomor_full}] CAPTCHA tidak ditemukan")
                result["status"] = "Error Jaringan"
                break

            captcha_src = captcha_img_el.get_attribute("src") or ""
            if captcha_src.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(DEPHUB_URL)
                captcha_src = f"{parsed.scheme}://{parsed.netloc}{captcha_src}"

            log.info(f"[{nomor_full}] CAPTCHA URL: {captcha_src}")

            # Ambil cookies Playwright untuk dikirim ke requests
            pw_cookies = context.cookies()
            session_cookies = {c["name"]: c["value"] for c in pw_cookies}

            # Download CAPTCHA image
            img_resp = requests.get(
                captcha_src,
                cookies=session_cookies,
                timeout=10,
                headers={"Referer": DEPHUB_URL}
            )
            if img_resp.status_code != 200:
                log.warning(f"[{nomor_full}] Gagal download CAPTCHA (HTTP {img_resp.status_code})")
                continue

            captcha_bytes = img_resp.content

            # ---- 4. Solve CAPTCHA via AI Vision ----
            captcha_text = solve_captcha_with_ai(captcha_bytes, attempt)
            if not captcha_text:
                log.warning(f"[{nomor_full}] AI gagal baca CAPTCHA")
                continue

            # ---- 5. Isi field CAPTCHA ----
            captcha_input = _find_input(page, [
                "input[name*='captcha']",
                "input[name*='security']",
                "input[name*='kode_keamanan']",
                "input[placeholder*='CAPTCHA']",
                "input[placeholder*='Captcha']",
                "input[placeholder*='kode keamanan']",
                "#captcha_input",
                "#security_code",
                ".captcha-input",
            ])
            if not captcha_input:
                log.warning(f"[{nomor_full}] CAPTCHA input field tidak ditemukan")
                result["status"] = "Error Jaringan"
                break

            captcha_input.click()
            captcha_input.fill(captcha_text)
            log.info(f"[{nomor_full}] CAPTCHA diisi: '{captcha_text}'")

            # ---- 6. Submit form ----
            submit_btn = _find_element(page, [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Verifikasi')",
                "button:has-text('Cek')",
                "button:has-text('Submit')",
                ".btn-verifikasi",
                "#btn-submit",
            ])
            if submit_btn:
                submit_btn.click()
            else:
                captcha_input.press("Enter")

            page.wait_for_load_state("networkidle", timeout=20_000)
            page.wait_for_timeout(2000)

            # ---- 7. Cek apakah CAPTCHA salah ----
            page_text_lower = page.inner_text("body").lower()
            if any(w in page_text_lower for w in ["captcha salah", "kode tidak valid", "wrong captcha", "invalid captcha"]):
                log.warning(f"[{nomor_full}] CAPTCHA salah, retry...")
                continue  # retry dengan CAPTCHA baru

            # ---- 8. Parse hasil ----
            result = _parse_result(page, nomor_full, result)

            # Screenshot hasil
            try:
                ss_bytes = page.screenshot(full_page=False)
                result["screenshot_base64"] = base64.b64encode(ss_bytes).decode()
            except Exception:
                pass

            log.info(f"[{nomor_full}] Status: {result['status']}")
            return result

        except PWTimeout:
            log.error(f"[{nomor_full}] Timeout attempt {attempt}")
            result["status"] = "Error Jaringan"
            if attempt == MAX_CAPTCHA_RETRY:
                break
            page.wait_for_timeout(2000)
            continue

        except Exception as e:
            log.error(f"[{nomor_full}] Error attempt {attempt}: {e}")
            result["status"] = "Error Jaringan"
            if attempt == MAX_CAPTCHA_RETRY:
                break
            continue

    result["verified_at"] = datetime.now().isoformat()
    return result


def _parse_result(page: Page, nomor: str, base_result: dict) -> dict:
    """Parse HTML hasil verifikasi Kemenhub menjadi dict terstruktur."""
    r = dict(base_result)
    body_text = page.inner_text("body").lower()

    # ----- Deteksi status -----
    if any(w in body_text for w in ["tidak ditemukan", "data tidak ada", "not found", "no data"]):
        r["status"] = "Tidak Ditemukan"
        return r

    if any(w in body_text for w in ["kadaluarsa", "expired", "sudah habis", "tidak berlaku"]):
        r["status"] = "Kadaluarsa"
    elif any(w in body_text for w in ["valid", "aktif", "berlaku", "masih berlaku"]):
        r["status"] = "Valid"
    else:
        # Jika ada tabel hasil → anggap Valid
        tables = page.query_selector_all("table")
        r["status"] = "Valid" if tables else "Tidak Ditemukan"

    # ----- Extract data dari tabel -----
    try:
        rows = page.query_selector_all("table tr, .result-row, .verifikasi-result tr")
        data_map = {}
        for row in rows:
            cells = row.query_selector_all("td, th")
            if len(cells) >= 2:
                key   = cells[0].inner_text().strip().lower()
                value = cells[1].inner_text().strip()
                data_map[key] = value

        # Mapping field → key yang mungkin muncul di tabel Kemenhub
        FIELD_KEYS = {
            "nama":             ["nama", "name", "nama pelaut", "nama lengkap"],
            "nomor_sertifikat": ["nomor sertifikat", "no sertifikat", "cert no", "nomor", "no."],
            "jenis_sertifikat": ["jenis sertifikat", "type", "jenis", "certificate type", "nama sertifikat"],
            "tanggal_terbit":   ["tanggal terbit", "issued", "issue date", "tgl terbit"],
            "tanggal_berlaku":  ["berlaku sampai", "valid until", "expire", "kadaluarsa", "tgl berlaku"],
        }
        for field, possible_keys in FIELD_KEYS.items():
            for k in possible_keys:
                if k in data_map:
                    r[field] = data_map[k]
                    break

    except Exception as e:
        log.warning(f"[{nomor}] Parse tabel gagal: {e}")

    # Simpan raw HTML untuk debug
    r["raw_html"] = page.content()[:5000]
    return r


def _find_input(page: Page, selectors: list):
    """Coba daftar selector, kembalikan elemen pertama yang ditemukan."""
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


def _find_element(page: Page, selectors: list):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


# ===========================================================================
# Verifikasi Semua Sertifikat Satu Kandidat
# ===========================================================================

def verify_candidate_certs(
    sertifikat_list: list,
    nama_kandidat: str = "",
    kode_pelaut_override: str = "",
) -> dict:
    """
    Verifikasi seluruh sertifikat satu kandidat dan cocokkan judulnya.

    Args:
        sertifikat_list: list of {"judul": str, "nomor_sertifikat": str}
        nama_kandidat:   nama untuk logging/laporan
        kode_pelaut_override: opsional override kode pelaut (10 digit)

    Returns:
        {
          "kandidat": str,
          "total": int,
          "lulus": int,
          "gagal": int,
          "perlu_periksa": int,
          "ringkasan": "LULUS" | "GAGAL" | "PERLU PERIKSA",
          "detail": [...],
          "verified_at": str,
        }
    """
    log.info(f"\nMulai verifikasi {len(sertifikat_list)} sertifikat untuk: {nama_kandidat or '(tanpa nama)'}")
    detail = []

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="id-ID",
        )
        page = context.new_page()

        for i, sert in enumerate(sertifikat_list, 1):
            judul_cv   = (sert.get("judul") or sert.get("title") or "").strip()
            nomor      = (sert.get("nomor_sertifikat") or sert.get("nomor") or "").strip()

            log.info(f"\n{'='*55}")
            log.info(f"[{i}/{len(sertifikat_list)}] {judul_cv} — {nomor}")

            if not nomor:
                detail.append({
                    "index": i,
                    "judul_cv": judul_cv,
                    "nomor_sertifikat": nomor,
                    "kode_pelaut": "",
                    "status_kemenhub": "Error Jaringan",
                    "jenis_kemenhub": None,
                    "nama_pemegang": None,
                    "berlaku_sampai": None,
                    "title_match": match_cert_title(judul_cv, ""),
                    "hasil": "GAGAL",
                    "alasan": "Nomor sertifikat tidak tersedia",
                })
                continue

            r = verify_seafarer_cert(nomor, page, context)

            # Pencocokan judul (hanya jika Kemenhub return jenis sertifikat)
            title_match = match_cert_title(judul_cv, r.get("jenis_sertifikat") or "")

            # Tentukan hasil akhir sertifikat ini
            if r["status"] == "Tidak Ditemukan":
                hasil   = "GAGAL"
                alasan  = "Sertifikat tidak ditemukan di database Kemenhub"
            elif r["status"] == "Kadaluarsa":
                hasil   = "GAGAL"
                alasan  = f"Sertifikat kadaluarsa (berlaku s.d. {r.get('tanggal_berlaku') or '?'})"
            elif r["status"] == "Error Jaringan":
                hasil   = "PERLU PERIKSA"
                alasan  = "Error saat mengakses Kemenhub — coba ulang secara manual"
            elif title_match["match"] is None:
                # Valid tapi Kemenhub tidak return jenis sertifikat
                hasil   = "LULUS*"
                alasan  = "Sertifikat VALID di Kemenhub (judul tidak dapat diverifikasi otomatis)"
            elif title_match["match"] is True:
                hasil   = "LULUS"
                alasan  = f"Sertifikat VALID + judul cocok ({title_match['method']})"
            else:
                hasil   = "PERLU PERIKSA"
                alasan  = (f"Sertifikat VALID tapi judul tidak cocok: "
                           f"CV='{judul_cv}' vs Kemenhub='{r.get('jenis_sertifikat')}'")

            detail.append({
                "index": i,
                "judul_cv": judul_cv,
                "nomor_sertifikat": nomor,
                "kode_pelaut": r["kode_pelaut"],
                "status_kemenhub": r["status"],
                "jenis_kemenhub": r.get("jenis_sertifikat"),
                "nama_pemegang": r.get("nama"),
                "berlaku_sampai": r.get("tanggal_berlaku"),
                "title_match": title_match,
                "hasil": hasil,
                "alasan": alasan,
            })

            if i < len(sertifikat_list):
                time.sleep(2)  # jeda antar request

        browser.close()

    # Hitung ringkasan
    lulus   = sum(1 for d in detail if d["hasil"] in ("LULUS", "LULUS*"))
    gagal   = sum(1 for d in detail if d["hasil"] == "GAGAL")
    periksa = sum(1 for d in detail if d["hasil"] == "PERLU PERIKSA")

    ringkasan = "LULUS" if gagal == 0 and periksa == 0 else \
                "PERLU PERIKSA" if gagal == 0 else "GAGAL"

    return {
        "kandidat": nama_kandidat,
        "total": len(sertifikat_list),
        "lulus": lulus,
        "gagal": gagal,
        "perlu_periksa": periksa,
        "ringkasan": ringkasan,
        "detail": detail,
        "verified_at": datetime.now().isoformat(),
    }


# ===========================================================================
# Batch Verification (nomor saja, tanpa validasi judul)
# ===========================================================================

def verify_batch(kode_list: list, output_file: str = "hasil_verifikasi.json") -> list:
    """
    Verifikasi daftar nomor sertifikat sekaligus dalam satu sesi browser.
    Lebih efisien daripada buka browser tiap nomor.
    """
    results = []
    log.info(f"Mulai verifikasi batch: {len(kode_list)} nomor sertifikat")

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="id-ID",
        )
        page = context.new_page()

        for i, kode in enumerate(kode_list, 1):
            log.info(f"\n{'='*50}")
            log.info(f"[{i}/{len(kode_list)}] Memverifikasi: {kode}")
            r = verify_seafarer_cert(kode.strip(), page, context)
            results.append(r)
            _print_result(r)
            # Jeda antar request (hindari rate limit)
            if i < len(kode_list):
                time.sleep(2)

        browser.close()

    # Simpan hasil
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log.info(f"\nHasil disimpan ke: {output_file}")
    return results


def verify_single(nomor_sertifikat: str) -> dict:
    """Verifikasi satu nomor sertifikat (convenience function)."""
    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="id-ID",
        )
        page = context.new_page()
        result = verify_seafarer_cert(nomor_sertifikat, page, context)
        browser.close()
    return result


# ===========================================================================
# Scraper: crewing.lintasmaritim.com
# ===========================================================================

def fetch_new_candidates_from_crewing(
    admin_url: str = "https://crewing.lintasmaritim.com/admin/applicants",
    admin_session_cookie: str = None,
    since_id: int = 0
) -> list:
    """
    Ambil data kandidat baru dari admin panel crewing.lintasmaritim.com.
    """
    if not admin_session_cookie:
        log.warning("admin_session_cookie tidak diset — data tidak bisa diambil otomatis.")
        return []

    headers = {
        "Cookie": admin_session_cookie,
        "Accept": "application/json",
        "User-Agent": "MAXY-Integration/1.0",
    }

    try:
        api_url = f"{admin_url}?format=json&since_id={since_id}"
        resp = requests.get(api_url, headers=headers, timeout=15)

        if resp.status_code == 200 and "application/json" in resp.headers.get("Content-Type", ""):
            data = resp.json()
            candidates = data.get("data", data.get("candidates", []))
            log.info(f"Fetched {len(candidates)} kandidat baru dari crewing.lintasmaritim.com")
            return candidates
        else:
            log.warning(f"API JSON tidak tersedia (HTTP {resp.status_code}), perlu scraping HTML")
            return []

    except Exception as e:
        log.error(f"Gagal fetch dari crewing.lintasmaritim.com: {e}")
        return []


# ===========================================================================
# MAXY Integration: Alur Lengkap
# ===========================================================================

def process_new_candidate(candidate: dict) -> dict:
    """
    Proses satu kandidat baru dari crewing.lintasmaritim.com:
    1. Verifikasi sertifikat Kemenhub (dengan validasi judul jika ada)
    2. Return enriched candidate data
    """
    # Support dua format: daftar sertifikat lengkap, atau kode tunggal
    sertifikat_list = candidate.get("sertifikat") or []
    if isinstance(sertifikat_list, str):
        sertifikat_list = [{"judul": sertifikat_list, "nomor_sertifikat": candidate.get("kode_pelaut", "")}]

    kode = candidate.get("kode_pelaut") or candidate.get("seaman_code") or ""

    if not sertifikat_list and not kode:
        log.warning(f"Kandidat {candidate.get('nama','?')} tidak punya kode/sertifikat, skip verifikasi")
        candidate["cert_verification"] = {"status": "Tidak Ada Kode", "kode_pelaut": None}
        return candidate

    if not sertifikat_list and kode:
        sertifikat_list = [{"judul": "", "nomor_sertifikat": kode}]

    log.info(f"Verifikasi {len(sertifikat_list)} sertifikat untuk: {candidate.get('nama','?')}")

    laporan = verify_candidate_certs(
        sertifikat_list,
        nama_kandidat=candidate.get("nama") or candidate.get("nama_lengkap") or "",
    )

    candidate["cert_verification"] = laporan
    return candidate


def run_auto_pipeline(since_id: int = 0) -> list:
    """
    Pipeline otomatis lengkap:
    1. Fetch kandidat baru dari crewing.lintasmaritim.com
    2. Verifikasi sertifikat masing-masing di Kemenhub
    3. Return hasil lengkap (siap dikirim ke MAXY backend / database)
    """
    session_cookie = os.getenv("CREWING_SESSION_COOKIE", "")
    candidates = fetch_new_candidates_from_crewing(
        admin_session_cookie=session_cookie or None,
        since_id=since_id
    )

    if not candidates:
        log.info("Tidak ada kandidat baru.")
        return []

    results = []
    for c in candidates:
        enriched = process_new_candidate(c)
        results.append(enriched)

    return results


# ===========================================================================
# Webhook Receiver (untuk dipanggil dari maxy_server.py)
# ===========================================================================

def receive_webhook_candidate(payload: dict) -> dict:
    """
    Dipanggil ketika crewing.lintasmaritim.com mengirim webhook ke MAXY server.

    Field yang diharapkan:
    {
        "nama_lengkap": "...",
        "kode_pelaut": "...",
        "sertifikat": [
            {"judul": "BST", "nomor_sertifikat": "6212316902MC3123"},
            ...
        ],
        ...
    }
    """
    log.info(f"Webhook diterima: {payload.get('nama_lengkap','?')} ({payload.get('kode_pelaut','no-kode')})")

    candidate = {
        "nama":          payload.get("nama_lengkap") or payload.get("nama"),
        "email":         payload.get("email"),
        "phone":         payload.get("no_telepon") or payload.get("phone"),
        "kode_pelaut":   payload.get("kode_pelaut") or payload.get("seaman_code"),
        "jabatan":       payload.get("jabatan_terakhir"),
        "jenis_kapal":   payload.get("jenis_kapal"),
        "posisi_dilamar":payload.get("posisi_dilamar"),
        "sertifikat":    payload.get("sertifikat") or [],
        "cv_url":        payload.get("cv_url"),
        "applied_at":    datetime.now().isoformat(),
        "source":        "crewing.lintasmaritim.com",
    }

    enriched = process_new_candidate(candidate)
    return enriched


# ===========================================================================
# Utilities / Printing
# ===========================================================================

def _print_result(r: dict):
    """Print hasil verifikasi satu sertifikat (mode batch sederhana)."""
    status_icon = {"Valid": "✓", "Kadaluarsa": "⚠", "Tidak Ditemukan": "✗", "Error Jaringan": "?"}.get(r["status"], "?")
    nomor_display = r.get("nomor_sertifikat_input") or r.get("kode_pelaut", "")
    print(f"\n  {status_icon} {nomor_display:25s} → {r['status']}")
    if r.get("nama"):            print(f"    Nama            : {r['nama']}")
    if r.get("nomor_sertifikat"):print(f"    No. Sertifikat  : {r['nomor_sertifikat']}")
    if r.get("jenis_sertifikat"):print(f"    Jenis Sertifikat: {r['jenis_sertifikat']}")
    if r.get("tanggal_berlaku"): print(f"    Berlaku Sampai  : {r['tanggal_berlaku']}")


def _print_laporan(laporan: dict):
    """Cetak laporan lengkap screening sertifikat kandidat."""
    W = 60
    ICONS = {"LULUS": "✓", "LULUS*": "✓", "GAGAL": "✗", "PERLU PERIKSA": "⚠"}

    print("\n" + "=" * W)
    print(" LAPORAN SCREENING SERTIFIKAT")
    print("=" * W)
    print(f" Kandidat : {laporan.get('kandidat') or '(tanpa nama)'}")
    print(f" Total    : {laporan['total']} sertifikat")
    print(f" Lulus    : {laporan['lulus']}  |  Gagal: {laporan['gagal']}  |  Periksa: {laporan.get('perlu_periksa', 0)}")
    print("=" * W)

    for d in laporan["detail"]:
        icon = ICONS.get(d["hasil"], "?")
        print(f"\n  {icon} [{d['index']}] {d['judul_cv'] or '(tanpa judul)'}")
        print(f"    Nomor     : {d['nomor_sertifikat']}")
        print(f"    Kode      : {d['kode_pelaut']}")
        print(f"    Kemenhub  : {d['status_kemenhub']}", end="")
        if d.get("jenis_kemenhub"):
            print(f" — {d['jenis_kemenhub']}", end="")
        print()
        if d.get("berlaku_sampai"):
            print(f"    Berlaku   : {d['berlaku_sampai']}")
        if d.get("nama_pemegang"):
            print(f"    Atas nama : {d['nama_pemegang']}")

        tm = d.get("title_match", {})
        if tm.get("match") is True:
            print(f"    Judul     : ✓ COCOK ({tm.get('method', '')})")
        elif tm.get("match") is False:
            print(f"    Judul     : ✗ TIDAK COCOK — {tm.get('note', '')}")
        elif tm.get("match") is None:
            print(f"    Judul     : — Tidak dapat diverifikasi otomatis")

        print(f"    ▶ Hasil   : {d['hasil']} — {d['alasan']}")

    print("\n" + "=" * W)
    ring = laporan["ringkasan"]
    ring_icon = ICONS.get(ring, "?")
    print(f" HASIL AKHIR: {ring_icon} {ring}")
    print(f" ({laporan['lulus']}/{laporan['total']} sertifikat lolos)")
    print("=" * W + "\n")


# ===========================================================================
# CLI Entry Point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="MAXY — Verifikasi Sertifikat Pelaut Otomatis (Kemenhub)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--kode",     "-k", help="Satu nomor sertifikat lengkap (contoh: 6212316902MC3123)")
    group.add_argument("--batch",    "-b", help="File JSON berisi daftar nomor sertifikat")
    group.add_argument("--test-cv",  "-t", help=(
        "Testing alur CV: file JSON [{judul, nomor_sertifikat}] atau kandidat lengkap. "
        "Verifikasi semua sertifikat + cocokkan judulnya."
    ))
    group.add_argument("--auto",     "-a", action="store_true",
                       help="Mode otomatis: fetch dari crewing.lintasmaritim.com + verifikasi")

    parser.add_argument("--output",   "-o", default="hasil_verifikasi.json",
                        help="File output JSON (default: hasil_verifikasi.json)")
    parser.add_argument("--nama",     "-n", default="",
                        help="Nama kandidat untuk laporan (mode --test-cv)")
    parser.add_argument("--show-browser", action="store_true",
                        help="Tampilkan browser (untuk debug)")
    parser.add_argument("--since-id", type=int, default=0,
                        help="Ambil kandidat dengan ID lebih dari nilai ini (mode --auto)")
    args = parser.parse_args()

    global HEADLESS
    HEADLESS = not args.show_browser

    if not ANTHROPIC_API_KEY and not GEMINI_API_KEY:
        print("ERROR: Tidak ada AI API key yang diset.")
        print("       Set GEMINI_API_KEY atau ANTHROPIC_API_KEY di .env")
        sys.exit(1)

    if _active_vision_provider() == "none":
        print("ERROR: Library AI Vision tidak tersedia.")
        print("       Jalankan: pip install google-generativeai  atau  pip install anthropic")
        sys.exit(1)

    print(f"  Menggunakan AI Vision: {_active_vision_provider().upper()}")

    # ---- Single ----
    if args.kode:
        result = verify_single(args.kode)
        _print_result(result)
        out = {"results": [result]}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nDisimpan ke: {args.output}")

    # ---- Batch (nomor saja) ----
    elif args.batch:
        with open(args.batch, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list) and data and isinstance(data[0], str):
            kode_list = data
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            kode_list = [d.get("nomor_sertifikat") or d.get("kode_pelaut") or d.get("nomor", "") for d in data]
        else:
            kode_list = data.get("kode_list", [])

        verify_batch([k for k in kode_list if k], args.output)

    # ---- Test CV: alur lengkap dengan validasi judul ----
    elif args.test_cv:
        with open(args.test_cv, encoding="utf-8") as f:
            data = json.load(f)

        nama = args.nama

        if isinstance(data, dict):
            nama = nama or data.get("nama_lengkap") or data.get("nama") or ""
            sertifikat_list = data.get("sertifikat") or []
            if not sertifikat_list:
                kode = data.get("kode_pelaut") or data.get("seaman_code") or ""
                if kode:
                    sertifikat_list = [{"judul": "", "nomor_sertifikat": kode}]
        elif isinstance(data, list) and data and isinstance(data[0], dict) and (
            "judul" in data[0] or "nomor_sertifikat" in data[0] or "nomor" in data[0]
        ):
            sertifikat_list = data
        elif isinstance(data, list) and data and isinstance(data[0], str):
            sertifikat_list = [{"judul": "", "nomor_sertifikat": k} for k in data]
        else:
            print("ERROR: Format file tidak dikenali.")
            sys.exit(1)

        if not sertifikat_list:
            print("ERROR: Tidak ada sertifikat yang bisa diverifikasi.")
            sys.exit(1)

        print(f"\n  Kandidat : {nama or '(tanpa nama)'}")
        print(f"  Sertifikat: {len(sertifikat_list)} item")
        print(f"  Output   : {args.output}\n")

        laporan = verify_candidate_certs(sertifikat_list, nama_kandidat=nama)
        _print_laporan(laporan)

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(laporan, f, ensure_ascii=False, indent=2)
        print(f"Laporan tersimpan ke: {args.output}")

    # ---- Auto pipeline ----
    elif args.auto:
        results = run_auto_pipeline(since_id=args.since_id)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n{len(results)} kandidat diproses. Hasil: {args.output}")


if __name__ == "__main__":
    main()
