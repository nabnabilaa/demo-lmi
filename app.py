"""
app.py
======
Server utama MAXY — menyatukan seluruh alur:

  SUMBER DATA (crewing.lintasmaritim.com)
    POST /webhook/crewing            terima pendaftar baru (push)
    POST /api/poll-crewing           tarik pendaftar (pull, manual trigger)
    POST /api/simulate               buat kandidat contoh (uji/demo)

  PORTAL ASESMEN KANDIDAT (domain MAXX, via tautan unik /a/<token>)
    GET  /a/<token>                  halaman portal kandidat
    GET  /api/assessment/<token>     status & soal asesmen
    GET  /api/assessment/<token>/schedule  jadwal interview (hanya scheduledAt, mode, location)
    POST /api/assessment/<token>/quiz      submit kuis kualifikasi (maritime)
    POST /api/assessment/<token>/bundle    submit Form Tambahan (back office)
    POST /api/assessment/<token>/tests     hasil DISC/IQ dari platform eksternal (upload/API)
    POST /api/assessment/<token>/video     unggah 1 jawaban video → transkrip+nilai
    POST /api/assessment/<token>/complete  tandai asesmen mandiri selesai

  DASHBOARD HR (domain MAXX, /hr)
    GET  /hr                         konsol HR (mode live)
    GET  /api/candidates             daftar kandidat
    GET  /api/candidates/<id>        detail kandidat
    PUT  /api/candidates/<id>        simpan perubahan dokumen (aksi HR)
    GET  /api/stats                  statistik
    POST /api/candidates/<id>/notify       kirim notifikasi manual
    POST /api/candidates/<id>/contract     generate kontrak PKWT → URL unduh
    POST /api/candidates/<id>/verify-cert  jalankan verifikasi sertifikat (HR/RPA)
    POST /api/candidates/<id>/bgcheck      simpan hasil background check (HR only)
    PUT  /api/candidates/<id>/mcu          update status MCU (maritime, HR only)
    POST /api/candidates/<id>/schedule     set jadwal interview + kirim notifikasi ke kandidat

  MEDIA
    GET  /media/video/<file>         streaming video (nonton ulang HR)
    GET  /media/contract/<file>      unduh kontrak
    GET  /media/test/<file>          berkas hasil tes DISC/IQ (untuk HR)
"""

import os
import logging
import argparse
import threading

from flask import (Flask, request, jsonify, abort, send_from_directory,
                   send_file, Response)

import config
import db
import ingest
import links
import contracts
from catalog import (job_by_id, video_questions_for, MARITIME_QUIZ,
                     BACKOFFICE_TOOLS, MARITIME_STEPS, BACKOFFICE_STEPS,
                     AVAILABILITY_OPTIONS)
from scoring import (qual_score_maritime, calc_form_score,
                     disc_from_result, iq_from_result,
                     cv_match, recompute, hr_column, ai_recommendation)
import video_pipeline
import tests_pipeline

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("maxy.app")



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))
app.secret_key = config.SECRET_KEY
db.init_db()


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Webhook-Secret"
    return resp


# ===========================================================================
# Helper progress asesmen
# ===========================================================================

def _progress(doc: dict) -> dict:
    track = doc["track"]
    vq = video_questions_for(track)
    base = {
        "videoNeeded": len(vq),
        "videoCount": len(doc["video"].get("answers", [])),
        "videoDone": len(doc["video"].get("answers", [])) >= len(vq),
        "completed": bool(doc.get("selfServeDone")),
    }
    if track == "maritime":
        base["quizDone"] = doc["quiz"].get("passFail") is not None
    else:
        b = doc["bundle"]
        base["formDone"] = bool(
            (b.get("story") or "").strip() or b.get("tools") or b.get("availabilityJoin")
        )
        base["testsDone"] = b.get("testSource") is not None
    return base


def _public_candidate(doc: dict) -> dict:
    """Subset data kandidat untuk ditampilkan ke kandidat sendiri (tanpa skor internal)."""
    return {
        "id": doc["id"], "name": doc.get("name"), "track": doc["track"],
        "jobId": doc["jobId"], "jobTitle": doc.get("jobTitle"),
    }


def _doc_or_404_by_token(token: str) -> dict:
    maxy_id = db.resolve_token(token)
    if not maxy_id:
        abort(404, "Tautan tidak valid atau sudah kedaluwarsa.")
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan.")
    return doc


# ===========================================================================
# SUMBER DATA — crewing.lintasmaritim.com
# ===========================================================================

@app.route("/webhook/crewing", methods=["POST"])
def webhook_crewing():
    secret = request.headers.get("X-Webhook-Secret") or request.args.get("secret")
    if secret != config.WEBHOOK_SECRET:
        abort(401, "Invalid webhook secret")
    payload = request.get_json(silent=True)
    if not payload:
        abort(400, "Payload JSON kosong/invalid")
    doc = ingest.ingest_payload(payload, send_link=True)
    return jsonify({"success": True, "maxy_id": doc["id"],
                    "assessment_url": links.assessment_url(db.token_for(doc["id"]))}), 201


@app.route("/api/poll-crewing", methods=["POST"])
def api_poll_crewing():
    new_docs = ingest.poll_crewing(send_link=True)
    return jsonify({"success": True, "new": len(new_docs),
                    "candidates": [d["id"] for d in new_docs]})


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """Buat kandidat contoh untuk uji/demo — meniru payload nyata dari
    crewing.lintasmaritim.com/apply (maritime) & rekrutmen.lintasmaritim.com (back office).
    Nama field disamakan dengan form klien agar tidak ada pengisian ganda."""
    payload = request.get_json(silent=True) or {}
    if not payload:
        import random
        sample = random.choice([
            {  # ——— Maritime: persis field form crewing.lintasmaritim.com/apply ———
                "nomor_ktp": "3174060101900002", "nama_lengkap": "Budi Santoso",
                "no_telepon": "081234567890", "email": "budi.santoso@example.com",
                "tempat_lahir": "Semarang", "tanggal_lahir": "1990-01-01", "jenis_kelamin": "LAKI-LAKI",
                "domisili": "Jakarta Utara", "kode_pelaut": "6211123456789",
                "jenis_kapal_terakhir": "TANKER", "jabatan_terakhir": "Able Seaman",
                "posisi_dilamar": "Able Seaman", "jenis_kapal_dilamar": "TANKER",
                "sertifikat": "RATING AS ABLE DECK",
                "cv_text": "Pelaut berpengalaman 3 tahun di Product Tanker (GT 12.000). "
                           "Memegang STCW Basic Safety Training dan CoP. Terbiasa watchkeeping & mooring."},
            {  # ——— Back office: field form rekrutmen.lintasmaritim.com ———
                "nomor_ktp": "3174065502950003", "nama_lengkap": "Dewi Anggraini",
                "no_telepon": "081322119087", "email": "dewi.anggraini@example.com",
                "tempat_lahir": "Bandung", "tanggal_lahir": "1998-02-15", "jenis_kelamin": "PEREMPUAN",
                "domisili": "Jakarta Selatan", "posisi_dilamar": "Finance Staff",
                "pendidikan": "S1", "jurusan": "Akuntansi", "usia": 27,
                "cv_text": "Lulusan S1 Akuntansi (usia 27). Pengalaman 2 tahun sebagai staf keuangan. "
                           "Menguasai Excel/Spreadsheet, Accounting Software, dan SAP."},
        ])
        payload = sample
    doc = ingest.ingest_payload(payload, send_link=True, verify_cert=False)
    return jsonify({"success": True, "maxy_id": doc["id"],
                    "assessment_url": links.assessment_url(db.token_for(doc["id"])),
                    "candidate": doc}), 201


# ===========================================================================
# DEMO SHORTCUTS
# ===========================================================================

_DEMO_PAYLOADS = {
    "crew": {
        "nama_lengkap": "Budi Santoso", "no_telepon": "081234567890",
        "email": "budi.demo@example.com", "posisi_dilamar": "Able Seaman",
        "jenis_kapal_terakhir": "TANKER", "jabatan_terakhir": "Able Seaman",
        "kode_pelaut": "6211123456789",
        "cv_text": "Pelaut berpengalaman 3 tahun di Product Tanker (GT 12.000). "
                   "Memegang BST, SCRB, AFF. Terbiasa watchkeeping & mooring.",
    },
    "backoffice": {
        "nama_lengkap": "Dewi Anggraini", "no_telepon": "081322119087",
        "email": "dewi.demo@example.com", "posisi_dilamar": "Finance Staff",
        "pendidikan": "S1", "jurusan": "Akuntansi", "usia": 27,
        "cv_text": "Lulusan S1 Akuntansi. Pengalaman 2 tahun staf keuangan. "
                   "Menguasai Excel, SAP, dan Accounting Software.",
    },
}

@app.route("/candidates/<track>")
def demo_candidate(track):
    """Buat kandidat demo baru dan langsung redirect ke portalnya."""
    payload = _DEMO_PAYLOADS.get(track)
    if not payload:
        abort(404)
    doc = ingest.ingest_payload(payload, send_link=False, verify_cert=False)
    token = db.token_for(doc["id"])
    from flask import redirect
    return redirect(f"/a/{token}")


# ===========================================================================
# PORTAL ASESMEN KANDIDAT
# ===========================================================================

@app.route("/a/<token>")
def portal(token):
    if not db.resolve_token(token):
        return Response("<h2 style='font-family:sans-serif'>Tautan asesmen tidak valid atau kedaluwarsa.</h2>",
                        status=404, mimetype="text/html")
    return send_from_directory(TPL_DIR, "portal.html")


@app.route("/api/assessment/<token>")
def assessment_state(token):
    doc = _doc_or_404_by_token(token)
    track = doc["track"]
    job = job_by_id(doc["jobId"])
    # Job ditampilkan terbatas ke kandidat: hanya judul & info posisi, TANPA bobot/skor.
    pub_job = None
    if job:
        pub_job = {"id": job["id"], "track": job["track"], "title": job["title"]}
        if track == "maritime":
            pub_job["vesselHint"] = job.get("vesselHint")
        else:
            pub_job["dept"] = job.get("dept")
    catalog = {
        "track": track, "job": pub_job,
        "steps": MARITIME_STEPS if track == "maritime" else BACKOFFICE_STEPS,
        "videoQuestions": video_questions_for(track),
    }
    if track == "maritime":
        catalog["quiz"] = MARITIME_QUIZ
    else:
        catalog["tools"] = BACKOFFICE_TOOLS
        # DISC & IQ dikerjakan di platform EKSTERNAL — tidak ada soal yang dikirim ke MAXY.
        catalog["testMode"] = config.TEST_RESULT_MODE          # "api" | "upload"
        catalog["discTestUrl"] = config.DISC_TEST_URL
        catalog["iqTestUrl"] = config.IQ_TEST_URL

    b = doc["bundle"]
    saved_tests = {
        "source": b.get("testSource"),
        "discType": b["disc"].get("type"),
        "discFile": b["disc"].get("resultFileName"),
        "iqScore": b["iq"].get("score") if b.get("testSource") else None,
        "iqFile": b["iq"].get("resultFileName"),
    }
    return jsonify({
        # Subset publik — TANPA skor kecocokan / hasil screening (kandidat tak boleh ubah/lihat).
        "candidate": _public_candidate(doc),
        "catalog": catalog,
        "progress": _progress(doc),
        "savedQuiz": doc["quiz"].get("answers", {}),
        "savedForm": {
            "story": b.get("story", ""),
            "tools": b.get("tools", []),
            "availabilityJoin": b.get("availabilityJoin"),
            "salaryExpIdr": b.get("salaryExpIdr"),
        },
        "availabilityOptions": AVAILABILITY_OPTIONS,
        "savedTests": saved_tests,
        "videoAnswers": [
            {"qIndex": i, "done": True, "url": (a.get("videoUrl") or "")}
            for i, a in enumerate(doc["video"].get("answers", []))
        ],
    })


@app.route("/api/assessment/<token>/quiz", methods=["POST"])
def submit_quiz(token):
    doc = _doc_or_404_by_token(token)
    if doc["track"] != "maritime":
        abort(400, "Kuis ini hanya untuk jalur maritime.")
    data = request.get_json(force=True)
    job = job_by_id(doc["jobId"])
    ans = {"q1": data.get("q1"), "q2": data.get("q2"), "q3": data.get("q3"), "q4": data.get("q4"),
           "_expYears": doc["cv"]["fields"].get("expYears")}
    res = qual_score_maritime(job, ans)
    doc["quiz"] = {"answers": ans, "passFail": res["passFail"], "qualScore": res["score"]}
    recompute(doc)
    db.save_candidate(doc)
    # Skor kualifikasi & pass/fail TIDAK dikembalikan ke kandidat (hanya untuk HR).
    return jsonify({"success": True, "progress": _progress(doc)})


@app.route("/api/assessment/<token>/bundle", methods=["POST"])
def submit_form(token):
    """Form Tambahan (kuesioner posisi). DISC/IQ TIDAK di sini — lihat /tests."""
    doc = _doc_or_404_by_token(token)
    if doc["track"] != "backoffice":
        abort(400, "Asesmen ini hanya untuk jalur back office.")
    job = job_by_id(doc["jobId"])
    data = request.get_json(force=True)
    story = (data.get("story") or "").strip()
    tools = [t for t in (data.get("tools") or []) if t in BACKOFFICE_TOOLS]
    avail = data.get("availabilityJoin") or None
    if avail not in AVAILABILITY_OPTIONS:
        avail = None
    try:
        salary = int(data.get("salaryExpIdr") or 0) or None
    except (TypeError, ValueError):
        salary = None
    doc["bundle"]["story"] = story
    doc["bundle"]["tools"] = tools
    doc["bundle"]["availabilityJoin"] = avail
    doc["bundle"]["salaryExpIdr"] = salary
    doc["bundle"]["formScore"] = calc_form_score(doc["bundle"], job)
    recompute(doc)
    db.save_candidate(doc)
    return jsonify({"success": True, "progress": _progress(doc)})


@app.route("/api/assessment/<token>/tests", methods=["POST"])
def submit_tests(token):
    """Hasil tes DISC/MBTI & IQ dari platform EKSTERNAL.

    Mode 'api'    : tarik hasil otomatis dari provider (tanpa unggahan).
    Mode 'upload' : terima berkas hasil (multipart) + opsional tipe/skor ringkas.
    """
    doc = _doc_or_404_by_token(token)
    if doc["track"] != "backoffice":
        abort(400, "Asesmen ini hanya untuk jalur back office.")

    mode = (request.form.get("mode") or request.values.get("mode")
            or config.TEST_RESULT_MODE)

    if mode == "api":
        pulled = tests_pipeline.pull_results_from_provider(doc)
        tests_pipeline.apply_test_results(doc, source="api",
                                          disc_type=pulled.get("discType"),
                                          iq_score=pulled.get("iqScore"))
        doc = db.get_candidate(doc["id"])
        b = doc["bundle"]
        return jsonify({"success": True, "source": "api",
                        "discType": b["disc"].get("type"), "iqScore": b["iq"].get("score"),
                        "progress": _progress(doc)})

    # mode upload (multipart)
    disc_file = (tests_pipeline.save_uploaded_test_file(doc["id"], "disc", request.files["discFile"])
                 if "discFile" in request.files and request.files["discFile"].filename else None)
    iq_file = (tests_pipeline.save_uploaded_test_file(doc["id"], "iq", request.files["iqFile"])
               if "iqFile" in request.files and request.files["iqFile"].filename else None)
    disc_type = request.form.get("discType") or None
    iq_score = request.form.get("iqScore") or None
    if not (disc_file or iq_file or disc_type or iq_score):
        abort(400, "Mohon unggah berkas hasil tes atau isi ringkasan hasilnya.")
    tests_pipeline.apply_test_results(doc, source="upload",
                                      disc_type=disc_type, iq_score=iq_score,
                                      disc_file=disc_file, iq_file=iq_file)
    doc = db.get_candidate(doc["id"])
    return jsonify({"success": True, "source": "upload", "progress": _progress(doc)})


@app.route("/api/assessment/<token>/video", methods=["POST"])
def submit_video(token):
    doc = _doc_or_404_by_token(token)
    q_index = int(request.form.get("qIndex", 0))
    duration = float(request.form.get("duration", 0) or 0)
    vq = video_questions_for(doc["track"])
    if q_index < 0 or q_index >= len(vq):
        abort(400, "Indeks pertanyaan tidak valid.")
    question = vq[q_index]

    gdrive_url = (request.form.get("gdriveUrl") or "").strip() or None
    response_url = None

    if gdrive_url:
        # Cek dulu apakah link GDrive bisa diakses publik — sinkron, sebelum accept
        check = video_pipeline.check_gdrive_public(gdrive_url)
        if not check["accessible"]:
            return jsonify({"success": False, "error": check["reason"]}), 400

        # Link public → simpan dan langsung proses di background
        fname = f"gdrive_{doc['id']}_q{q_index}"
        threading.Thread(
            target=video_pipeline.process_video_answer,
            args=(doc["id"], q_index, question, fname, duration, doc["track"], 0, gdrive_url),
            daemon=True,
        ).start()
        response_url = gdrive_url

    elif "video" in request.files and request.files["video"].filename:
        # Mode upload file langsung dari browser
        fname = video_pipeline.save_uploaded_video(doc["id"], q_index, request.files["video"])
        threading.Thread(
            target=video_pipeline.process_video_answer,
            args=(doc["id"], q_index, question, fname, duration, doc["track"]),
            daemon=True,
        ).start()
        response_url = f"/media/video/{fname}"
    else:
        abort(400, "Kirim file video (field 'video') atau link Google Drive (field 'gdriveUrl').")

    doc = db.get_candidate(doc["id"])
    return jsonify({"success": True, "url": response_url,
                    "answer": {"qIndex": q_index, "recorded": True},
                    "progress": _progress(doc)})


@app.route("/api/assessment/<token>/schedule")
def assessment_schedule(token):
    """Kembalikan jadwal interview untuk kandidat — hanya info minimal, tanpa data HR internal."""
    doc = _doc_or_404_by_token(token)
    iv = doc["hr"].get("interview", {})
    scheduled = iv.get("scheduledAt")
    if not scheduled:
        return jsonify({"scheduled": False})
    return jsonify({
        "scheduled": True,
        "scheduledAt": scheduled,
        "mode": iv.get("mode", "Online"),
        "location": iv.get("location", ""),
        "notes": iv.get("candidateNote", ""),  # catatan khusus untuk kandidat (opsional)
    })


@app.route("/api/assessment/<token>/complete", methods=["POST"])
def complete_assessment(token):
    doc = _doc_or_404_by_token(token)
    doc["selfServeDone"] = True
    doc["stage"] = "review"
    recompute(doc)
    # Agen bergerak sendiri: begitu asesmen mandiri selesai, SISTEM langsung
    # mengantre verifikasi sertifikat (maritime). HR tidak perlu memicunya —
    # HR hanya melakukan ACC atas hasilnya.
    if doc["track"] == "maritime":
        kode = (doc["cv"]["fields"].get("kodePelaut") or "").strip()
        if kode:
            doc["cert"]["auto"] = True
            doc["cert"]["queuedAt"] = db.now_iso()
            if config.ENABLE_CERT_VERIFY:
                threading.Thread(target=ingest._verify_cert_bg, args=(doc["id"], kode), daemon=True).start()
    links.notify(doc, "Asesmen Anda Telah Lengkap",
                 f"Halo {doc.get('name','')}, seluruh tahapan asesmen mandiri Anda telah kami terima. "
                 f"Tim rekrutmen akan meninjau profil Anda dan menghubungi untuk tahap berikutnya.",
                 channels=("email", "wa"))
    db.save_candidate(doc)
    return jsonify({"success": True, "composite": doc["composite"], "progress": _progress(doc)})


# ===========================================================================
# DEMO: lewati langkah dengan data stub
# ===========================================================================

@app.route("/api/assessment/<token>/skip", methods=["POST"])
def demo_skip(token):
    """Demo only — lewati satu langkah asesmen dengan data stub tanpa proses nyata."""
    doc = _doc_or_404_by_token(token)
    step = (request.get_json(force=True) or {}).get("step", "")
    track = doc["track"]
    job = job_by_id(doc["jobId"])
    vq = video_questions_for(track)

    if step == "quiz" and track == "maritime":
        salary_demo = str(round((job.get("salaryMaxUsd") or 1000) * 0.85))
        exp_demo = doc["cv"]["fields"].get("expYears") or job.get("minExp", 2)
        ans = {"q1": "Ya", "q2": "Segera (≤1 minggu)", "q3": salary_demo, "q4": "Ya",
               "_expYears": exp_demo}
        res = qual_score_maritime(job, ans)
        doc["quiz"] = {"answers": ans, "passFail": res["passFail"], "qualScore": res["score"]}

    elif step == "bundle" and track == "backoffice":
        tools = (job.get("toolsRequired") or BACKOFFICE_TOOLS[:2])
        doc["bundle"]["story"] = "Memiliki pengalaman yang relevan dan rekam jejak yang baik di bidang ini."
        doc["bundle"]["tools"] = tools
        doc["bundle"]["availabilityJoin"] = "Segera (≤1 minggu)"
        doc["bundle"]["salaryExpIdr"] = int((job.get("salaryMaxIdr") or 6_000_000) * 0.85)
        doc["bundle"]["formScore"] = calc_form_score(doc["bundle"], job)

    elif step == "tests" and track == "backoffice":
        doc["bundle"]["disc"] = disc_from_result("I")
        doc["bundle"]["iq"] = iq_from_result(82)
        doc["bundle"]["testSource"] = "demo"

    elif step == "video":
        stub_answers = []
        for qi, q in enumerate(vq):
            transcript = "Selamat pagi. Terkait pertanyaan ini, saya selalu memastikan semua perlengkapan keselamatan (PPE) sudah lengkap sebelum mulai bekerja. Saya pernah menghadapi situasi darurat di kapal sebelumnya, dan hal pertama yang saya lakukan adalah membunyikan alarm, melapor ke anjungan, dan mengikuti muster list sesuai prosedur perusahaan. Saya memprioritaskan keselamatan kru dan kapal."
            summary = "Kandidat merespons dengan tenang, runut, dan sangat percaya diri. Pengetahuan teknis terkait prosedur keselamatan (penggunaan PPE dan Muster List) sangat baik dan diartikulasikan dengan jelas. Bahasa tubuh menunjukkan sikap profesional dan antusiasme tinggi."
            stub_answers.append({
                "question": q, "mode": "demo", "durationSec": 45,
                "transcript": transcript,
                "summary": summary, "aiScore": 85, "videoUrl": None,
            })
            db.save_video_answer(doc["id"], qi, q, None, 45,
                                 transcript,
                                 summary, 85)
        doc["video"]["answers"] = stub_answers
        doc["video"]["aiScore"] = 85

    else:
        abort(400, f"Step '{step}' tidak dikenal atau tidak sesuai jalur.")

    recompute(doc)
    db.save_candidate(doc)
    return jsonify({"success": True, "step": step, "progress": _progress(doc)})


# ===========================================================================
# HR API
# ===========================================================================

@app.route("/api/candidates", methods=["GET"])
def list_candidates():
    track = request.args.get("track")
    q = request.args.get("q")
    docs = db.list_candidates(track=track, q=q)
    for doc in docs:
        token = db.token_for(doc["id"])
        doc["assessment_url"] = links.assessment_url(token) if token else None
    return jsonify({"total": len(docs), "candidates": docs})


@app.route("/api/candidates/<maxy_id>", methods=["GET"])
def get_candidate(maxy_id):
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    return jsonify(doc)


@app.route("/api/candidates/<maxy_id>", methods=["PUT"])
def update_candidate(maxy_id):
    """Simpan perubahan dokumen kandidat dari aksi HR (overwrite + recompute)."""
    existing = db.get_candidate(maxy_id)
    if not existing:
        abort(404, "Kandidat tidak ditemukan")
    incoming = request.get_json(force=True)
    incoming["id"] = maxy_id  # jaga konsistensi id
    # pertahankan metadata internal, data registrasi, & jawaban video yang dikelola server
    incoming["_meta"] = existing.get("_meta", {})
    if "registration" not in incoming and existing.get("registration"):
        incoming["registration"] = existing["registration"]
    recompute(incoming)
    db.save_candidate(incoming)
    return jsonify({"success": True, "candidate": incoming})


@app.route("/api/candidates/<maxy_id>/notify", methods=["POST"])
def notify_candidate(maxy_id):
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    data = request.get_json(force=True)
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    if not subject or not body:
        abort(400, "Subjek dan pesan wajib diisi")
    statuses = links.notify(doc, subject, body, channels=("email", "wa"))
    db.save_candidate(doc)
    return jsonify({"success": True, "statuses": statuses})


@app.route("/api/candidates/<maxy_id>/contract", methods=["POST"])
def make_contract(maxy_id):
    fmt = request.args.get("fmt", "pdf").lower()
    if fmt not in ("pdf", "docx"):
        abort(400, "fmt harus pdf atau docx")
    path = contracts.generate_contract(maxy_id, fmt)
    if not path:
        abort(404, "Kandidat tidak ditemukan")
    fname = os.path.basename(path)
    return jsonify({"success": True, "url": f"/media/contract/{fname}", "filename": fname})


@app.route("/api/candidates/<maxy_id>/verify-cert", methods=["POST"])
def verify_cert(maxy_id):
    """Jalankan verifikasi sertifikat pelaut ke Kemenhub (tugas HR/SISTEM, bukan kandidat).

    Bila ENABLE_CERT_VERIFY aktif & server bisa mengakses pelaut.dephub.go.id, RPA
    (cert_verifier.py) dijalankan di latar. Hasil (Valid/Kadaluarsa/Tidak Ditemukan/
    Error) tersimpan di doc["cert"] dan tampil di dashboard HR. Tanpa akses jaringan,
    endpoint mengembalikan status 'pending' agar UI HR tetap dapat menampilkan alur.
    """
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    if doc["track"] != "maritime":
        abort(400, "Verifikasi sertifikat hanya untuk jalur maritime.")
    data = request.get_json(silent=True) or {}
    kode = (data.get("kodePelaut") or doc["cv"]["fields"].get("kodePelaut") or "").strip()
    if not kode:
        abort(400, "Kode pelaut tidak tersedia pada data pendaftaran kandidat.")
    doc["cv"]["fields"]["kodePelaut"] = kode
    db.save_candidate(doc)
    if config.ENABLE_CERT_VERIFY:
        threading.Thread(target=ingest._verify_cert_bg, args=(doc["id"], kode), daemon=True).start()
        return jsonify({"success": True, "status": "running", "kodePelaut": kode,
                        "message": "Verifikasi RPA dijalankan ke pelaut.dephub.go.id. "
                                   "Hasil akan muncul di kartu kandidat."})
    return jsonify({"success": True, "status": "pending", "kodePelaut": kode,
                    "message": "ENABLE_CERT_VERIFY nonaktif / server tidak dapat mengakses "
                               "Kemenhub dari lingkungan ini. Aktifkan saat di-deploy."})


@app.route("/api/candidates/<maxy_id>/bgcheck", methods=["POST"])
def save_bgcheck(maxy_id):
    """Simpan hasil background check (HR only — tidak terekspos ke portal kandidat)."""
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    data = request.get_json(force=True)
    bg = doc["hr"].get("bgCheck", {})
    bg["ref1Name"]    = data.get("ref1Name", bg.get("ref1Name", ""))
    bg["ref1Result"]  = data.get("ref1Result", bg.get("ref1Result", ""))
    bg["ref2Name"]    = data.get("ref2Name", bg.get("ref2Name", ""))
    bg["ref2Result"]  = data.get("ref2Result", bg.get("ref2Result", ""))
    bg["lastTenure"]  = data.get("lastTenure", bg.get("lastTenure", ""))
    bg["resignReason"]= data.get("resignReason", bg.get("resignReason", ""))
    bg["rehire"]      = data.get("rehire", bg.get("rehire", ""))
    if data.get("done") is not None:
        bg["done"] = bool(data["done"])
    doc["hr"]["bgCheck"] = bg
    db.save_candidate(doc)
    return jsonify({"success": True, "bgCheck": bg})


@app.route("/api/candidates/<maxy_id>/mcu", methods=["PUT", "POST"])
def save_mcu(maxy_id):
    """Update status Medical Check-Up (maritime only, HR only)."""
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    if doc["track"] != "maritime":
        abort(400, "MCU hanya untuk jalur maritime.")
    data = request.get_json(force=True)
    mcu = doc["hr"].get("mcu", {})
    if data.get("uploaded") is not None:
        mcu["uploaded"] = bool(data["uploaded"])
    if data.get("validUntil") is not None:
        mcu["validUntil"] = data["validUntil"]
    if data.get("fitForDuty") is not None:
        mcu["fitForDuty"] = data["fitForDuty"]   # True / False / None
    if data.get("done") is not None:
        mcu["done"] = bool(data["done"])
    doc["hr"]["mcu"] = mcu
    db.save_candidate(doc)
    return jsonify({"success": True, "mcu": mcu})


@app.route("/api/candidates/<maxy_id>/schedule", methods=["POST"])
def schedule_interview(maxy_id):
    """HR menjadwalkan interview + opsional kirim notifikasi ke kandidat."""
    doc = db.get_candidate(maxy_id)
    if not doc:
        abort(404, "Kandidat tidak ditemukan")
    data = request.get_json(force=True)
    iv = doc["hr"].get("interview", {})
    iv["scheduledAt"]   = data.get("scheduledAt") or iv.get("scheduledAt")
    iv["mode"]          = data.get("mode", iv.get("mode", "Online"))
    iv["location"]      = data.get("location", iv.get("location", ""))
    iv["candidateNote"] = data.get("candidateNote", iv.get("candidateNote", ""))
    doc["hr"]["interview"] = iv
    db.save_candidate(doc)
    # Kirim notifikasi ke kandidat bila diminta
    if data.get("notify") and iv.get("scheduledAt"):
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(iv["scheduledAt"])
            tgl = dt.strftime("%d %B %Y pukul %H:%M")
        except Exception:
            tgl = iv["scheduledAt"]
        mode_str = iv.get("mode", "Online")
        loc_str = f" di {iv['location']}" if iv.get("location") else ""
        body = (f"Halo {doc.get('name','')},\n\n"
                f"Selamat! Anda berhasil lolos tahap asesmen mandiri dan dijadwalkan untuk wawancara:\n\n"
                f"📅 Waktu: {tgl}\n"
                f"📍 Mode: {mode_str}{loc_str}\n")
        if iv.get("candidateNote"):
            body += f"\nCatatan: {iv['candidateNote']}\n"
        body += "\nMohon hadir tepat waktu. Salam, Tim HR PT Lintas Maritim Indonesia."
        links.notify(doc, "Undangan Wawancara — PT Lintas Maritim Indonesia", body,
                     channels=("email", "wa"))
        db.save_candidate(doc)
    return jsonify({"success": True, "interview": iv})


@app.route("/api/stats", methods=["GET"])
def stats():
    docs = db.list_candidates()
    by_col = {}
    for d in docs:
        col = hr_column(d)
        by_col[col] = by_col.get(col, 0) + 1
    return jsonify({
        "total": len(docs),
        "by_track": {"maritime": sum(1 for d in docs if d["track"] == "maritime"),
                     "backoffice": sum(1 for d in docs if d["track"] == "backoffice")},
        "by_column": by_col,
        "hired": sum(1 for d in docs if d["hr"].get("final") == "hired"),
    })


# ===========================================================================
# MEDIA
# ===========================================================================

@app.route("/media/video/<path:filename>")
def media_video(filename):
    path = os.path.join(config.VIDEO_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, conditional=True)  # mendukung range request (seek video)


@app.route("/media/contract/<path:filename>")
def media_contract(filename):
    path = os.path.join(config.CONTRACT_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True)


@app.route("/media/test/<path:filename>")
def media_test(filename):
    """Berkas hasil tes DISC/IQ yang diunggah kandidat — dibuka HR untuk ditinjau."""
    path = os.path.join(config.TEST_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, conditional=True)


# ===========================================================================
# Halaman & utilitas
# ===========================================================================

@app.route("/hr")
def hr_console():
    return send_from_directory(TPL_DIR, "hr.html")


@app.route("/")
def index():
    return send_from_directory(TPL_DIR, "index.html")


@app.route("/api/pubconfig")
def pub_config():
    return jsonify({"google_client_id": os.getenv("GOOGLE_CLIENT_ID", "")})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "config": config.masked_summary(),
                    "candidates": len(db.list_candidates())})


def _retention_loop():
    """Pembersih video kedaluwarsa (sederhana, jalan saat startup)."""
    try:
        video_pipeline.purge_expired_videos()
    except Exception as e:
        log.error(f"Retention purge error: {e}")


def main():
    parser = argparse.ArgumentParser(description="MAXY Server")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    _retention_loop()
    log.info(f"MAXY berjalan di http://{args.host}:{args.port}")
    log.info(f"  Portal kandidat : {config.PUBLIC_BASE_URL}/a/<token>")
    log.info(f"  Dashboard HR    : {config.PUBLIC_BASE_URL}/hr")
    log.info(f"  Webhook crewing : {config.PUBLIC_BASE_URL}/webhook/crewing")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
