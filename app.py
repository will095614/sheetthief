# -*- coding: utf-8 -*-
"""
鋼琴五線譜手動擷取工具 - 網頁伺服器 (本地端 / 也可部署到伺服器)
"""

import os
import uuid
import shutil
import threading
import traceback
import re

from flask import (
    Flask, request, jsonify, send_file, render_template, url_for,
    redirect, make_response,
)

import pipeline
import downloader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(BASE_DIR, "_work")
os.makedirs(WORK_DIR, exist_ok=True)

ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
COOKIE_NAME = "pte_access_code"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024

JOBS = {}
JOBS_LOCK = threading.Lock()

def job_dir(job_id):
    d = os.path.join(WORK_DIR, job_id)
    os.makedirs(d, exist_ok=True)
    return d

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

def _finish_preparing(job_id, video_path):
    d = job_dir(job_id)
    os.makedirs(os.path.join(d, "crops"), exist_ok=True)
    duration, width, height, fps = pipeline.get_video_info(video_path)

    try:
        crop_box = pipeline.suggest_crop_box(video_path, duration)
    except Exception:
        crop_box = {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.42)}

    preview_t = duration * 0.4
    preview_img = pipeline.grab_frame_at(video_path, preview_t)
    preview_path = os.path.join(d, "preview.jpg")
    if preview_img is not None:
        preview_img.save(preview_path, quality=88)

    with JOBS_LOCK:
        JOBS[job_id].update({
            "status": "ready",
            "progress": 100,
            "message": "",
            "video_path": video_path,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "suggested_crop": crop_box,
            "captured_sections": [],
        })

@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "沒有收到影片檔案"}), 400

    job_id = uuid.uuid4().hex[:12]
    d = job_dir(job_id)
    ext = os.path.splitext(f.filename)[1] or ".mp4"
    video_path = os.path.join(d, "source" + ext)
    f.save(video_path)

    with JOBS_LOCK:
        JOBS[job_id] = {"status": "preparing", "progress": 0, "message": ""}

    try:
        _finish_preparing(job_id, video_path)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return jsonify({"error": "無法讀取影片：{}".format(e)}), 400

    with JOBS_LOCK:
        job = JOBS[job_id]

    return jsonify({
        "job_id": job_id,
        "duration": job["duration"],
        "width": job["width"],
        "height": job["height"],
        "suggested_crop": job["suggested_crop"],
        "preview_url": url_for("api_preview_image", job_id=job_id),
    })

def _run_url_download(job_id, url):
    d = job_dir(job_id)
    def progress_cb(pct, msg):
        with JOBS_LOCK:
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["message"] = msg
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "downloading"
        video_path = downloader.download_video_from_url(url, d, progress_cb=progress_cb)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "preparing"
            JOBS[job_id]["message"] = "分析影片中..."
        _finish_preparing(job_id, video_path)
    except downloader.UnsafeURLError as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
    except Exception as e:
        traceback.print_exc()
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "下載或分析失敗：{}".format(e)

@app.route("/api/from_url", methods=["POST"])
def api_from_url():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "請輸入影片網址"}), 400
    try:
        downloader.assert_public_http_url(url)
    except downloader.UnsafeURLError as e:
        return jsonify({"error": str(e)}), 400
    job_id = uuid.uuid4().hex[:12]
    job_dir(job_id)
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "downloading", "progress": 0, "message": "準備下載..."}
    th = threading.Thread(target=_run_url_download, args=(job_id, url), daemon=True)
    th.start()
    return jsonify({"job_id": job_id})

@app.route("/api/preview/<job_id>")
def api_preview_image(job_id):
    d = job_dir(job_id)
    p = os.path.join(d, "preview.jpg")
    if not os.path.exists(p):
        return jsonify({"error": "找不到預覽畫面"}), 404
    return send_file(p, mimetype="image/jpeg")

@app.route("/api/preview_at", methods=["POST"])
def api_preview_at():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    t = float(data.get("t", 0))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400
    d = job_dir(job_id)
    p = os.path.join(d, "preview.jpg")
    img.save(p, quality=88)
    return jsonify({"preview_url": url_for("api_preview_image", job_id=job_id) + "?t=" + str(t)})

@app.route("/api/capture", methods=["POST"])
def api_capture():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    t = float(data.get("t", 0))
    crop_box = data.get("crop_box")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400

    try:
        crop = pipeline._crop(img, crop_box)
    except Exception as e:
        return jsonify({"error": "裁切畫面失敗：{}".format(e)}), 400

    d = job_dir(job_id)
    fname = "capture_{:.1f}_{}.png".format(t, uuid.uuid4().hex[:6])
    p = os.path.join(d, "crops", fname)
    crop.save(p, quality=100)
    
    with JOBS_LOCK:
        if "captured_sections" not in job:
            job["captured_sections"] = []
        job["captured_sections"].append({"t": t, "filename": fname})

    return jsonify({
        "filename": fname,
        "url": url_for("api_download_section", job_id=job_id, filename=fname)
    })

@app.route("/api/preview_enhance", methods=["POST"])
def api_preview_enhance():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    crop_box = data.get("crop_box")
    t = float(data.get("t", 0))

    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404
    if not crop_box:
        return jsonify({"error": "缺少框選區域"}), 400

    img = pipeline.grab_frame_at(job["video_path"], t)
    if img is None:
        return jsonify({"error": "抓取畫面失敗"}), 400

    try:
        crop = pipeline._crop(img, crop_box)
        enhanced = pipeline.enhance_quality(crop)
    except Exception as e:
        return jsonify({"error": "產生增強預覽失敗：{}".format(e)}), 400

    d = job_dir(job_id)
    p = os.path.join(d, "preview_enhance.jpg")
    enhanced.save(p, quality=92)
    return jsonify({
        "preview_url": url_for("api_preview_enhance_image", job_id=job_id) + "?t=" + str(t)
    })

@app.route("/api/preview_enhance_image/<job_id>")
def api_preview_enhance_image(job_id):
    d = job_dir(job_id)
    p = os.path.join(d, "preview_enhance.jpg")
    if not os.path.exists(p):
        return jsonify({"error": "尚未產生預覽"}), 404
    return send_file(p, mimetype="image/jpeg")

@app.route("/api/build_pdf", methods=["POST"])
def api_build_pdf():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or "video_path" not in job:
        return jsonify({"error": "找不到工作"}), 404

    filenames = data.get("filenames", [])
    if not filenames:
        return jsonify({"error": "沒有選擇任何擷取畫面"}), 400

    d = job_dir(job_id)
    paths = []
    for fname in filenames:
        p = os.path.join(d, "crops", fname)
        if os.path.exists(p):
            paths.append(p)
            
    if not paths:
        return jsonify({"error": "找不到圖片檔案"}), 400

    title = data.get("title") or "鋼琴五線譜"
    subtitle = (data.get("subtitle") or "").strip()
    page_size = data.get("page_size") or "A4"
    enhance = bool(data.get("enhance", False))

    pdf_path = os.path.join(d, "piano_score.pdf")

    try:
        pipeline.build_pdf(paths, pdf_path, title=title, subtitle=subtitle, page_size=page_size, enhance=enhance)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "產生 PDF 失敗：{}".format(e)}), 500

    with JOBS_LOCK:
        job["result"] = {
            "pdf_path": pdf_path,
            "kept_count": len(paths),
            "title": title
        }
        job["status"] = "done"

    return jsonify({
        "pdf_url": url_for("api_download_pdf", job_id=job_id),
        "kept_count": len(paths)
    })

@app.route("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "找不到工作"}), 404

    resp = {
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
    }

    if job["status"] in ("ready", "queued", "running", "done") and "duration" in job:
        resp["duration"] = job["duration"]
        resp["width"] = job["width"]
        resp["height"] = job["height"]
        resp["suggested_crop"] = job["suggested_crop"]
        resp["preview_url"] = url_for("api_preview_image", job_id=job_id)

    if job["status"] == "done":
        result = job.get("result", {})
        resp["kept_count"] = result.get("kept_count", 0)
        resp["pdf_url"] = url_for("api_download_pdf", job_id=job_id)

    if job["status"] == "error":
        resp["error"] = job.get("error", "未知錯誤")
    return jsonify(resp)

@app.route("/api/download/<job_id>/pdf")
def api_download_pdf(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "尚未完成"}), 404
    
    pdf_path = job["result"]["pdf_path"]
    title = job["result"].get("title", "piano_score")
    
    # Filter out invalid characters for Windows/Mac filenames
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    if not safe_title:
        safe_title = "piano_score"
        
    return send_file(pdf_path, as_attachment=True, download_name=f"{safe_title}.pdf")

@app.route("/api/download/<job_id>/section/<filename>")
def api_download_section(job_id, filename):
    d = job_dir(job_id)
    p = os.path.join(d, "crops", filename)
    if not os.path.exists(p):
        return jsonify({"error": "找不到檔案"}), 404
    return send_file(p, mimetype="image/png")

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5002))
    print("=" * 50)
    print(" 鋼琴五線譜手動擷取工具已啟動")
    if host == "127.0.0.1":
        print(" 請在瀏覽器開啟： http://127.0.0.1:{}".format(port))
    else:
        print(" 監聽於 {}:{}".format(host, port))
    if ACCESS_CODE:
        print(" 已啟用存取碼保護")
    else:
        print(" 未設定 ACCESS_CODE，任何能連到這個網址的人都能直接使用")
    print("=" * 50)
    app.run(host=host, port=port, debug=False, threaded=True)
