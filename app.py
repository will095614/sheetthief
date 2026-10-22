# -*- coding: utf-8 -*-
"""
鋼琴五線譜擷取工具 - 網頁伺服器 (本地端 / 也可部署到伺服器)
"""

import glob
import os
import re
import shutil
import threading
import time
import traceback
import uuid

from flask import (
    Flask, request, jsonify, send_file, render_template, url_for,
    redirect, make_response,
)
from werkzeug.exceptions import HTTPException

import pipeline
import downloader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(BASE_DIR, "_work")
os.makedirs(WORK_DIR, exist_ok=True)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
COOKIE_NAME = "pte_access_code"

JOB_TTL_SECONDS = 12 * 60 * 60      # 12 小時沒動就清掉，避免暫存資料夾無限長大
MAX_TITLE_LEN = 120
MAX_SUBTITLE_LEN = 200

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024

JOBS = {}
JOBS_LOCK = threading.Lock()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SAFE_TITLE_RE = re.compile(r'[\\/*?:"<>|\r\n\t]')


# ---------------------------------------------------------------------------
# 工作與檔案管理
# ---------------------------------------------------------------------------

def job_dir(job_id):
    d = os.path.join(WORK_DIR, job_id)
    os.makedirs(d, exist_ok=True)
    return d


def _valid_job_id(job_id):
    return bool(job_id) and bool(_JOB_ID_RE.match(job_id))


def _get_job(job_id):
    if not _valid_job_id(job_id):
        return None
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _clean_crop_box(box, video_w, video_h):
    if not isinstance(box, dict):
        return None
    try:
        x = int(round(float(box["x"])))
        y = int(round(float(box["y"])))
        w = int(round(float(box["w"])))
        h = int(round(float(box["h"])))
    except (KeyError, TypeError, ValueError):
        return None
    if w < 16 or h < 16:
        return None
    x = max(0, min(x, max(0, video_w - 16)))
    y = max(0, min(y, max(0, video_h - 16)))
    w = max(16, min(w, video_w - x))
    h = max(16, min(h, video_h - y))
    return {"x": x, "y": y, "w": w, "h": h}


def _section_path(job_id, filename):
    """
    回傳 crops 資料夾裡的檔案路徑；任何可能跳脫資料夾的名稱一律視為無效。
    這是為了擋掉 ../../ 之類的路徑穿越。
    """
    if not filename or not _FILENAME_RE.match(filename):
        return None
    base = os.path.abspath(os.path.join(job_dir(job_id), "crops"))
    path = os.path.abspath(os.path.join(base, filename))
    if os.path.dirname(path) != base:
        return None
    return path


def _sections_payload(job_id, job):
    out = []
    for s in job.get("captured_sections", []):
        path = _section_path(job_id, s["filename"])
        if not path or not os.path.exists(path):
            continue
        out.append({
            "t": s["t"],
            "filename": s["filename"],
            "auto": bool(s.get("auto")),
            "score": float(s.get("score") or 0.0),
            "url": url_for("api_download_section", job_id=job_id, filename=s["filename"]),
        })
    return out


def _append_section(job_id, filename, t, auto=False, score=0.0):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.setdefault("captured_sections", []).append({
            "t": float(t),
            "filename": filename,
            "auto": bool(auto),
            "score": float(score),
        })


def _delete_section_file(job_id, filename):
    path = _section_path(job_id, filename)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _prune_old_jobs():
    """清掉過期的工作與孤兒暫存資料夾 (伺服器重開後 JOBS 會清空)"""
    now = time.time()
    stale = []
    with JOBS_LOCK:
        for jid, job in list(JOBS.items()):
            if now - float(job.get("created_at", now)) > JOB_TTL_SECONDS:
                stale.append(jid)
                JOBS.pop(jid, None)
    for jid in stale:
        d = os.path.join(WORK_DIR, jid)
        shutil.rmtree(d, ignore_errors=True)

    try:
        for name in os.listdir(WORK_DIR):
            path = os.path.join(WORK_DIR, name)
            if not os.path.isdir(path):
                continue
            if not _valid_job_id(name):
                continue
            with JOBS_LOCK:
                alive = name in JOBS
            if alive:
                continue
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if age > 2 * 60 * 60:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _send_no_cache(path, mimetype):
    resp = make_response(send_file(path, mimetype=mimetype, max_age=0))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ---------------------------------------------------------------------------
# 存取碼保護
# ---------------------------------------------------------------------------

def _authorized(req):
    if not ACCESS_CODE:
        return True
    return req.cookies.get(COOKIE_NAME) == ACCESS_CODE


@app.before_request
def _check_access():
    if not ACCESS_CODE:
        return None
    if request.path in ("/api/login", "/login") or request.path.startswith("/static/"):
        return None
    if not _authorized(request):
        if request.path.startswith("/api/"):
            return jsonify({"error": "需要存取碼", "need_login": True}), 401
        return redirect("/login")
    return None


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        if code == ACCESS_CODE:
            resp = make_response(redirect("/"))
            resp.set_cookie(COOKIE_NAME, code, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
            return resp
        error = "存取碼錯誤"
    return render_template("login.html", error=error)


@app.route("/")
def index():
    return render_template("index.html")


@app.errorhandler(413)
def _too_large(_e):
    return jsonify({"error": "影片檔案太大 (上限 4GB)，請先壓縮或改用「貼上網址」"}), 413


@app.errorhandler(Exception)
def _handle_uncaught(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e
    traceback.print_exc()
    if request.path.startswith("/api/"):
        return jsonify({"error": "伺服器發生錯誤：{}".format(e)}), 500
    return "伺服器發生錯誤，請回到首頁重試", 500


# ---------------------------------------------------------------------------
# 影片上傳 / 下載
# ---------------------------------------------------------------------------

def _new_job():
    job_id = uuid.uuid4().hex[:12]
    job_dir(job_id)
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "preparing",
            "progress": 0,
            "message": "",
            "created_at": time.time(),
            "captured_sections": [],
            "scan": {"status": "idle", "progress": 0, "message": "", "found": 0, "error": None},
        }
    return job_id


def _finish_preparing(job_id, video_path):
    d = job_dir(job_id)
    os.makedirs(os.path.join(d, "crops"), exist_ok=True)
    duration, width, height, fps = pipeline.get_video_info(video_path)
    if width <= 0 or height <= 0:
        raise RuntimeError("讀不到影片畫面，請確認檔案沒有損壞")

    try:
        layout = pipeline.suggest_layout(video_path, duration)
        crop_box = layout["crop"]
        best_t = layout["best_t"]
        best_score = layout["score"]
    except Exception:
        traceback.print_exc()
        crop_box = {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.42)}
        best_t = duration * 0.4
        best_score = 0.0

    preview_img = pipeline.grab_frame_at(video_path, best_t)
    if preview_img is None:
        preview_img = pipeline.grab_frame_at(video_path, 0)
    if preview_img is not None:
        preview_img.save(os.path.join(d, "preview.jpg"), quality=88)

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job.update({
            "status": "ready",
            "progress": 100,
            "message": "",
            "video_path": video_path,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "suggested_crop": crop_box,
            "suggested_t": float(best_t),
            "suggested_score": float(best_score),
        })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "沒有收到影片檔案"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    allowed_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".ts", ".m4v"}
    if ext not in allowed_exts:
        return jsonify({"error": "不支援的檔案格式，請上傳影片檔案 (mp4/mov/mkv 等)"}), 400

    _prune_old_jobs()
    job_id = _new_job()
    d = job_dir(job_id)
    video_path = os.path.join(d, "source" + ext)
    f.save(video_path)

    try:
        _finish_preparing(job_id, video_path)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return jsonify({"error": "無法讀取影片：{}".format(e)}), 400

    job = _get_job(job_id)
    return jsonify({
        "job_id": job_id,
        "duration": job["duration"],
        "width": job["width"],
        "height": job["height"],
        "suggested_crop": job["suggested_crop"],
        "suggested_t": job["suggested_t"],
        "suggested_score": job["suggested_score"],
        "preview_url": url_for("api_preview_image", job_id=job_id),
        "sections": [],
    })


def _run_url_download(job_id, url):
    d = job_dir(job_id)

    def progress_cb(pct, msg):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return
            job["progress"] = pct
            job["message"] = msg

    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "downloading"
        video_path = downloader.download_video_from_url(url, d, progress_cb=progress_cb)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "preparing"
            JOBS[job_id]["message"] = "分析影片中..."
            JOBS[job_id]["progress"] = 100
        _finish_preparing(job_id, video_path)
    except downloader.UnsafeURLError as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = "下載或分析失敗：{}".format(e)


@app.route("/api/from_url", methods=["POST"])
def api_from_url():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "請輸入影片網址"}), 400
    try:
        downloader.assert_public_http_url(url)
    except downloader.UnsafeURLError as e:
        return jsonify({"error": str(e)}), 400

    _prune_old_jobs()
    job_id = _new_job()
    with JOBS_LOCK:
        JOBS[job_id].update({"status": "downloading", "message": "準備下載..."})
    th = threading.Thread(target=_run_url_download, args=(job_id, url), daemon=True)
    th.start()
    return jsonify({"job_id": job_id})


# ---------------------------------------------------------------------------
# 預覽
# ---------------------------------------------------------------------------

@app.route("/api/preview/<job_id>")
def api_preview_image(job_id):
    if not _get_job(job_id):
        return jsonify({"error": "找不到工作"}), 404
    p = os.path.join(job_dir(job_id), "preview.jpg")
    if not os.path.exists(p):
        return jsonify({"error": "找不到預覽畫面"}), 404
    return _send_no_cache(p, "image/jpeg")


@app.route("/api/preview_at", methods=["POST"])
def api_preview_at():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    try:
        t = max(0.0, float(data.get("t", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "時間點格式錯誤"}), 400
    if job.get("duration"):
        t = min(t, float(job["duration"]))

    crop_box = _clean_crop_box(data.get("crop_box"), job["width"], job["height"]) \
        or job.get("suggested_crop")

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗，請換個時間點試試"}), 400

    feat = {"score": 0.0, "is_score": False, "lines": 0}
    if crop_box:
        try:
            feat = pipeline.analyze_score_frame(pipeline.crop_image(img, crop_box))
        except Exception:
            pass

    d = job_dir(job_id)
    # 每次都存成不同檔名：快速拖動時間軸時才不會互相覆蓋，看到錯的畫面
    fname = "preview_{}.jpg".format(uuid.uuid4().hex[:8])
    img.save(os.path.join(d, fname), quality=88)
    for old in sorted(glob.glob(os.path.join(d, "preview_*.jpg")), key=os.path.getmtime)[:-6]:
        try:
            os.remove(old)
        except OSError:
            pass

    return jsonify({
        "preview_url": url_for("api_preview_file", job_id=job_id, filename=fname),
        "t": t,
        "score": float(feat["score"]),
        "is_score": bool(feat.get("is_score")),
        "lines": int(feat.get("lines", 0)),
    })


@app.route("/api/preview_file/<job_id>/<filename>")
def api_preview_file(job_id, filename):
    if not _get_job(job_id) or not _FILENAME_RE.match(filename or ""):
        return jsonify({"error": "找不到預覽畫面"}), 404
    p = os.path.join(job_dir(job_id), filename)
    if not os.path.isfile(p):
        return jsonify({"error": "找不到預覽畫面"}), 404
    return _send_no_cache(p, "image/jpeg")


# ---------------------------------------------------------------------------
# 手動擷取
# ---------------------------------------------------------------------------

@app.route("/api/capture", methods=["POST"])
def api_capture():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    try:
        t = max(0.0, float(data.get("t", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "時間點格式錯誤"}), 400

    crop_box = _clean_crop_box(data.get("crop_box"), job["width"], job["height"])
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗，請換個時間點試試"}), 400

    crop = pipeline.crop_image(img, crop_box)
    fname = "capture_{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
    crops_dir = os.path.join(job_dir(job_id), "crops")
    os.makedirs(crops_dir, exist_ok=True)
    crop.save(os.path.join(crops_dir, fname))

    try:
        feat = pipeline.analyze_score_frame(crop)
    except Exception:
        feat = {"score": 0.0, "is_score": False, "lines": 0}

    _append_section(job_id, fname, t, auto=False, score=feat["score"])
    job = _get_job(job_id)

    return jsonify({
        "filename": fname,
        "url": url_for("api_download_section", job_id=job_id, filename=fname),
        "sections": _sections_payload(job_id, job),
        "score": float(feat["score"]),
        "is_score": bool(feat.get("is_score")),
        "lines": int(feat.get("lines", 0)),
    })


@app.route("/api/sections/remove", methods=["POST"])
def api_sections_remove():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404
    filename = data.get("filename")
    with JOBS_LOCK:
        sections = job.get("captured_sections", [])
        job["captured_sections"] = [s for s in sections if s["filename"] != filename]
    _delete_section_file(job_id, filename)
    return jsonify({"sections": _sections_payload(job_id, job)})


@app.route("/api/sections/reorder", methods=["POST"])
def api_sections_reorder():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404

    order = data.get("filenames") or []
    with JOBS_LOCK:
        sections = job.get("captured_sections", [])
        by_name = {s["filename"]: s for s in sections}
        ordered = [by_name[f] for f in order if f in by_name]
        ordered += [s for s in sections if s["filename"] not in order]
        job["captured_sections"] = ordered
    return jsonify({"sections": _sections_payload(job_id, job)})


@app.route("/api/sections/clear", methods=["POST"])
def api_sections_clear():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404
    only_auto = bool(data.get("only_auto"))
    removed = []
    with JOBS_LOCK:
        sections = job.get("captured_sections", [])
        if only_auto:
            removed = [s["filename"] for s in sections if s.get("auto")]
            job["captured_sections"] = [s for s in sections if not s.get("auto")]
        else:
            removed = [s["filename"] for s in sections]
            job["captured_sections"] = []
    for fname in removed:
        _delete_section_file(job_id, fname)
    return jsonify({"sections": _sections_payload(job_id, job)})


# ---------------------------------------------------------------------------
# 自動掃描整部影片
# ---------------------------------------------------------------------------

def _clear_auto_sections(job_id):
    removed = []
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        sections = job.get("captured_sections", [])
        removed = [s["filename"] for s in sections if s.get("auto")]
        job["captured_sections"] = [s for s in sections if not s.get("auto")]
    for fname in removed:
        _delete_section_file(job_id, fname)


def _run_auto_scan(job_id, crop_box, interval, sensitivity):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        video_path = job["video_path"]
        cancel = job["scan_cancel"]

    def progress(pct, msg):
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j is None:
                return
            j["scan"]["progress"] = max(0, min(88, int(pct * 0.88)))
            j["scan"]["message"] = msg

    try:
        found = pipeline.auto_scan_video(
            video_path, crop_box,
            interval=interval, sensitivity=sensitivity,
            progress_cb=progress, cancel_cb=cancel.is_set,
        )

        # 完整掃描才取代舊結果；中途按停止時保留上一次找到的頁面
        if not cancel.is_set():
            _clear_auto_sections(job_id)

        with JOBS_LOCK:
            existing_times = [s["t"] for s in JOBS[job_id].get("captured_sections", [])]

        crops_dir = os.path.join(job_dir(job_id), "crops")
        os.makedirs(crops_dir, exist_ok=True)
        saved = 0
        total = max(1, len(found))
        for i, item in enumerate(found):
            if cancel.is_set():
                break
            # 已經手動擷取過的時間點就不要重複加
            if any(abs(item["t"] - t) < max(1.5, interval * 0.75) for t in existing_times):
                continue
            img = pipeline.grab_frame_at(video_path, item["t"])
            if img is None:
                continue
            crop = pipeline.crop_image(img, crop_box)
            fname = "auto_{:03d}_{:.1f}.png".format(i + 1, item["t"])
            crop.save(os.path.join(crops_dir, fname))
            _append_section(job_id, fname, item["t"], auto=True, score=item["score"])
            saved += 1
            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if j is not None:
                    j["scan"]["progress"] = 88 + int((i + 1) / total * 12)
                    j["scan"]["found"] = saved
                    j["scan"]["message"] = "整理譜面中... ({} 頁)".format(saved)

        # 自動掃描的結果依影片時間排序，拼出來的樂譜順序才會正確
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j is not None:
                j["captured_sections"].sort(key=lambda s: s["t"])

        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j is not None:
                if cancel.is_set():
                    j["scan"].update({
                        "status": "stopped",
                        "message": "已停止（這次新增 {} 頁，原有的掃描結果保留）".format(saved),
                        "found": saved,
                    })
                else:
                    j["scan"].update({
                        "status": "done",
                        "progress": 100,
                        "message": "完成！共找到 {} 頁".format(saved),
                        "found": saved,
                    })
    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j is not None:
                j["scan"].update({"status": "error", "error": str(e),
                                  "message": "自動掃描失敗：{}".format(e)})


@app.route("/api/auto_scan", methods=["POST"])
def api_auto_scan():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    if job.get("scan", {}).get("status") == "running":
        return jsonify({"error": "正在掃描中，請稍候"}), 409

    crop_box = _clean_crop_box(data.get("crop_box"), job["width"], job["height"]) \
        or job.get("suggested_crop")
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    try:
        interval = float(data.get("interval", 2.0))
    except (TypeError, ValueError):
        interval = 2.0
    interval = max(0.5, min(10.0, interval))
    try:
        sensitivity = float(data.get("sensitivity", 50))
    except (TypeError, ValueError):
        sensitivity = 50.0
    sensitivity = max(0.0, min(100.0, sensitivity))

    with JOBS_LOCK:
        job["scan_cancel"] = threading.Event()
        job["scan"] = {
            "status": "running",
            "progress": 0,
            "message": "準備掃描...",
            "found": 0,
            "error": None,
            "interval": interval,
        }

    th = threading.Thread(
        target=_run_auto_scan,
        args=(job_id, crop_box, interval, sensitivity),
        daemon=True,
    )
    th.start()
    return jsonify({"scan": job["scan"], "sections": _sections_payload(job_id, job)})


@app.route("/api/auto_scan/status/<job_id>")
def api_auto_scan_status(job_id):
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404
    return jsonify({
        "scan": job.get("scan", {"status": "idle"}),
        "sections": _sections_payload(job_id, job),
    })


@app.route("/api/auto_scan/stop", methods=["POST"])
def api_auto_scan_stop():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404
    cancel = job.get("scan_cancel")
    if cancel is not None:
        cancel.set()
    return jsonify({"scan": job.get("scan", {}), "sections": _sections_payload(job_id, job)})


# ---------------------------------------------------------------------------
# 增強預覽
# ---------------------------------------------------------------------------

@app.route("/api/preview_enhance", methods=["POST"])
def api_preview_enhance():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    crop_box = _clean_crop_box(data.get("crop_box"), job["width"], job["height"])
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400
    try:
        t = max(0.0, float(data.get("t", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "時間點格式錯誤"}), 400

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400

    enhanced = pipeline.enhance_quality(pipeline.crop_image(img, crop_box))
    d = job_dir(job_id)
    fname = "enhance_{}.jpg".format(uuid.uuid4().hex[:8])
    enhanced.save(os.path.join(d, fname), quality=92)
    for old in sorted(glob.glob(os.path.join(d, "enhance_*.jpg")), key=os.path.getmtime)[:-4]:
        try:
            os.remove(old)
        except OSError:
            pass

    return jsonify({"preview_url": url_for("api_preview_enhance_image", job_id=job_id, filename=fname)})


@app.route("/api/preview_enhance_image/<job_id>/<filename>")
def api_preview_enhance_image(job_id, filename):
    if not _get_job(job_id) or not _FILENAME_RE.match(filename or ""):
        return jsonify({"error": "尚未產生預覽"}), 404
    p = os.path.join(job_dir(job_id), filename)
    if not os.path.isfile(p):
        return jsonify({"error": "尚未產生預覽"}), 404
    return _send_no_cache(p, "image/jpeg")


# ---------------------------------------------------------------------------
# 產生 PDF / 下載
# ---------------------------------------------------------------------------

@app.route("/api/build_pdf", methods=["POST"])
def api_build_pdf():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("job_id")
    job = _get_job(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    known = {s["filename"]: s for s in job.get("captured_sections", [])}
    filenames = data.get("filenames") or list(known.keys())
    if not filenames:
        return jsonify({"error": "沒有選擇任何擷取畫面"}), 400

    d = job_dir(job_id)
    paths = []
    for fname in filenames:
        if fname not in known:
            continue
        path = _section_path(job_id, fname)
        if path and os.path.exists(path):
            paths.append(path)
    if not paths:
        return jsonify({"error": "找不到圖片檔案，請重新擷取"}), 400

    title = (data.get("title") or "鋼琴五線譜").strip()[:MAX_TITLE_LEN]
    subtitle = (data.get("subtitle") or "").strip()[:MAX_SUBTITLE_LEN]
    page_size = data.get("page_size") or "A4"
    if page_size not in pipeline.PAGE_SIZES_PX:
        page_size = "A4"
    enhance = bool(data.get("enhance", False))

    pdf_path = os.path.join(d, "piano_score.pdf")
    try:
        pipeline.build_pdf(paths, pdf_path, title=title, subtitle=subtitle,
                           page_size=page_size, enhance=enhance)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "產生 PDF 失敗：{}".format(e)}), 500

    with JOBS_LOCK:
        job["result"] = {"pdf_path": pdf_path, "kept_count": len(paths), "title": title}
        job["status"] = "done"

    return jsonify({
        "pdf_url": url_for("api_download_pdf", job_id=job_id),
        "kept_count": len(paths),
    })


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404

    resp = {
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "scan": job.get("scan", {"status": "idle"}),
        "sections": _sections_payload(job_id, job),
    }

    if job["status"] in ("ready", "queued", "running", "done") and "duration" in job:
        resp.update({
            "duration": job["duration"],
            "width": job["width"],
            "height": job["height"],
            "suggested_crop": job["suggested_crop"],
            "suggested_t": job.get("suggested_t", 0),
            "suggested_score": job.get("suggested_score", 0),
            "preview_url": url_for("api_preview_image", job_id=job_id),
        })

    if job["status"] == "done":
        result = job.get("result", {})
        resp["kept_count"] = result.get("kept_count", 0)
        resp["pdf_url"] = url_for("api_download_pdf", job_id=job_id)

    if job["status"] == "error":
        resp["error"] = job.get("error", "未知錯誤")
    return jsonify(resp)


@app.route("/api/download/<job_id>/pdf")
def api_download_pdf(job_id):
    job = _get_job(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "尚未完成"}), 404

    pdf_path = job["result"]["pdf_path"]
    title = job["result"].get("title", "piano_score")
    safe_title = _SAFE_TITLE_RE.sub("", title).strip() or "piano_score"
    return send_file(pdf_path, as_attachment=True, download_name="{}.pdf".format(safe_title))


@app.route("/api/download/<job_id>/section/<filename>")
def api_download_section(job_id, filename):
    if not _get_job(job_id):
        return jsonify({"error": "找不到工作"}), 404
    path = _section_path(job_id, filename)
    if not path or not os.path.isfile(path):
        return jsonify({"error": "找不到檔案"}), 404
    return _send_no_cache(path, "image/png")


if __name__ == "__main__":
    _prune_old_jobs()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5002))
    print("=" * 50)
    print(" 鋼琴五線譜擷取工具已啟動")
    if host in ("127.0.0.1", "localhost"):
        print(" 請在瀏覽器開啟： http://127.0.0.1:{}".format(port))
    else:
        print(" 監聽於 {}:{}".format(host, port))
    if ACCESS_CODE:
        print(" 已啟用存取碼保護")
    else:
        print(" 未設定 ACCESS_CODE，任何能連到這個網址的人都能直接使用")
    print("=" * 50)
    app.run(host=host, port=port, debug=False, threaded=True)
