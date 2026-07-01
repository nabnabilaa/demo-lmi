# Daftar Pertanyaan Meeting: Kebutuhan Sistem Rekrutmen LMI (MAXY)

*Dokumen ini berisi daftar pertanyaan strategis yang perlu Anda tanyakan kepada tim LMI (HR, Operasional, atau IT) untuk memastikan sistem MAXY dibangun sesuai dengan standar dan budaya kerja mereka.*

---

## 1. Integrasi API & Portal Pendaftaran
*Tujuan: Memastikan data yang masuk ke MAXY sudah lengkap.*
*   "Mengingat pendaftaran tetap dilakukan di portal LMI yang sudah ada, apakah tim IT LMI bisa memastikan pengiriman data via Webhook (API) ke MAXY? Kapan kita bisa mulai uji coba integrasinya?"
*   "Field data apa saja yang wajib (mandatory) diisi kandidat saat daftar di portal Anda? *(Kita butuh memastikan nama, email, no HP, posisi, dan file CV selalu terkirim ke MAXY).* "

## 3. Matriks Kualifikasi & Sertifikat (Screening Awal)
*Tujuan: Mendapatkan aturan "Hard Filter" agar AI bekerja akurat.*
*   "Apakah LMI memiliki dokumen SOP atau Matriks Kualifikasi per Jabatan (terutama pelaut)? Kami membutuhkan detail seperti: Syarat minimal GT kapal, Tipe Kapal, dan Daftar Sertifikat Wajib untuk setiap posisi."
*   "Untuk validasi keaslian sertifikat pelaut, apakah LMI punya akses API/Jalur khusus ke database Disnaker/Kemenhub, atau pengecekan selama ini murni dilakukan secara manual via web *pelaut.dephub.go.id*?"
*   "Terkait lamanya proses pengecekan ke Kemenhub, kami mengusulkan **Sistem Fast-Fail** dengan membedah 16 digit kode sertifikat (di mana 2 digit terakhir adalah tahun terbit). Dengan ini, sistem MAXY bisa otomatis mencoret kandidat yang masa berlaku sertifikatnya sudah lebih dari 5 tahun tanpa perlu menembak server Kemenhub. Apakah strategi ini bisa disetujui?"

## 3. Asesmen Mandiri & Video Interview
*Tujuan: Menyesuaikan konten asesmen dengan budaya LMI.*
*   **Video Interview:** "Kami menyiapkan 3 pertanyaan video asinkronus (durasi maks 2 menit) untuk menilai komunikasi dan *safety awareness* kandidat. Apakah HR LMI punya daftar pertanyaan spesifik yang *wajib* ditanyakan saat interview awal?"
*   **Kuis Backoffice:** "Untuk posisi darat (Back Office), filter awal apa yang biasanya paling memakan waktu HR saat menyeleksi? (Misal: ekspektasi gaji, ketersediaan join, atau kemahiran software tertentu?). Kami akan masukkan ini ke *Kuis Kualifikasi* agar AI otomatis memfilter."
*   **Marlins Test:** "Apakah LMI mewajibkan skor Marlins Test (Bahasa Inggris) untuk Perwira Kapal (Officer)? Jika ya, berapakah standar minimal skornya?"

## 4. Background Check (Referensi Kerja)
*Tujuan: Menyepakati alur otomatisasi background check.*
*   "Kami merancang alur di mana sistem MAXY akan otomatis mengirim email ke atasan sebelumnya (yang datanya diinput kandidat) berisi 3 pertanyaan: *Validasi posisi, Evaluasi integritas (1-5), dan Kelayakan Rehire*. Apakah 3 pertanyaan ini sudah cukup untuk standar Background Check di LMI?"
*   "Atau apakah tim HR LMI lebih memilih menelepon langsung atasan tersebut? (Jika menelepon langsung, MAXY akan menampilkan nomor HP atasan di Dashboard HR)."

## 5. Kontrak Kerja (PKWT/PKWTT)
*Tujuan: Menyiapkan template kontrak digital.*
*   "Untuk fitur *Auto-Generate Kontrak*, apakah LMI bisa membagikan *Draft/Template Standard* PKWT untuk Pelaut dan Back Office (format Word/PDF)?"
*   "Data variabel apa saja yang wajib ada di dalam kontrak? (Misal: Nama, NIK, Jabatan, Gaji, Tunjangan, Durasi Kontrak, Tempat Penugasan/Kapal)."

## 6. Notifikasi & Operasional WhatsApp
*Tujuan: Memastikan komunikasi ke kandidat lancar dan profesional.*
*   "Mengingat pelaut lebih sering membuka WhatsApp daripada Email, MAXY membutuhkan integrasi WA untuk mengirim link asesmen. Apakah LMI saat ini sudah menggunakan layanan **WhatsApp Business API** (seperti Wati, Qontak, atau Qiscus)? Jika belum, apakah LMI bersedia difasilitasi pembuatannya?"
*   "Saat ini, siapa (*email/role apa*) yang berwenang mengambil keputusan akhir *'Hired'* di sistem sebelum kontrak dibuat? Apakah hanya HR Manager, atau butuh persetujuan User/Direktur?"

---

## 7. TAMBAHAN (Advanced Features): Validasi Klinik & Data
*Poin ini ditanyakan untuk menunjukkan bahwa MAXY memikirkan operasional end-to-end LMI.*

*   **Integrasi Hasil MCU (Medical Check-Up):** "Untuk tahapan MCU pelaut, apakah hasilnya diunggah sendiri oleh pelaut, atau pihak LMI menerima langsung dari Klinik Rekanan? Jika dari Klinik Rekanan, MAXY bisa dibuatkan portal khusus atau API agar Klinik bisa langsung mengunggah status *Fit for Duty* ke sistem."
*   **Sistem Blacklist & Talent Pool:** "Jika seorang kandidat gagal di tahap Background Check (misal: *attitude* buruk) atau tidak *Fit for Duty* di MCU, apakah LMI menerapkan sistem *Blacklist* mutlak agar mereka tidak bisa melamar lagi selamanya, atau sekadar ditolak untuk lowongan tersebut?"
*   **Keamanan Data (Data Retention):** "Karena MAXY akan menyimpan data sensitif (KTP, Buku Pelaut, Kontrak), berapa lama SOP penyimpanan data lamaran kerja di LMI? Apakah data kandidat yang tidak lolos harus dihapus otomatis (auto-purge) setelah 1 tahun untuk memenuhi standar ISO/Security?"

---

**Tips Tambahan Saat Meeting:**
Tunjukkan kepada mereka bahwa dengan sistem "Fast Fail" (lewat kuis dan parsing kode sertifikat), beban kerja HR akan berkurang drastis karena HR hanya akan melihat profil kandidat yang *benar-benar sudah memenuhi kualifikasi mutlak*.


Yang perlu direvisi:

1. Bahasa kode sertifikat 16 digit (poin 2) — Data ini perlu diverifikasi dulu. Format kode di database Kemenhub (pelaut.dephub.go.id) tidak tentu punya pola tahun yang bisa di-parse secara instan. MAXY saat ini sudah pakai cara yang lebih akurat: scraping langsung ke portal Kemenhub. Pertanyaan ini bisa menyesatkan — ganti ke: "Apakah LMI punya akses khusus ke database Disnaker/Kemenhub, atau pengecekan selama ini dilakukan manual?"
2. Marlins Test (poin 3) — Pertanyaannya tepat, tapi perlu tambah: "Apakah skornya dikirim langsung oleh provider Marlins ke LMI, atau kandidat yang upload sendiri?" Ini akan menentukan apakah MAXY perlu integrasi API atau cukup upload PDF.
3. Poin 6 — "Siapa yang berwenang approve 'Hired'" — Pertanyaan ini sangat penting tapi terlalu diburu. Pisahkan menjadi dua: (a) siapa yang bisa set status Hired di MAXY, (b) apakah butuh fitur approval/sign-off bertingkat (misal HR input → Manager approve). Ini menentukan apakah perlu fitur multi-role atau tidak.
4. Urutan pertanyaan kurang strategis — Sebaiknya buka dengan pertanyaan yang menunjukkan kamu sudah mengerti masalah mereka, bukan langsung teknis. Tambahkan satu pertanyaan pembuka di depan: "Saat ini berapa rata-rata waktu dari kandidat mendaftar sampai kontrak ditandatangani? Dan di tahap mana yang paling memakan waktu tim HR?" — ini langsung membangun kepercayaan.

Yang bisa dihapus:
- Kalimat penjelasan dalam tanda kurung (kita butuh memastikan...) — terlalu panjang untuk dokumen meeting. Cukup pertanyaannya saja, penjelasan bisa disampaikan lisan.

---
Secara keseluruhan: dokumen ini 80% siap. Paling penting tambahkan pertanyaan pembuka tentang pain point saat ini, dan pisahkan pertanyaan approval Hired menjadi lebih spesifik. Itu yang paling sering jadi decision-maker di meeting discovery seperti ini.