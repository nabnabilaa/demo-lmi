# MAXY — Sistem Rekrutmen Bertenaga AI · PT Lintas Maritim Indonesia

MAXY menerima data pendaftar dari website klien yang sudah ada
(`crewing.lintasmaritim.com`), menjalankan **asesmen mandiri** kandidat
(kuis, DISC/IQ, wawancara video), **menilai otomatis** dengan AI, lalu
menyajikan semuanya untuk keputusan **tim HR** — lengkap dengan penjadwalan
interview, background check, dan generate kontrak PKWT.

Dua jalur rekrutmen didukung:

- **Maritime Crew** — CV Screening → Kuis Kualifikasi → Video Interview → Verifikasi Sertifikat Pelaut
- **Back Office** — Intelligent Screening → Form + DISC + IQ → Video Qs

---

## 0. Model: AI Agent (agentic) dengan ACC HR

MAXY dirancang sebagai **agen otonom (agentic AI)**: begitu ada **pendaftar baru**, sistem
**bergerak sendiri** menjalankan pipeline — tanpa perlu dipicu HR:

1. Tarik data & CV dari `crewing.lintasmaritim.com` (webhook/poll) → buat profil.
2. Ekstrak field CV + skor kecocokan (AI) — otomatis.
3. Kirim link asesmen unik (email/WA) — otomatis.
4. Nilai kuis, transkrip + analisa video, dan hasil DISC/IQ — otomatis tiap kandidat submit.
5. **Verifikasi sertifikat pelaut (RPA + AI Vision)** — otomatis dijalankan sistem begitu
   kandidat menyelesaikan asesmen (bukan diklik HR).
6. Agregasi skor komposit + auto-ranking + rekomendasi AI — otomatis.

**Peran manusia hanya di titik ACC** (keputusan): review Tahap 1 (lanjut/tolak),
evaluasi interview, dan keputusan final (hired / talent pool / rejected). Tahap yang memang
butuh kandidat (rekam video, kerjakan tes DISC/IQ di platform eksternal) menunggu kandidat.

### Prinsip privasi kandidat (anti isi-ganda)

Sesuai desain, portal kandidat **tidak menampilkan proses/skor internal** — kandidat
tidak bisa melihat atau mengubahnya:

- **Data & CV ditarik via API** dari `crewing.lintasmaritim.com` (webhook/poll). Kandidat
  **tidak** mengisi ulang biodata / mengunggah CV di MAXY (menghindari isi ganda).
- **Screening CV & skor kecocokan disembunyikan** dari kandidat. Skor kuis, skor video,
  dan composite **tidak** dikembalikan ke portal kandidat — hanya untuk HR.
- **Verifikasi sertifikat & background check tidak ditampilkan** ke kandidat. Di portal,
  seluruh proses HR/sistem hanya tampil sebagai satu tahap ringkas **"Tahap Analisa HR"**.
- **DISC/MBTI & IQ dikerjakan di platform EKSTERNAL** (bukan di MAXY). Dua mode
  (`TEST_RESULT_MODE`): `api` (MAXY menarik hasil otomatis dari provider) atau `upload`
  (kandidat mengunggah berkas hasil untuk ditinjau HR).
- Portal kandidat memiliki **navigator demo** (bar bawah) untuk melompat antar tahap saat
  peninjauan — bagian ini tidak ada di produksi.

Konsol HR (`/hr`) menata setiap tahap sebagai **tab** (Profil · Scoring · Sertifikat ·
Video · Interview · Background · MCU · Keputusan · Komunikasi), bukan satu halaman panjang.
Tab **Sertifikat** (khusus maritime) menjalankan verifikasi RPA ke Kemenhub di sisi HR.

## 1. Arsitektur singkat

```
crewing.lintasmaritim.com         MAXY (aplikasi ini)
   (pendaftaran kandidat)            ┌─────────────────────────────────────┐
        │                            │  Ingestion  →  Scoring Engine (AI)  │
        │  webhook / polling  ─────► │       │                │           │
        ▼                            │       ▼                ▼           │
   data pendaftar                    │   SQLite (JSON doc)   Storage      │
                                     │       │            (video, PDF)    │
   Kandidat  ◄── tautan unik ─────── │       ▼                            │
   /a/<token>  (portal asesmen)      │   Portal & Dashboard HR  (/hr)     │
                                     └─────────────────────────────────────┘
                                              │
                                  Verifikasi sertifikat (RPA + Claude Vision)
                                        → pelaut.dephub.go.id
```

- **Pendaftaran tetap di website klien.** MAXY *menarik* datanya, tidak menggantikannya.
- Setiap pendaftar otomatis menerima **tautan unik** ke portal asesmen MAXY.
- Hasil tiap tahap dihitung jadi **skor komposit** dan tampil di **dashboard HR**.
- **Video wawancara**: kandidat merekam langsung di browser → ditranskripsi →
  dinilai AI → **dan tetap bisa ditonton ulang oleh HR** di dashboard.
- **Verifikasi sertifikat** pelaut dijalankan **sistem** (RPA Playwright +
  Claude Vision membaca CAPTCHA) ke `pelaut.dephub.go.id`.

---

## 2. Menjalankan secara lokal

```bash
cd maxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (opsional, untuk verifikasi sertifikat) pasang browser Playwright:
playwright install chromium

# ffmpeg WAJIB ada di sistem untuk transkripsi video:
#   Ubuntu/Debian: sudo apt-get install -y ffmpeg

cp .env.example .env          # lalu sesuaikan bila perlu (boleh dibiarkan default)

python app.py --port 5050
```

Buka:

| Halaman | URL | Keterangan |
|---|---|---|
| Beranda | `http://localhost:5050/` | landing |
| Dashboard HR | `http://localhost:5050/hr` | konsol recruiter (mode live) |
| Status sistem | `http://localhost:5050/health` | cek konfigurasi & koneksi |
| Portal kandidat | `http://localhost:5050/a/<token>` | dibuka via tautan unik |

### Coba cepat (tanpa integrasi crewing)

Buat kandidat contoh untuk menguji seluruh alur:

```bash
# kandidat maritime acak
curl -X POST http://localhost:5050/api/simulate
# atau tentukan sendiri:
curl -X POST http://localhost:5050/api/simulate -H "Content-Type: application/json" \
  -d '{"nama_lengkap":"Budi Santoso","email":"budi@example.com","no_telepon":"08123456789",
       "posisi_dilamar":"Able Seaman","kode_pelaut":"PLT-2291-0457",
       "cv_text":"Pelaut 3 tahun di Product Tanker, GT 12000, STCW & CoP."}'
```

Respons memuat `assessment_url` — buka di browser untuk menjalani asesmen
sebagai kandidat. Selesai mengisi, lihat hasilnya di `/hr`.

---

## 3. Menghubungkan ke crewing.lintasmaritim.com

Pilih salah satu (boleh keduanya):

### (A) Push via webhook — disarankan
Minta tim crewing mengirim setiap pendaftar baru ke:

```
POST {PUBLIC_BASE_URL}/webhook/crewing
Header: X-Webhook-Secret: <WEBHOOK_SECRET di .env>
Body (JSON): data pendaftar
```

### (B) Pull via polling
Isi `CREWING_POLL_URL` (+ `CREWING_API_KEY` / `CREWING_SESSION_COOKIE`) di `.env`,
lalu picu penarikan:

```bash
curl -X POST http://localhost:5050/api/poll-crewing
```

> **Pemetaan field.** `ingest.normalize_payload()` mengenali nama field umum
> (mis. `nama_lengkap`/`name`, `posisi_dilamar`/`position`, `kode_pelaut`,
> `cv_url`/`cv_text`). Bila struktur data crewing berbeda, sesuaikan fungsi
> tersebut di `ingest.py`. Untuk sumber HTML, lengkapi `_parse_html_applicants()`
> sesuai markup halaman crewing.

---

## 4. Penilaian (bobot komposit)

**Maritime** — CV 40% · Video 30% · Kuis 20% · Sertifikat 10%
**Back Office** — CV & Kompetensi 25% · Kepribadian (DISC) 20% · IQ 15% · Video 25% · Form 15%

Bobot, posisi, dan pertanyaan terpusat di `catalog.py` (single source of truth)
sehingga skor yang dihitung server identik dengan yang tampil di dashboard HR.
Skor sertifikat: valid=100, kadaluarsa=45, error=60, tidak ditemukan=15.

---

## 5. Fitur HR (di `/hr`)

- **Profil 360°** kandidat + ringkasan AI (pro/kontra).
- **Scoring breakdown** per metrik dan skor komposit + auto-ranking.
- **Video interview** — tonton ulang rekaman asli, baca transkrip & ringkasan AI.
- **Keputusan Tahap 1** (Lanjut / Talent Pool / Tolak).
- **Penjadwalan interview** (online/offline) + **formulir evaluasi** rubrik.
- **Background check** — template referensi (masa kerja, alasan resign, rehire).
- **MCU** (khusus maritime) — validitas & status fit-for-duty.
- **Keputusan final** (Hired / Talent Pool / Rejected).
- **Generate kontrak PKWT** — pratinjau + **unduh PDF / DOCX** + siap cetak.

Semua aksi HR otomatis tersimpan ke server (`PUT /api/candidates/<id>`),
dan papan HR menyegarkan diri berkala agar pendaftar/asesmen baru muncul sendiri.

---

## 6. Verifikasi sertifikat pelaut (RPA)

`cert_verifier.py` membuka `pelaut.dephub.go.id/verifikasi` dengan Playwright,
mengunduh gambar CAPTCHA, mengirimnya ke **Claude Vision** untuk dibaca,
mengisi form, lalu mengekstrak status: **Valid / Kadaluarsa / Tidak Ditemukan /
Error Jaringan**.

Aktifkan dengan `ENABLE_CERT_VERIFY=true` **hanya** bila:
1. server dapat mengakses `pelaut.dephub.go.id`,
2. `ANTHROPIC_API_KEY` terisi, dan
3. `playwright install chromium` sudah dijalankan.

Saat nonaktif (default), kandidat tetap mengonfirmasi nomor sertifikat dan HR
dapat memprosesnya manual; sistem tidak menghubungi situs eksternal.

---

## 7. Catatan operasional

- **Tanpa kunci API / provider**, MAXY tetap berjalan penuh memakai *fallback*:
  ekstraksi CV & penilaian video heuristik, notifikasi dicetak ke log, dan
  transkrip contoh (`STT_PROVIDER=stub`). Cocok untuk demo & pengembangan.
- **Transkripsi video** butuh `ffmpeg` di sistem. Pilih provider STT di `.env`
  (`openai` / `groq` untuk API, `local` untuk faster-whisper tanpa biaya API).
- **Retensi video** otomatis: berkas video lebih tua dari `VIDEO_RETENTION_DAYS`
  dibersihkan; metadata & skor tetap tersimpan.
- **Produksi**: jalankan dengan gunicorn, mis.
  `gunicorn -w 4 -b 0.0.0.0:5050 app:app`, dan letakkan di belakang reverse
  proxy (HTTPS). Untuk skala besar, pindahkan SQLite → Postgres dan simpan
  video di object storage (S3/GCS).

---

## 8. Struktur berkas

```
maxy/
├── app.py             # Server Flask — seluruh route (API + halaman)
├── config.py          # Konfigurasi dari .env
├── catalog.py         # Posisi, pertanyaan, bobot, tahapan (single source of truth)
├── scoring.py         # Mesin penilaian (identik dgn front-end)
├── db.py              # Lapisan SQLite (kandidat, token, notifikasi, video)
├── ingest.py          # Tarik & normalisasi data pendaftar crewing
├── ai.py              # Helper Claude (ekstraksi CV, analisis video) + fallback
├── transcription.py   # Speech-to-Text (stub/openai/groq/local)
├── video_pipeline.py  # Simpan video → transkrip → nilai → retensi
├── links.py           # Tautan asesmen + notifikasi Email/WhatsApp
├── contracts.py       # Generate kontrak PKWT (PDF/DOCX)
├── cert_verifier.py   # RPA verifikasi sertifikat + Claude Vision CAPTCHA
├── templates/
│   ├── index.html     # Beranda
│   ├── portal.html    # Portal asesmen kandidat (rekam video di browser)
│   └── hr.html        # Dashboard HR (mode live)
├── requirements.txt
└── .env.example
```

---

## 9. Ringkasan endpoint

| Metode | Endpoint | Fungsi |
|---|---|---|
| POST | `/webhook/crewing` | Terima pendaftar baru (push, butuh secret) |
| POST | `/api/poll-crewing` | Tarik pendaftar (pull) |
| POST | `/api/simulate` | Buat kandidat contoh (uji/demo) |
| GET | `/a/<token>` | Portal asesmen kandidat |
| GET | `/api/assessment/<token>` | Status & soal asesmen |
| POST | `/api/assessment/<token>/quiz` | Submit kuis kualifikasi (maritime) |
| POST | `/api/assessment/<token>/bundle` | Submit Form Tambahan / kuesioner (back office) |
| POST | `/api/assessment/<token>/tests` | Hasil DISC & IQ dari platform eksternal (upload / API) |
| POST | `/api/assessment/<token>/video` | Unggah 1 jawaban video → transkrip+nilai |
| POST | `/api/assessment/<token>/complete` | Tandai asesmen mandiri selesai |
| GET | `/api/candidates` | Daftar kandidat (HR) |
| GET/PUT | `/api/candidates/<id>` | Detail / simpan perubahan (aksi HR) |
| POST | `/api/candidates/<id>/notify` | Kirim notifikasi manual |
| POST | `/api/candidates/<id>/contract?fmt=pdf\|docx` | Generate kontrak PKWT |
| POST | `/api/candidates/<id>/verify-cert` | Jalankan verifikasi sertifikat (HR/RPA, maritime) |
| GET | `/api/stats` | Statistik ringkas |
| GET | `/media/video/<file>` | Streaming video (tonton ulang HR) |
| GET | `/media/contract/<file>` | Unduh kontrak |
| GET | `/media/test/<file>` | Berkas hasil tes DISC/IQ (untuk HR) |
| GET | `/health` | Status & konfigurasi |
