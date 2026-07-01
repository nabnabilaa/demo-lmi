"""
transcription.py
================
Speech-to-Text yang dapat dipasang-pasang (pluggable). Claude tidak melakukan
transkripsi audio, jadi kita pakai provider STT eksternal, lalu transcript-nya
dinilai oleh Claude di ai.py.

Provider (set lewat STT_PROVIDER di .env):
  - openai : OpenAI Whisper API   (butuh OPENAI_API_KEY)
  - groq   : Groq Whisper API     (butuh GROQ_API_KEY) — cepat & murah
  - local  : faster-whisper lokal (pip install faster-whisper)
  - stub   : transcript contoh    (default; untuk demo tanpa biaya)

extract_audio() memakai ffmpeg untuk mengubah video (webm/mp4) menjadi audio
mono 16kHz yang ringan untuk dikirim ke STT.
"""

import os
import subprocess
import logging

import config

log = logging.getLogger("maxy.stt")


class TranscriptionRateLimited(Exception):
    """Dilempar saat provider STT mengembalikan 429 (kuota harian habis)."""


def extract_audio(video_path: str) -> str | None:
    """Ekstrak audio dari video menjadi mp3 mono 16kHz. Return path audio atau None."""
    audio_path = os.path.splitext(video_path)[0] + ".mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "64k", audio_path],
            check=True, capture_output=True, timeout=180,
        )
        return audio_path
    except FileNotFoundError:
        log.error("ffmpeg tidak ditemukan — instal ffmpeg untuk ekstraksi audio.")
    except subprocess.CalledProcessError as e:
        log.error(f"ffmpeg gagal: {e.stderr.decode(errors='ignore')[:300]}")
    except Exception as e:
        log.error(f"extract_audio error: {e}")
    return None


def transcribe(video_path: str, language: str = "id") -> str:
    """Transkripsi video → teks. Mengembalikan string (kosong jika gagal total)."""
    provider = config.STT_PROVIDER.lower()
    if provider == "stub":
        return _stub_transcript(video_path)

    audio_path = extract_audio(video_path)
    if not audio_path:
        log.warning("Gagal ekstrak audio; fallback ke stub transcript.")
        return _stub_transcript(video_path)

    try:
        if provider == "openai":
            return _whisper_api(audio_path, language, base_url="https://api.openai.com/v1",
                                api_key=config.OPENAI_API_KEY, model="whisper-1")
        if provider == "groq":
            return _whisper_api(audio_path, language, base_url="https://api.groq.com/openai/v1",
                                api_key=config.GROQ_API_KEY, model="whisper-large-v3")
        if provider == "local":
            return _local_whisper(audio_path, language)
        log.warning(f"STT_PROVIDER '{provider}' tidak dikenal; pakai stub.")
        return _stub_transcript(video_path)
    finally:
        try:
            os.remove(audio_path)
        except Exception:
            pass


def _whisper_api(audio_path: str, language: str, base_url: str, api_key: str, model: str) -> str:
    """Whisper API kompatibel OpenAI (dipakai OpenAI & Groq)."""
    import requests
    if not api_key:
        log.warning("API key STT kosong; pakai stub.")
        return _stub_transcript(audio_path)
    with open(audio_path, "rb") as f:
        resp = requests.post(
            f"{base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={"model": model, "language": language, "response_format": "text"},
            timeout=180,
        )
    if resp.status_code == 200:
        return resp.text.strip()
    if resp.status_code == 429:
        raise TranscriptionRateLimited(
            f"Kuota STT habis (HTTP 429): {resp.text[:200]}"
        )
    log.error(f"STT API HTTP {resp.status_code}: {resp.text[:200]}")
    return ""


def _local_whisper(audio_path: str, language: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        log.error("faster-whisper belum terinstal (pip install faster-whisper); pakai stub.")
        return _stub_transcript(audio_path)
    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, language=language)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _stub_transcript(_path: str) -> str:
    """Transcript contoh agar pipeline tetap menghasilkan output saat STT nonaktif."""
    return ("(Transcript demo) Kandidat menjawab pertanyaan dengan menjelaskan pengalaman "
            "yang relevan, langkah-langkah yang diambil, serta hasilnya secara terstruktur "
            "dan menggunakan istilah teknis yang sesuai dengan posisi yang dilamar.")
