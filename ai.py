"""
ai.py
=====
Pembungkus AI untuk dua tugas inti:
  1. extract_cv_fields()      — ubah teks CV mentah menjadi field terstruktur
  2. analyze_video_answer()   — nilai transcript jawaban video → skor + ringkasan

Mendukung Anthropic (Claude), Google (Gemini), dan OpenAI (GPT).
Pilih provider via AI_PROVIDER di .env: auto | anthropic | gemini | openai
Mode auto: anthropic → gemini → openai → fallback heuristik.
"""

import json
import logging
import re
import config
from catalog import MARITIME_CERTS, BACKOFFICE_TOOLS, VESSEL_TYPES

log = logging.getLogger("maxy.ai")

try:
    import anthropic as _anthropic_lib
except ImportError:
    _anthropic_lib = None

try:
    import google.genai as _genai_lib
except ImportError:
    _genai_lib = None

try:
    from openai import OpenAI as _OpenAI
except ImportError:
    _OpenAI = None


def _get_client_type() -> str | None:
    """Tentukan provider AI berdasarkan AI_PROVIDER dan key yang tersedia."""
    provider = config.AI_PROVIDER.lower()

    if provider == "anthropic":
        return "anthropic" if (_anthropic_lib and config.ANTHROPIC_API_KEY) else None
    if provider == "gemini":
        return "gemini" if (_genai_lib and config.GEMINI_API_KEY) else None
    if provider == "openai":
        return "openai" if (_OpenAI and config.OPENAI_API_KEY) else None

    # auto: prioritas anthropic → gemini → openai
    if _anthropic_lib and config.ANTHROPIC_API_KEY:
        return "anthropic"
    if _genai_lib and config.GEMINI_API_KEY:
        return "gemini"
    if _OpenAI and config.OPENAI_API_KEY:
        return "openai"
    return None


def _call_anthropic(prompt: str) -> str:
    client = _anthropic_lib.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _call_gemini(prompt: str) -> str:
    if not _genai_lib:
        raise RuntimeError("google-genai tidak terinstall. Jalankan: pip install google-genai")
    client = _genai_lib.Client(api_key=config.GEMINI_API_KEY)
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    return resp.text


def _call_openai(prompt: str) -> str:
    if not _OpenAI:
        raise RuntimeError("openai tidak terinstall. Jalankan: pip install openai")
    client = _OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    return resp.choices[0].message.content


def _call_model(prompt: str) -> str:
    ctype = _get_client_type()
    if ctype is None:
        log.warning("Tidak ada AI provider yang aktif — pakai fallback heuristik.")
        return ""
    try:
        if ctype == "anthropic":
            return _call_anthropic(prompt)
        if ctype == "gemini":
            return _call_gemini(prompt)
        if ctype == "openai":
            return _call_openai(prompt)
    except Exception as e:
        log.warning(f"AI call ({ctype}) gagal: {e}")
    return ""


def _extract_json(text: str) -> dict:
    """Ambil objek JSON pertama dari balasan model."""
    t = text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except Exception:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(t[start:end + 1])
            except Exception:
                pass
    return {}


# ===========================================================================
# 1. Ekstraksi CV
# ===========================================================================

def extract_cv_fields(cv_text: str, track: str) -> dict:
    if not _get_client_type() or not (cv_text or "").strip():
        return _cv_fallback(cv_text, track)

    if track == "maritime":
        schema = (
            '{"vessel": "salah satu dari ' + " | ".join(VESSEL_TYPES) + '", '
            '"expYears": <angka>, "gt": <angka>, "ocimf": <true/false>, '
            '"certs": [pilih dari: ' + ", ".join(MARITIME_CERTS) + ']}'
        )
    else:
        schema = (
            '{"edu": "SMA/SMK | D3 | S1 | S2", "age": <angka>, '
            '"expYears": <angka>, "tools": [pilih dari: ' + ", ".join(BACKOFFICE_TOOLS) + ']}'
        )

    prompt = f"Ekstrak data CV ke JSON. SKEMA: {schema}\n\nCV:\n{cv_text[:6000]}"
    try:
        text = _call_model(prompt)
        data = _extract_json(text)
        return _normalize_cv(data, track)
    except Exception as e:
        log.warning(f"extract_cv_fields gagal: {e}")
        return _cv_fallback(cv_text, track)


def _normalize_cv(data: dict, track: str) -> dict:
    if track == "maritime":
        return {
            "vessel": data.get("vessel") or VESSEL_TYPES[0],
            "expYears": int(data.get("expYears", 0) or 0),
            "gt": int(data.get("gt", 0) or 0),
            "ocimf": bool(data.get("ocimf", False)),
            "certs": [c for c in (data.get("certs") or []) if c in MARITIME_CERTS],
        }
    return {
        "edu": data.get("edu") if data.get("edu") in ("SMA/SMK", "D3", "S1", "S2") else "SMA/SMK",
        "age": int(data.get("age", 0) or 0),
        "expYears": int(data.get("expYears", 0) or 0),
        "tools": [t for t in (data.get("tools") or []) if t in BACKOFFICE_TOOLS],
    }


def _cv_fallback(cv_text: str, track: str) -> dict:
    text = (cv_text or "").lower()
    years = int((re.search(r"(\d+)\s*(?:tahun|years?)", text) or [0]).group(1) if re.search(r"(\d+)\s*(?:tahun|years?)", text) else 0)
    if track == "maritime":
        gt_match = re.search(r"(\d[\d.,]{2,})\s*gt", text)
        gt = int(re.sub(r"[.,]", "", gt_match.group(1))) if gt_match else 0
        return {
            "vessel": next((v for v in VESSEL_TYPES if v.split()[0].lower() in text), VESSEL_TYPES[0]),
            "expYears": years, "gt": gt,
            "ocimf": "ocimf" in text or "vetting" in text,
            "certs": [c for c in MARITIME_CERTS if c.split()[0].lower() in text][:3],
        }
    edu = "S2" if "s2" in text or "magister" in text else "S1" if "s1" in text or "sarjana" in text else "D3" if "d3" in text or "diploma" in text else "SMA/SMK"
    age = int((re.search(r"(?:umur|usia|age)\D{0,4}(\d{2})", text) or [0]).group(1) if re.search(r"(?:umur|usia|age)\D{0,4}(\d{2})", text) else 0)
    return {"edu": edu, "age": age, "expYears": years, "tools": [t for t in BACKOFFICE_TOOLS if t.split()[0].lower() in text]}


# ===========================================================================
# 1b. CV Summary (narasi singkat)
# ===========================================================================

def generate_cv_summary(cv_text: str, fields: dict, track: str, job_title: str = "") -> str:
    """Buat ringkasan narasi 2–3 kalimat dari data CV kandidat."""
    if not _get_client_type():
        return _cv_summary_fallback(fields, track, job_title)
    if track == "maritime":
        detail = (
            f"Posisi dilamar: {job_title}. "
            f"Jenis kapal: {fields.get('vessel', '-')}. "
            f"Pengalaman: {fields.get('expYears', 0)} tahun. "
            f"GT: {fields.get('gt', 0)}. "
            f"OCIMF: {'Ya' if fields.get('ocimf') else 'Tidak'}. "
            f"Sertifikat: {', '.join(fields.get('certs', [])) or '-'}."
        )
    else:
        detail = (
            f"Posisi dilamar: {job_title}. "
            f"Pendidikan: {fields.get('edu', '-')}. "
            f"Usia: {fields.get('age', '-')} tahun. "
            f"Pengalaman: {fields.get('expYears', 0)} tahun. "
            f"Keahlian: {', '.join(fields.get('tools', [])) or '-'}."
        )
    prompt = (
        "Tulis ringkasan profil kandidat rekrutmen dalam 2–3 kalimat Bahasa Indonesia "
        "yang padat dan profesional. Fokus pada kesesuaian dengan posisi, "
        "pengalaman utama, dan kekuatan kandidat. Jangan tambahkan opini subjektif.\n\n"
        f"DATA KANDIDAT:\n{detail}\n\n"
        f"PENGGALAN CV (jika ada):\n{(cv_text or '')[:1500]}"
    )
    try:
        return _call_model(prompt).strip()
    except Exception as e:
        log.warning(f"generate_cv_summary gagal: {e}")
        return _cv_summary_fallback(fields, track, job_title)


def _cv_summary_fallback(fields: dict, track: str, job_title: str = "") -> str:
    if track == "maritime":
        exp = fields.get("expYears", 0) or 0
        vessel = fields.get("vessel") or "kapal"
        certs = fields.get("certs", []) or []
        cert_str = f", memiliki {len(certs)} sertifikat" if certs else ""
        return (f"Kandidat memiliki {exp} tahun pengalaman di {vessel}{cert_str}. "
                f"Melamar posisi {job_title or 'maritime'}.")
    else:
        edu = fields.get("edu") or "tidak diketahui"
        exp = fields.get("expYears", 0) or 0
        tools = fields.get("tools", []) or []
        tools_str = f", menguasai {', '.join(tools[:2])}" if tools else ""
        return (f"Kandidat berpendidikan {edu} dengan {exp} tahun pengalaman{tools_str}. "
                f"Melamar posisi {job_title or 'back office'}.")


# ===========================================================================
# 2. Analisis jawaban video
# ===========================================================================

def analyze_video_answer(question: str, transcript: str, track: str) -> dict:
    if not _get_client_type() or not (transcript or "").strip():
        return _video_fallback(transcript)

    prompt = (
        "Nilai jawaban wawancara ini. Balas HANYA JSON: "
        '{"score": <0-100>, "summary": "<1 kalimat>"}\n\n'
        f"PERTANYAAN: {question}\n\nTRANSCRIPT: {transcript[:15000]}"
    )
    try:
        text = _call_model(prompt)
        data = _extract_json(text)
        return {
            "score": max(0, min(100, int(data.get("score", 0) or 0))),
            "summary": (data.get("summary") or "Penilaian otomatis.").strip()
        }
    except Exception as e:
        log.warning(f"analyze_video_answer gagal: {e}")
        return _video_fallback(transcript)


def _video_fallback(transcript: str) -> dict:
    t = (transcript or "").strip()
    if not t: return {"score": 0, "summary": "Tidak ada transcript."}
    words = t.split()
    score = max(0, min(95, 55 + min(25, len(words) // 8)))
    return {"score": score, "summary": "Penilaian heuristik."}


def overall_video_score(answers: list[dict]) -> int:
    scored = [a.get("aiScore", a.get("ai_score", 0)) or 0 for a in answers]
    return round(sum(scored) / len(scored)) if scored else 0
