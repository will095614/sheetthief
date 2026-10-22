(function () {
  "use strict";

  var state = {
    jobId: null,
    duration: 0,
    videoW: 0,
    videoH: 0,
    cropBox: null,
    pollTimer: null,
    seekTimer: null,
    previewSeq: 0,
    sections: [],
    scanning: false,
    uploading: false,
    scanTimer: null,
  };

  var el = {
    tabFile: document.getElementById("tabFile"),
    tabUrl: document.getElementById("tabUrl"),
    panelFile: document.getElementById("panelFile"),
    panelUrl: document.getElementById("panelUrl"),
    urlInput: document.getElementById("urlInput"),
    urlSubmitBtn: document.getElementById("urlSubmitBtn"),

    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("fileInput"),
    uploadStatus: document.getElementById("uploadStatus"),
    uploadProgressWrap: document.getElementById("uploadProgressWrap"),
    uploadProgress: document.getElementById("uploadProgress"),

    stepCrop: document.getElementById("step-crop"),
    stepParams: document.getElementById("step-params"),
    stepProgress: document.getElementById("step-progress"),
    stepProgressTitle: document.getElementById("stepProgressTitle"),
    stepResult: document.getElementById("step-result"),

    cropStage: document.getElementById("cropStage"),
    previewImg: document.getElementById("previewImg"),
    previewLoading: document.getElementById("previewLoading"),
    cropBox: document.getElementById("cropBox"),
    detectBadge: document.getElementById("detectBadge"),
    previewSeek: document.getElementById("previewSeek"),
    previewSeekLabel: document.getElementById("previewSeekLabel"),
    captureBtn: document.getElementById("captureBtn"),
    captureStatus: document.getElementById("captureStatus"),

    autoScanBtn: document.getElementById("autoScanBtn"),
    autoScanStopBtn: document.getElementById("autoScanStopBtn"),
    scanProgressWrap: document.getElementById("scanProgressWrap"),
    scanProgress: document.getElementById("scanProgress"),
    scanStatus: document.getElementById("scanStatus"),
    intervalInput: document.getElementById("intervalInput"),
    sensitivityInput: document.getElementById("sensitivityInput"),
    sensitivityLabel: document.getElementById("sensitivityLabel"),

    galleryWrap: document.getElementById("galleryWrap"),
    galleryCount: document.getElementById("galleryCount"),
    gallery: document.getElementById("gallery"),
    clearAllBtn: document.getElementById("clearAllBtn"),

    titleInput: document.getElementById("titleInput"),
    subtitleInput: document.getElementById("subtitleInput"),
    pageSizeInput: document.getElementById("pageSizeInput"),
    enhanceInput: document.getElementById("enhanceInput"),
    enhancePreviewBtn: document.getElementById("enhancePreviewBtn"),
    enhancePreviewWrap: document.getElementById("enhancePreviewWrap"),
    enhancePreviewImg: document.getElementById("enhancePreviewImg"),
    startBtn: document.getElementById("startBtn"),
    startHint: document.getElementById("startHint"),

    progressBar: document.getElementById("progressBar"),
    progressMsg: document.getElementById("progressMsg"),

    resultSummary: document.getElementById("resultSummary"),
    downloadBtn: document.getElementById("downloadBtn"),
    restartBtn: document.getElementById("restartBtn"),

    errorBox: document.getElementById("errorBox"),

    imageModal: document.getElementById("imageModal"),
    modalImg: document.getElementById("modalImg"),
    imageModalClose: document.getElementById("imageModalClose"),
  };

  // ---------------------------------------------------------------- helpers
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function showError(msg) {
    el.errorBox.textContent = "發生錯誤：" + msg;
    el.errorBox.classList.remove("hidden");
    el.errorBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function clearError() {
    el.errorBox.classList.add("hidden");
    el.errorBox.textContent = "";
  }
  function fetchJson(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok && !data.error) data.error = "伺服器回應錯誤 (" + r.status + ")";
        return data;
      });
    });
  }

  // ------------------------------------------------------- settings storage
  var SETTINGS_KEY = "piano_score_extractor_settings_v2";

  function loadSettings() {
    var s = {};
    try { s = JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || "{}") || {}; } catch (e) { s = {}; }
    if (s.title) el.titleInput.value = s.title;
    if (s.subtitle) el.subtitleInput.value = s.subtitle;
    if (s.pageSize) el.pageSizeInput.value = s.pageSize;
    if (typeof s.enhance === "boolean") el.enhanceInput.checked = s.enhance;
    if (s.interval) el.intervalInput.value = String(s.interval);
    if (typeof s.sensitivity === "number") el.sensitivityInput.value = String(s.sensitivity);
    el.sensitivityLabel.textContent = el.sensitivityInput.value;
  }

  function saveSettings() {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        title: el.titleInput.value,
        subtitle: el.subtitleInput.value,
        pageSize: el.pageSizeInput.value,
        enhance: !!el.enhanceInput.checked,
        interval: el.intervalInput.value,
        sensitivity: Number(el.sensitivityInput.value),
      }));
    } catch (e) {}
  }

  loadSettings();
  [el.titleInput, el.subtitleInput, el.pageSizeInput, el.enhanceInput,
   el.intervalInput, el.sensitivityInput].forEach(function (input) {
    input.addEventListener("change", saveSettings);
  });
  el.titleInput.addEventListener("input", saveSettings);
  el.subtitleInput.addEventListener("input", saveSettings);
  el.sensitivityInput.addEventListener("input", function () {
    el.sensitivityLabel.textContent = el.sensitivityInput.value;
  });
  el.sensitivityInput.addEventListener("change", saveSettings);

  // ---------------------------------------------------------------- modal
  function openModal(src) {
    el.modalImg.src = src;
    el.imageModal.classList.remove("hidden");
  }
  function closeModal() {
    el.imageModal.classList.add("hidden");
    el.modalImg.src = "";
  }
  el.imageModalClose.addEventListener("click", closeModal);
  el.imageModal.addEventListener("click", function (e) {
    if (e.target === el.imageModal) closeModal();
  });

  // ---------------------------------------------------------------- tabs
  function selectTab(which) {
    var file = which === "file";
    el.tabFile.classList.toggle("active", file);
    el.tabUrl.classList.toggle("active", !file);
    el.panelFile.classList.toggle("hidden", !file);
    el.panelUrl.classList.toggle("hidden", file);
  }
  el.tabFile.addEventListener("click", function () { selectTab("file"); });
  el.tabUrl.addEventListener("click", function () { selectTab("url"); });

  // ---------------------------------------------------------------- upload
  el.urlSubmitBtn.addEventListener("click", function () {
    var url = (el.urlInput.value || "").trim();
    if (!url) { showError("請輸入影片網址"); return; }
    submitUrl(url);
  });
  el.urlInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") el.urlSubmitBtn.click();
  });

  function submitUrl(url) {
    clearError();
    el.uploadStatus.textContent = "";
    showProgress("下載影片中...", "準備下載...", 0);

    fetchJson("/api/from_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    }).then(function (data) {
      if (data.error) {
        hideProgress();
        showError(data.error);
        return;
      }
      state.jobId = data.job_id;
      pollUntilReady();
    }).catch(function (e) {
      hideProgress();
      showError(String(e));
    });
  }

  function showProgress(title, msg, pct) {
    el.stepProgress.classList.remove("hidden");
    el.stepProgressTitle.textContent = title;
    el.progressBar.style.width = clamp(pct, 0, 100) + "%";
    el.progressMsg.textContent = msg || "";
  }
  function hideProgress() {
    el.stepProgress.classList.add("hidden");
  }

  function pollUntilReady() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(checkReady, 1000);
    checkReady();
  }

  function checkReady() {
    if (!state.jobId) return;
    fetchJson("/api/status/" + state.jobId)
      .then(function (data) {
        if (data.error && !data.status) {
          clearInterval(state.pollTimer);
          hideProgress();
          showError(data.error);
          return;
        }
        el.progressBar.style.width = (data.progress || 0) + "%";
        el.progressMsg.textContent = data.message || "";
        if (data.status === "preparing") el.stepProgressTitle.textContent = "分析影片中...";
        if (data.status === "downloading") el.stepProgressTitle.textContent = "下載影片中...";

        if (data.status === "ready") {
          clearInterval(state.pollTimer);
          hideProgress();
          applyPreparedData(data);
        } else if (data.status === "error") {
          clearInterval(state.pollTimer);
          hideProgress();
          showError(data.error || "處理失敗");
        }
      })
      .catch(function (e) {
        clearInterval(state.pollTimer);
        hideProgress();
        showError(String(e));
      });
  }

  ["dragenter", "dragover"].forEach(function (evt) {
    el.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      el.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    el.dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      el.dropzone.classList.remove("dragover");
    });
  });
  el.dropzone.addEventListener("drop", function (e) {
    var files = e.dataTransfer.files;
    if (files && files.length) pickFile(files[0]);
  });
  el.fileInput.addEventListener("change", function () {
    if (el.fileInput.files.length) pickFile(el.fileInput.files[0]);
  });

  function pickFile(file) {
    var looksLikeVideo = !file.type || file.type.indexOf("video/") === 0 ||
      /\.(mp4|m4v|mov|avi|mkv|webm|flv|wmv|ts)$/i.test(file.name || "");
    if (!looksLikeVideo) {
      showError("請上傳影片檔案 (例如 .mp4, .mov)，不支援此格式：" + file.name);
      return;
    }
    uploadFile(file);
  }

  function uploadFile(file) {
    if (state.uploading) return;
    clearError();
    state.uploading = true;
    el.uploadStatus.textContent = "上傳並分析影片中，請稍候...(大檔案可能需要一點時間)";
    el.uploadProgressWrap.classList.remove("hidden");
    el.uploadProgress.style.width = "0%";

    var fd = new FormData();
    fd.append("video", file);

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) {
        var pct = Math.round(e.loaded / e.total * 100);
        el.uploadProgress.style.width = pct + "%";
        el.uploadStatus.textContent = "上傳中... " + pct + "%";
      }
    };
    xhr.onload = function () {
      state.uploading = false;
      el.uploadProgressWrap.classList.add("hidden");
      var data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status >= 400 || data.error) {
        el.uploadStatus.textContent = "";
        showError(data.error || "上傳失敗 (HTTP " + xhr.status + ")");
        return;
      }
      state.jobId = data.job_id;
      applyPreparedData(data);
    };
    xhr.onerror = function () {
      state.uploading = false;
      el.uploadProgressWrap.classList.add("hidden");
      el.uploadStatus.textContent = "";
      showError("上傳中斷，請確認網路連線後再試一次");
    };
    xhr.send(fd);
  }

  // ------------------------------------------------------ prepared / preview
  function applyPreparedData(data) {
    if (state.scanTimer) clearInterval(state.scanTimer);
    setScanRunning(false);
    el.scanStatus.textContent = "";
    el.captureStatus.textContent = "";
    state.duration = Number(data.duration) || 0;
    state.videoW = Number(data.width) || 1;
    state.videoH = Number(data.height) || 1;
    if (data.suggested_crop) state.cropBox = normalizeBox(data.suggested_crop);

    el.uploadStatus.textContent = "已就緒！影片長度約 " + Math.round(state.duration) + " 秒。";
    el.previewSeek.max = state.duration;
    el.previewSeek.step = state.duration > 600 ? 0.5 : 0.1;

    el.stepCrop.classList.remove("hidden");
    el.stepParams.classList.remove("hidden");

    if (typeof data.suggested_t === "number") {
      var t = clamp(data.suggested_t, 0, state.duration || 0);
      el.previewSeek.value = t;
      el.previewSeekLabel.textContent = t.toFixed(1) + " 秒";
    } else {
      el.previewSeek.value = 0;
      el.previewSeekLabel.textContent = "0.0 秒";
    }

    setSections(data.sections || []);
    updatePreviewBox();
    fetchPreviewAt(Number(el.previewSeek.value));
  }

  function normalizeBox(box) {
    return {
      x: clamp(Number(box.x) || 0, 0, Math.max(0, state.videoW - 16)),
      y: clamp(Number(box.y) || 0, 0, Math.max(0, state.videoH - 16)),
      w: clamp(Number(box.w) || 16, 16, state.videoW),
      h: clamp(Number(box.h) || 16, 16, state.videoH),
    };
  }

  function updatePreviewBox() {
    if (!state.cropBox) return;
    el.cropBox.classList.remove("hidden");
    drawCropBox();
  }

  function scaleFactor() {
    var displayedW = el.previewImg.clientWidth || 0;
    if (!displayedW || !state.videoW) return 1;
    return displayedW / state.videoW;
  }

  function drawCropBox() {
    if (!state.cropBox) return;
    var s = scaleFactor();
    el.cropBox.style.left = (state.cropBox.x * s) + "px";
    el.cropBox.style.top = (state.cropBox.y * s) + "px";
    el.cropBox.style.width = (state.cropBox.w * s) + "px";
    el.cropBox.style.height = (state.cropBox.h * s) + "px";
  }
  window.addEventListener("resize", drawCropBox);

  // ------------------------------------------------------ pointer crop drag
  (function setupCropDrag() {
    var dragging = null;
    var startMouse = { x: 0, y: 0 };
    var startBox = null;

    function onPointerDown(e) {
      if (!state.cropBox) return;
      dragging = e.currentTarget.getAttribute("data-handle") || "move";
      if (e.currentTarget === el.cropBox) dragging = "move";
      startMouse = { x: e.clientX, y: e.clientY };
      startBox = { x: state.cropBox.x, y: state.cropBox.y, w: state.cropBox.w, h: state.cropBox.h };
      el.cropStage.classList.add("dragging");
      if (e.currentTarget.setPointerCapture) {
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) {}
      }
      e.preventDefault();
    }

    el.cropBox.addEventListener("pointerdown", onPointerDown);
    Array.prototype.forEach.call(el.cropBox.querySelectorAll(".handle"), function (h) {
      h.addEventListener("pointerdown", onPointerDown);
    });

    document.addEventListener("pointermove", function (e) {
      if (!dragging || !startBox) return;
      var s = scaleFactor() || 1;
      var dx = (e.clientX - startMouse.x) / s;
      var dy = (e.clientY - startMouse.y) / s;
      var box = { x: startBox.x, y: startBox.y, w: startBox.w, h: startBox.h };
      var minSize = 24;

      if (dragging === "move") {
        box.x = clamp(startBox.x + dx, 0, Math.max(0, state.videoW - startBox.w));
        box.y = clamp(startBox.y + dy, 0, Math.max(0, state.videoH - startBox.h));
      } else {
        if (dragging.indexOf("n") !== -1) {
          var bottom = startBox.y + startBox.h;
          box.y = clamp(startBox.y + dy, 0, bottom - minSize);
          box.h = bottom - box.y;
        }
        if (dragging.indexOf("s") !== -1) {
          box.h = clamp(startBox.h + dy, minSize, state.videoH - startBox.y);
        }
        if (dragging.indexOf("w") !== -1) {
          var right = startBox.x + startBox.w;
          box.x = clamp(startBox.x + dx, 0, right - minSize);
          box.w = right - box.x;
        }
        if (dragging.indexOf("e") !== -1) {
          box.w = clamp(startBox.w + dx, minSize, state.videoW - startBox.x);
        }
      }
      state.cropBox = box;
      drawCropBox();
    });

    function endDrag() {
      if (!dragging) return;
      dragging = null;
      startBox = null;
      el.cropStage.classList.remove("dragging");
      markDetectionStale();
      scheduleEnhanceHide();
    }
    document.addEventListener("pointerup", endDrag);
    document.addEventListener("pointercancel", endDrag);
  })();

  function markDetectionStale() {
    el.detectBadge.classList.remove("hidden", "ok", "bad");
    el.detectBadge.classList.add("stale");
    el.detectBadge.textContent = "框選範圍已調整，重新載入畫面或擷取後會重新判斷";
  }

  // ------------------------------------------------------------- seek / preview
  el.previewSeek.addEventListener("input", function () {
    el.previewSeekLabel.textContent = Number(el.previewSeek.value).toFixed(1) + " 秒";
    if (state.seekTimer) clearTimeout(state.seekTimer);
    state.seekTimer = setTimeout(function () {
      fetchPreviewAt(Number(el.previewSeek.value));
    }, 250);
  });

  function adjustSeek(delta) {
    if (!state.jobId) return;
    var current = Number(el.previewSeek.value);
    var next = clamp(current + delta, 0, state.duration || 0);
    el.previewSeek.value = next;
    el.previewSeekLabel.textContent = next.toFixed(1) + " 秒";
    if (state.seekTimer) clearTimeout(state.seekTimer);
    state.seekTimer = setTimeout(function () {
      fetchPreviewAt(next);
    }, 180);
  }

  [["btnSeekM5", -5], ["btnSeekM1", -1], ["btnSeekM05", -0.5],
   ["btnSeekP05", 0.5], ["btnSeekP1", 1], ["btnSeekP5", 5]].forEach(function (pair) {
    document.getElementById(pair[0]).addEventListener("click", function () { adjustSeek(pair[1]); });
  });

  function fetchPreviewAt(t) {
    if (!state.jobId) return;
    var seq = ++state.previewSeq;
    el.previewLoading.classList.remove("hidden");

    fetchJson("/api/preview_at", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        t: t,
        crop_box: state.cropBox ? roundedBox() : null,
      }),
    }).then(function (data) {
      if (seq !== state.previewSeq) return; // 已經換到別的時間點，丟掉過期回應
      if (data.error) {
        el.previewLoading.classList.add("hidden");
        showError(data.error);
        return;
      }
      loadPreviewImage(data.preview_url);
      updateDetectionBadge(data);
    }).catch(function (e) {
      if (seq !== state.previewSeq) return;
      el.previewLoading.classList.add("hidden");
      showError(String(e));
    });
  }

  function loadPreviewImage(url) {
    var probe = new Image();
    probe.onload = function () {
      el.previewImg.src = url;
      el.previewImg.classList.remove("hidden");
      el.previewLoading.classList.add("hidden");
      drawCropBox();
    };
    probe.onerror = function () {
      el.previewLoading.classList.add("hidden");
      showError("讀取預覽畫面失敗，請再試一次");
    };
    probe.src = url;
  }

  function updateDetectionBadge(data) {
    el.detectBadge.classList.remove("hidden", "ok", "bad", "stale");
    if (data.is_score) {
      el.detectBadge.classList.add("ok");
      el.detectBadge.textContent = "✓ 這個畫面偵測到五線譜 (信心 " +
        Math.round((data.score || 0) * 100) + "%，偵測到 " + (data.lines || 0) + " 條譜線)";
    } else {
      el.detectBadge.classList.add("bad");
      el.detectBadge.textContent = "⚠ 這個畫面看起來不是譜面，建議拖曳時間軸換一張再擷取";
    }
  }

  function roundedBox() {
    return {
      x: Math.round(state.cropBox.x),
      y: Math.round(state.cropBox.y),
      w: Math.round(state.cropBox.w),
      h: Math.round(state.cropBox.h),
    };
  }

  // ------------------------------------------------------------- capture
  el.captureBtn.addEventListener("click", function () {
    if (!state.jobId || !state.cropBox) return;
    el.captureBtn.disabled = true;
    el.captureBtn.textContent = "擷取中...";
    el.captureStatus.textContent = "";

    fetchJson("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        t: Number(el.previewSeek.value),
        crop_box: roundedBox(),
      }),
    }).then(function (data) {
      if (data.error) { showError(data.error); return; }
      setSections(data.sections || []);
      el.captureStatus.textContent = data.is_score
        ? "已擷取第 " + state.sections.length + " 段（偵測到五線譜）"
        : "已擷取第 " + state.sections.length + " 段，但這個畫面看起來不是譜面，請確認是否需要保留";
    }).catch(function (e) {
      showError(String(e));
    }).then(function () {
      el.captureBtn.disabled = false;
      el.captureBtn.textContent = "📸 擷取當前畫面";
    });
  });

  // ------------------------------------------------------------- auto scan
  function setScanRunning(running) {
    state.scanning = running;
    el.autoScanBtn.disabled = running;
    el.autoScanBtn.textContent = running ? "掃描中..." : "開始自動掃描";
    el.autoScanStopBtn.classList.toggle("hidden", !running);
    el.scanProgressWrap.classList.toggle("hidden", !running);
    el.intervalInput.disabled = running;
    el.sensitivityInput.disabled = running;
  }

  function applyScanState(scan) {
    if (!scan) return;
    if (scan.progress !== undefined) el.scanProgress.style.width = clamp(scan.progress, 0, 100) + "%";
    if (scan.message) el.scanStatus.textContent = scan.message;
  }

  el.autoScanBtn.addEventListener("click", function () {
    if (!state.jobId) return;
    if (!state.cropBox) { showError("請先確認框選區域"); return; }
    clearError();
    el.scanStatus.textContent = "準備掃描...";
    el.scanProgress.style.width = "0%";

    fetchJson("/api/auto_scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        crop_box: roundedBox(),
        interval: Number(el.intervalInput.value) || 2,
        sensitivity: Number(el.sensitivityInput.value) || 50,
      }),
    }).then(function (data) {
      if (data.error) { showError(data.error); return; }
      setSections(data.sections || []);
      setScanRunning(true);
      applyScanState(data.scan);
      pollScan();
    }).catch(function (e) {
      showError(String(e));
    });
  });

  el.autoScanStopBtn.addEventListener("click", function () {
    if (!state.jobId) return;
    el.autoScanStopBtn.disabled = true;
    fetchJson("/api/auto_scan/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId }),
    }).then(function () {
      el.autoScanStopBtn.disabled = false;
    }).catch(function (e) {
      el.autoScanStopBtn.disabled = false;
      showError(String(e));
    });
  });

  function pollScan() {
    if (state.scanTimer) clearInterval(state.scanTimer);
    state.scanTimer = setInterval(function () {
      if (!state.jobId) { clearInterval(state.scanTimer); return; }
      fetchJson("/api/auto_scan/status/" + state.jobId).then(function (data) {
        if (data.error) {
          clearInterval(state.scanTimer);
          setScanRunning(false);
          showError(data.error);
          return;
        }
        setSections(data.sections || []);
        applyScanState(data.scan);

        var status = data.scan && data.scan.status;
        if (status !== "running") {
          clearInterval(state.scanTimer);
          setScanRunning(false);
          if (status === "error") {
            showError((data.scan && data.scan.error) || "自動掃描失敗");
          } else if (status === "done" || status === "stopped") {
            el.scanStatus.textContent = data.scan.message || "";
            if (state.sections.length === 0) {
              showError("沒有找到任何譜面畫面。請確認藍色框有框到譜面，或改用手動擷取。");
            }
          }
        }
      }).catch(function (e) {
        clearInterval(state.scanTimer);
        setScanRunning(false);
        showError(String(e));
      });
    }, 700);
  }

  // ------------------------------------------------------------- gallery
  function updateStartHint() {
    el.startHint.textContent = state.sections.length
      ? "目前已擷取 " + state.sections.length + " 張片段"
      : "還沒有任何片段";
  }

  function setSections(sections) {
    state.sections = sections || [];
    renderGallery();
    updateStartHint();
  }

  function formatTime(t) {
    var total = Math.max(0, Number(t) || 0);
    var m = Math.floor(total / 60);
    var s = total % 60;
    return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
  }

  function renderGallery() {
    el.gallery.innerHTML = "";
    el.galleryCount.textContent = state.sections.length;
    el.galleryWrap.classList.toggle("hidden", state.sections.length === 0);
    if (state.sections.length === 0) el.captureStatus.textContent = "";

    state.sections.forEach(function (item, idx) {
      var row = document.createElement("div");
      row.className = "gallery-item";
      row.setAttribute("data-filename", item.filename);

      var img = document.createElement("img");
      img.src = item.url;
      img.alt = "片段 " + (idx + 1);
      img.loading = "lazy";
      img.addEventListener("click", function () { openModal(item.url); });

      var info = document.createElement("div");
      info.className = "info";
      var title = document.createElement("div");
      title.className = "info-title";
      title.textContent = "第 " + (idx + 1) + " 段 · " + formatTime(item.t);
      var meta = document.createElement("div");
      meta.className = "info-meta";
      meta.textContent = item.auto ? "自動掃描" : "手動擷取";
      if (item.score) meta.textContent += " · 信心 " + Math.round(item.score * 100) + "%";
      info.appendChild(title);
      info.appendChild(meta);

      var actions = document.createElement("div");
      actions.className = "item-actions";

      function actionBtn(label, action, disabled, titleText) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "icon-btn";
        b.textContent = label;
        b.title = titleText || "";
        b.disabled = !!disabled;
        b.setAttribute("data-action", action);
        return b;
      }

      actions.appendChild(actionBtn("↑", "up", idx === 0, "往上移"));
      actions.appendChild(actionBtn("↓", "down", idx === state.sections.length - 1, "往下移"));
      actions.appendChild(actionBtn("跳到", "jump", false, "跳到這個時間點"));
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger small";
      remove.textContent = "移除";
      remove.setAttribute("data-action", "remove");
      actions.appendChild(remove);

      row.appendChild(img);
      row.appendChild(info);
      row.appendChild(actions);
      el.gallery.appendChild(row);
    });
  }

  el.gallery.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-action]");
    if (!btn) return;
    var row = btn.closest(".gallery-item");
    if (!row) return;
    var filename = row.getAttribute("data-filename");
    var action = btn.getAttribute("data-action");
    var index = state.sections.map(function (s) { return s.filename; }).indexOf(filename);
    if (index === -1) return;

    if (action === "up" || action === "down") {
      var target = action === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= state.sections.length) return;
      var order = state.sections.map(function (s) { return s.filename; });
      var tmp = order[index]; order[index] = order[target]; order[target] = tmp;
      reorderSections(order);
    } else if (action === "jump") {
      var t = clamp(state.sections[index].t, 0, state.duration || 0);
      el.previewSeek.value = t;
      el.previewSeekLabel.textContent = Number(t).toFixed(1) + " 秒";
      fetchPreviewAt(t);
      el.cropStage.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (action === "remove") {
      fetchJson("/api/sections/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: state.jobId, filename: filename }),
      }).then(function (data) {
        if (data.error) { showError(data.error); return; }
        setSections(data.sections || []);
      }).catch(function (e) { showError(String(e)); });
    }
  });

  function reorderSections(order) {
    fetchJson("/api/sections/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, filenames: order }),
    }).then(function (data) {
      if (data.error) { showError(data.error); return; }
      setSections(data.sections || []);
    }).catch(function (e) { showError(String(e)); });
  }

  el.clearAllBtn.addEventListener("click", function () {
    if (!state.sections.length) return;
    if (!window.confirm("確定要清空全部 " + state.sections.length + " 張片段嗎？")) return;
    fetchJson("/api/sections/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId }),
    }).then(function (data) {
      if (data.error) { showError(data.error); return; }
      setSections(data.sections || []);
    }).catch(function (e) { showError(String(e)); });
  });

  // ------------------------------------------------------------- enhance preview
  function scheduleEnhanceHide() {
    el.enhancePreviewWrap.classList.add("hidden");
    el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
  }

  el.enhancePreviewBtn.addEventListener("click", function () {
    if (!el.enhancePreviewWrap.classList.contains("hidden")) {
      scheduleEnhanceHide();
      return;
    }
    if (!state.jobId || !state.cropBox) { showError("請先上傳影片並確認框選區域"); return; }
    el.enhancePreviewBtn.disabled = true;
    el.enhancePreviewBtn.textContent = "產生中...";
    fetchJson("/api/preview_enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        t: Number(el.previewSeek.value),
        crop_box: roundedBox(),
      }),
    }).then(function (data) {
      el.enhancePreviewBtn.disabled = false;
      if (data.error) {
        el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
        showError(data.error);
        return;
      }
      el.enhancePreviewBtn.textContent = "🙈 隱藏預覽圖片";
      el.enhancePreviewImg.src = data.preview_url;
      el.enhancePreviewWrap.classList.remove("hidden");
    }).catch(function (e) {
      el.enhancePreviewBtn.disabled = false;
      el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
      showError(String(e));
    });
  });

  // ------------------------------------------------------------- build PDF
  el.startBtn.addEventListener("click", function () {
    clearError();
    if (!state.jobId) return;
    if (state.sections.length === 0) {
      showError("請先自動掃描或手動擷取至少一張樂譜片段！");
      el.galleryWrap.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    el.startBtn.disabled = true;
    el.startBtn.textContent = "產生中...";
    showProgress("正在產生 PDF...", "這可能需要幾秒鐘...", 100);
    el.stepResult.classList.add("hidden");

    fetchJson("/api/build_pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        title: el.titleInput.value || "鋼琴五線譜",
        subtitle: el.subtitleInput.value || "",
        page_size: el.pageSizeInput.value,
        enhance: !!el.enhanceInput.checked,
        filenames: state.sections.map(function (s) { return s.filename; }),
      }),
    }).then(function (data) {
      el.startBtn.disabled = false;
      el.startBtn.textContent = "🚀 產生樂譜 PDF";
      if (data.error) {
        hideProgress();
        showError(data.error);
        return;
      }
      hideProgress();
      el.stepResult.classList.remove("hidden");
      el.resultSummary.textContent = "共拼接了 " + data.kept_count + " 張樂譜片段。";
      el.downloadBtn.href = data.pdf_url;
      el.stepResult.scrollIntoView({ behavior: "smooth", block: "center" });
    }).catch(function (e) {
      el.startBtn.disabled = false;
      el.startBtn.textContent = "🚀 產生樂譜 PDF";
      hideProgress();
      showError(String(e));
    });
  });

  el.restartBtn.addEventListener("click", function () { location.reload(); });

  // ------------------------------------------------------------- keyboard
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !el.imageModal.classList.contains("hidden")) {
      closeModal();
      return;
    }
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!state.jobId || el.imageModal.classList.contains("hidden") === false) return;

    var step = e.shiftKey ? 5 : 1;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      adjustSeek(-step);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      adjustSeek(step);
    } else if (e.key === "c" || e.key === "C") {
      e.preventDefault();
      if (!el.captureBtn.disabled) el.captureBtn.click();
    }
  });

  updateStartHint();
})();
