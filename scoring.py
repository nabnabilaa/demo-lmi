"""
scoring.py
==========
Logika penilaian sisi server. Dibuat identik dengan front-end demo agar skor
yang dihitung backend sama persis dengan yang ditampilkan dashboard HR.

Setiap sub-skor 0–100, lalu digabung dengan bobot per-jalur menjadi composite.
"""

from catalog import IQ_QUESTIONS, DISC_LABEL, job_by_id


def _clamp(n, a, b):
    return max(a, min(b, n))


def cv_match_maritime(job: dict, f: dict) -> int:
    score = 50
    exp = f.get("expYears", 0) or 0
    score += 20 if exp >= job["minExp"] else max(0, 20 - (job["minExp"] - exp) * 8)
    gt = f.get("gt", 0) or 0
    score += 10 if (not job.get("gtMin") or gt >= job["gtMin"]) else 4
    score += 10 if (not job.get("ocimf") or f.get("ocimf")) else -4
    have = f.get("certs", []) or []
    req = job["certsRequired"]
    matched = len([c for c in req if c in have])
    score += (matched / len(req)) * 10 if req else 0
    return round(_clamp(score, 0, 100))


def cv_match_backoffice(job: dict, f: dict) -> int:
    score = 45
    rank = {"SMA/SMK": 1, "D3": 2, "S1": 3, "S2": 4}
    score += 18 if rank.get(f.get("edu"), 0) >= rank.get(job["eduMin"], 0) else -8
    age = f.get("age", 0) or 0
    score += 12 if (job["minAge"] <= age <= job["maxAge"]) else -4
    exp = f.get("expYears", 0) or 0
    score += 15 if exp >= job["minExp"] else max(0, 15 - (job["minExp"] - exp) * 6)
    have = f.get("tools", []) or []
    req = job["toolsRequired"]
    matched = len([t for t in req if t in have])
    score += (matched / len(req)) * 10 if req else 0
    return round(_clamp(score, 0, 100))


def cv_match(job: dict, fields: dict) -> int:
    if job["track"] == "maritime":
        return cv_match_maritime(job, fields)
    return cv_match_backoffice(job, fields)


def qual_score_maritime(job: dict, ans: dict) -> dict:
    exp = ans.get("_expYears")
    gates_pass = (ans.get("q1") == "Ya" and ans.get("q4") == "Ya"
                  and (exp is None or exp >= job["minExp"]))
    score = 50 if gates_pass else 15
    avail = {"Segera (≤1 minggu)": 25, "2 minggu": 20, "1 bulan": 12, "Lebih dari 1 bulan": 5}.get(ans.get("q2"), 10)
    score += avail
    salary = float(ans.get("q3") or 0)
    if salary <= job["salaryMaxUsd"]:
        score += 25
    elif salary <= job["salaryMaxUsd"] * 1.1:
        score += 15
    else:
        score += 5
    return {"passFail": gates_pass, "score": round(_clamp(score, 0, 100))}


def calc_disc(answers: dict) -> dict:
    tally = {"D": 0, "I": 0, "S": 0, "C": 0}
    for k in answers.values():
        if k in tally:
            tally[k] += 1
    dtype, mx = "S", -1
    for k in ("D", "I", "S", "C"):
        if tally[k] > mx:
            mx, dtype = tally[k], k
    fit = 72 + mx * 6
    return {"tally": tally, "type": dtype, "label": DISC_LABEL[dtype], "fitScore": round(_clamp(fit, 0, 98))}


def calc_iq(answers: dict) -> dict:
    correct = sum(1 for q in IQ_QUESTIONS if answers.get(q["id"]) == q["answer"])
    return {"correct": correct, "total": len(IQ_QUESTIONS), "score": _clamp(40 + correct * 20, 0, 100)}


def disc_from_result(type_letter: str | None) -> dict:
    """Bangun sub-skor DISC dari HASIL EKSTERNAL (API provider atau unggahan kandidat).

    Tidak ada jawaban per-item di MAXY karena tes dikerjakan di platform pihak ketiga.
    Bila tipe dominan diketahui, kita simpan label + fitScore wajar; bila tidak,
    skor netral sambil menunggu HR menilai berkas hasil yang diunggah.
    """
    t = (type_letter or "").strip().upper()[:1]
    if t in DISC_LABEL:
        tally = {"D": 0, "I": 0, "S": 0, "C": 0}
        tally[t] = 1
        return {"tally": tally, "type": t, "label": DISC_LABEL[t], "fitScore": 85}
    return {"tally": {"D": 0, "I": 0, "S": 0, "C": 0}, "type": None, "label": "", "fitScore": 70}


def iq_from_result(score) -> dict:
    """Sub-skor IQ dari hasil eksternal (skala 0–100 yang dilaporkan provider)."""
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError):
        return {"correct": None, "total": None, "score": 70}  # netral; HR baca berkas
    return {"correct": None, "total": None, "score": _clamp(s, 0, 100)}


def calc_form_score(bundle: dict, job: dict = None) -> int:
    s = 15
    story = (bundle.get("story") or "").strip()
    if len(story) > 40:
        s += 18
    elif len(story) > 0:
        s += 8
    tools = bundle.get("tools", []) or []
    if len(tools) >= 2:
        s += 17
    elif len(tools) == 1:
        s += 8
    avail_score = {
        "Segera (≤1 minggu)": 25, "2 minggu": 20,
        "1 bulan": 12, "Lebih dari 1 bulan": 5,
    }.get(bundle.get("availabilityJoin"), 10)
    s += avail_score
    salary = bundle.get("salaryExpIdr") or 0
    if job and salary:
        max_idr = job.get("salaryMaxIdr", 0) or 0
        if max_idr:
            if salary <= max_idr:
                s += 25
            elif salary <= max_idr * 1.1:
                s += 15
            else:
                s += 5
        else:
            s += 10
    else:
        s += 10
    return round(_clamp(s, 0, 100))


def cert_subscore(status) -> int:
    return {"valid": 100, "expired": 45, "error": 60, "not_found": 15}.get(status, 50)


def recompute(c: dict) -> dict:
    """Hitung ulang composite berdasarkan seluruh sub-skor yang tersedia."""
    if c["track"] == "maritime":
        c["composite"] = round(
            (c["cv"].get("matchScore", 0) or 0) * 0.40
            + (c["video"].get("aiScore", 0) or 0) * 0.30
            + (c["quiz"].get("qualScore", 0) or 0) * 0.20
            + cert_subscore(c["cert"].get("status")) * 0.10
        )
    else:
        b = c["bundle"]
        c["composite"] = round(
            (c["cv"].get("matchScore", 0) or 0) * 0.25
            + (b["disc"].get("fitScore", 0) or 0) * 0.20
            + (b["iq"].get("score", 0) or 0) * 0.15
            + (c["video"].get("aiScore", 0) or 0) * 0.25
            + (b.get("formScore", 0) or 0) * 0.15
        )
    c["cv"]["aiRecommendation"] = ai_recommendation(c["composite"])
    return c


def ai_recommendation(score: int) -> dict:
    if score >= 80:
        return {"label": "Direkomendasikan Lanjut", "cls": "tag-rule"}
    if score >= 60:
        return {"label": "Pertimbangkan dengan Catatan", "cls": "tag-hr"}
    return {"label": "Belum Memenuhi Kriteria", "cls": "tag-red"}


def hr_column(c: dict) -> str:
    if c["hr"].get("final"):
        return "decision"
    if c["hr"].get("stage1") == "advance":
        return "interview"
    if c.get("selfServeDone"):
        return "review"
    return "screening"
