"""
catalog.py
==========
Katalog rekrutmen bersama (single source of truth) untuk posisi, pertanyaan,
bobot penilaian, dan tahapan. Nilainya disamakan dengan front-end demo agar
skor yang dihitung server identik dengan yang ditampilkan dashboard HR.

Di produksi, idealnya ini dipindah ke database/CMS agar HR bisa mengubah
posisi & bobot per-posisi tanpa deploy ulang. Struktur sengaja dibuat datar
agar mudah dipindahkan ke tabel.
"""

MARITIME_CERTS = [
    "STCW Basic Safety Training", "BST Refreshment", "CoC Class I", "CoC Class II",
    "CoP (Certificate of Proficiency)", "ETO Certificate", "Tanker Familiarization (PSC/PST)",
]
BACKOFFICE_TOOLS = [
    "Excel / Spreadsheet", "SAP", "Accounting Software", "Adobe Suite",
    "Project Management Tools", "Bahasa Inggris Aktif",
]
VESSEL_TYPES = [
    "Product / Chemical Tanker", "Crude Oil Tanker", "Bulk Carrier",
    "Container Ship", "Offshore Support Vessel", "Tug & Barge",
]

JOBS = [
    {"id": "mar-ab", "track": "maritime", "title": "Able Seaman (AB)", "vesselHint": "Product Tanker",
     "minExp": 2, "gtMin": 0, "ocimf": False, "salaryMaxUsd": 1100,
     "certsRequired": ["STCW Basic Safety Training", "CoP (Certificate of Proficiency)"]},
    {"id": "mar-2nd-eng", "track": "maritime", "title": "2nd Engineer", "vesselHint": "Bulk Carrier",
     "minExp": 3, "gtMin": 5000, "ocimf": False, "salaryMaxUsd": 3800,
     "certsRequired": ["STCW Basic Safety Training", "CoC Class II"]},
    {"id": "mar-chief-officer", "track": "maritime", "title": "Chief Officer", "vesselHint": "Crude Oil Tanker",
     "minExp": 4, "gtMin": 8000, "ocimf": True, "salaryMaxUsd": 5200,
     "certsRequired": ["STCW Basic Safety Training", "CoC Class I"]},
    {"id": "bo-finance", "track": "backoffice", "title": "Finance Staff", "dept": "Finance — Jakarta HQ",
     "minExp": 1, "eduMin": "D3", "minAge": 21, "maxAge": 35, "salaryMaxIdr": 7000000,
     "toolsRequired": ["Excel / Spreadsheet", "Accounting Software"]},
    {"id": "bo-hr", "track": "backoffice", "title": "HR Recruitment Officer", "dept": "Human Resources — Jakarta HQ",
     "minExp": 2, "eduMin": "S1", "minAge": 23, "maxAge": 38, "salaryMaxIdr": 8500000,
     "toolsRequired": ["Excel / Spreadsheet"]},
    {"id": "bo-opsadmin", "track": "backoffice", "title": "Operation Admin", "dept": "Operations — Surabaya Branch",
     "minExp": 0, "eduMin": "SMA/SMK", "minAge": 19, "maxAge": 30, "salaryMaxIdr": 5500000,
     "toolsRequired": ["Excel / Spreadsheet"]},
]

AVAILABILITY_OPTIONS = ["Segera (≤1 minggu)", "2 minggu", "1 bulan", "Lebih dari 1 bulan"]

MARITIME_QUIZ = [
    {"id": "q1", "text": "Apakah CoC/CoP Anda untuk posisi ini saat ini berstatus AKTIF (belum kadaluarsa)?", "type": "yesno", "gate": True},
    {"id": "q2", "text": "Kapan Anda dapat bergabung jika diterima?", "type": "choice",
     "options": ["Segera (≤1 minggu)", "2 minggu", "1 bulan", "Lebih dari 1 bulan"]},
    {"id": "q3", "text": "Berapa ekspektasi gaji Anda? (USD / bulan)", "type": "number", "unit": "USD"},
    {"id": "q4", "text": "Apakah Anda bersedia ditempatkan sesuai jenis kapal & area pelayaran perusahaan?", "type": "yesno", "gate": True},
]

DISC_QUESTIONS = [
    {"id": "d1", "text": "Saat menghadapi tenggat waktu yang ketat, saya cenderung…", "options": [
        {"t": "Langsung mengambil alih & memberi arahan tegas", "k": "D"},
        {"t": "Mengajak tim berdiskusi santai mencari solusi", "k": "I"},
        {"t": "Tetap tenang & mengikuti proses yang sudah ada", "k": "S"},
        {"t": "Membuat daftar prioritas yang rinci & teliti", "k": "C"}]},
    {"id": "d2", "text": "Rekan kerja akan menggambarkan saya sebagai orang yang…", "options": [
        {"t": "Tegas & berorientasi hasil", "k": "D"},
        {"t": "Antusias & mudah bergaul", "k": "I"},
        {"t": "Sabar & dapat diandalkan", "k": "S"},
        {"t": "Teliti & sistematis", "k": "C"}]},
    {"id": "d3", "text": "Dalam sebuah rapat, saya biasanya…", "options": [
        {"t": "Mendorong agar keputusan cepat diambil", "k": "D"},
        {"t": "Aktif berbicara & melontarkan ide", "k": "I"},
        {"t": "Mendengarkan & mendukung pendapat tim", "k": "S"},
        {"t": "Memperhatikan detail & data pendukung", "k": "C"}]},
    {"id": "d4", "text": "Saat menerima kritik atas pekerjaan saya, saya…", "options": [
        {"t": "Menerimanya & langsung bertindak memperbaiki", "k": "D"},
        {"t": "Membicarakannya secara terbuka & santai", "k": "I"},
        {"t": "Mempertimbangkannya secara hati-hati", "k": "S"},
        {"t": "Menganalisis validitas kritik tersebut", "k": "C"}]},
]
DISC_LABEL = {
    "D": "Dominance — Tegas & Berorientasi Hasil",
    "I": "Influence — Persuasif & Komunikatif",
    "S": "Steadiness — Stabil & Kooperatif",
    "C": "Conscientiousness — Teliti & Sistematis",
}

IQ_QUESTIONS = [
    {"id": "iq1", "text": "Lanjutkan deret angka berikut: 2, 4, 8, 16, …?", "options": ["24", "30", "32", "36"], "answer": "32"},
    {"id": "iq2", "text": "KAPAL : PELAUT = PESAWAT : ?", "options": ["Penumpang", "Pilot", "Bandara", "Tiket"], "answer": "Pilot"},
    {"id": "iq3", "text": "Manakah yang TIDAK termasuk satu kelompok dengan yang lain?", "options": ["Sextant", "Kompas", "Radar", "Jangkar"], "answer": "Jangkar"},
]

VIDEO_QUESTIONS = {
    "maritime": [
        "Ceritakan pengalaman Anda menangani situasi darurat atau insiden di atas kapal.",
        "Bagaimana Anda berkoordinasi dengan kru dari latar belakang berbeda saat watchkeeping?",
    ],
    "backoffice": [
        "Ceritakan pengalaman Anda menyelesaikan masalah teknis/administratif yang menantang.",
        "Bagaimana cara Anda mengatur prioritas saat menghadapi beberapa tenggat waktu sekaligus?",
    ],
}

# Bobot komposit (dapat dikonfigurasi per-posisi di produksi)
MARITIME_WEIGHTS = [("CV Matching", "cv", 0.40), ("Video Analysis", "video", 0.30),
                    ("Kuis Kualifikasi", "quiz", 0.20), ("Sertifikasi", "cert", 0.10)]
BACKOFFICE_WEIGHTS = [("CV & Competency", "cv", 0.25), ("Personality Test", "personality", 0.20),
                      ("IQ Test", "iq", 0.15), ("Video Qs Analysis", "video", 0.25), ("Custom Form", "form", 0.15)]

# Tahapan yang TERLIHAT oleh kandidat di portal. Sengaja menyembunyikan proses
# internal (screening CV/skor, verifikasi sertifikat, background check) — semua itu
# tugas HR/sistem. Setelah asesmen mandiri selesai, kandidat hanya melihat satu
# tahap ringkas "Tahap Analisa HR".
MARITIME_STEPS = ["Registrasi", "Kuis Kualifikasi", "Video Interview", "Tahap Analisa HR"]
BACKOFFICE_STEPS = ["Registrasi", "Form Tambahan", "Tes DISC & IQ", "Video Interview", "Tahap Analisa HR"]


def job_by_id(job_id: str):
    return next((j for j in JOBS if j["id"] == job_id), None)


def video_questions_for(track: str):
    return VIDEO_QUESTIONS.get(track, VIDEO_QUESTIONS["maritime"])
