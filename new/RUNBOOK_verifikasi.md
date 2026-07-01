# Menjalankan Verifikasi Sertifikat (RPA + Claude Vision) — `cert_verifier.py`

RPA ini membuka `https://pelaut.dephub.go.id/verifikasi`, mengisi nomor,
**membaca CAPTCHA dengan Claude Vision**, submit, lalu mengekstrak status.

## Kenapa tidak bisa dijalankan di sandbox demo ini
Sudah diuji dan **terhalang oleh lingkungan** (bukan bug kode):
1. `ANTHROPIC_API_KEY` tidak diset di sandbox → solver CAPTCHA tak bisa jalan.
2. Browser Chromium Playwright belum terpasang & CDN-nya tidak di-whitelist.
3. Egress ke `pelaut.dephub.go.id` **diblokir** sandbox
   (terbukti: `HTTP 403 · x-deny-reason: host_not_allowed`).

Sudah diverifikasi via `web_fetch` bahwa halaman aslinya **masih memakai CAPTCHA aktif**
(`/captcha/default?...`), sehingga pendekatan Vision memang diperlukan, dan selector RPA
(`img[src*='captcha']`, field kode/sertifikasi) **cocok** dengan struktur situs.

## Cara menjalankan (di mesin/server Anda yang bisa akses internet)

```bash
# 1) Masuk folder
cd maxy

# 2) Pasang dependensi + browser Playwright (WAJIB, sekali saja)
pip install -r requirements.txt
python -m playwright install chromium

# 3) Set API key Anthropic (untuk membaca CAPTCHA)
export ANTHROPIC_API_KEY=sk-ant-...       # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."

# 4a) Uji SATU nomor (pakai --show-browser untuk melihat prosesnya)
python cert_verifier.py --kode 6212316902MC3123 --show-browser

# 4b) Uji SEMUA 6 nomor sekaligus (file batch sudah disiapkan)
python cert_verifier.py --batch kode_pelaut_test.json --output hasil_verifikasi.json
```

Hasil tersimpan di `hasil_verifikasi.json` (status per nomor:
`Valid` / `Kadaluarsa` / `Tidak Ditemukan` / `Error Jaringan` + detail nama, TTL, dsb.).

## Catatan penting soal format nomor
Form Kemenhub punya **dua kolom**:
- **Nomor Kode Pelaut** = 10 digit pertama (mis. `6212316902`).
- **Nomor Sertifikasi** = nomor lengkap 16 karakter (mis. `6212316902MC3123`).

Keenam nomor Anda adalah **16 karakter (Nomor Sertifikasi)**. `cert_verifier.py` sudah
diperbarui untuk **otomatis memilih kolom yang benar** berdasar panjang input
(≥13 karakter → kolom Nomor Sertifikasi; selain itu → kolom Kode Pelaut). Bila hasil
"Tidak Ditemukan" untuk semua, coba jalankan ulang dengan 10 digit pertama saja.

## Kalau CAPTCHA sering gagal dibaca
- `MAX_CAPTCHA_RETRY` di `cert_verifier.py` (default 4) bisa dinaikkan.
- Jalankan dengan `--show-browser` untuk melihat CAPTCHA yang muncul.
- Pastikan `CLAUDE_VISION_MODEL` (opsional) menunjuk model vision yang aktif.

## Integrasi otomatis (agentic) ke MAXY
Di server produksi, cukup set `ENABLE_CERT_VERIFY=true`. MAXY akan menjalankan RPA ini
**otomatis** begitu kandidat maritime menyelesaikan asesmen (endpoint `/complete`),
tanpa perlu dipicu HR — hasil muncul di tab **Sertifikat** dashboard HR.
