(function () {
  "use strict";

  var state = {
    jobId: null,
    duration: 0,
    videoW: 0,
    videoH: 0,
    cropBox: null,
    pollTimer: null,
    capturedFrames: [], // array of {t, filename, url}
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

    stepCrop: document.getElementById("step-crop"),
    stepParams: document.getElementById("step-params"),
    stepProgress: document.getElementById("step-progress"),
    stepProgressTitle: document.getElementById("stepProgressTitle"),
    stepResult: document.getElementById("step-result"),

    cropStage: document.getElementById("cropStage"),
    previewImg: document.getElementById("previewImg"),
    cropBox: document.getElementById("cropBox"),
    previewSeek: document.getElementById("previewSeek"),
    previewSeekLabel: document.getElementById("previewSeekLabel"),
    captureBtn: document.getElementById("captureBtn"),

    galleryWrap: document.getElementById("galleryWrap"),
    galleryCount: document.getElementById("galleryCount"),
    gallery: document.getElementById("gallery"),

    titleInput: document.getElementById("titleInput"),
    subtitleInput: document.getElementById("subtitleInput"),
    pageSizeInput: document.getElementById("pageSizeInput"),
    enhanceInput: document.getElementById("enhanceInput"),
    enhancePreviewBtn: document.getElementById("enhancePreviewBtn"),
    enhancePreviewWrap: document.getElementById("enhancePreviewWrap"),
    enhancePreviewImg: document.getElementById("enhancePreviewImg"),
    startBtn: document.getElementById("startBtn"),

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

  // Setup modal close
  el.imageModalClose.addEventListener("click", function() {
    el.imageModal.classList.add("hidden");
  });
  el.imageModal.addEventListener("click", function(e) {
    if (e.target === el.imageModal) {
      el.imageModal.classList.add("hidden");
    }
  });

  // ---------------- Title fields persistence ----------------
  var TITLE_STORAGE_KEY = "piano_score_extractor_title";
  var SUBTITLE_STORAGE_KEY = "piano_score_extractor_subtitle";

  (function restoreTitleFields() {
    try {
      var savedTitle = window.localStorage.getItem(TITLE_STORAGE_KEY);
      if (savedTitle) el.titleInput.value = savedTitle;
      var savedSubtitle = window.localStorage.getItem(SUBTITLE_STORAGE_KEY);
      if (savedSubtitle) el.subtitleInput.value = savedSubtitle;
    } catch (e) {}
  })();

  function persistTitleFields() {
    try {
      window.localStorage.setItem(TITLE_STORAGE_KEY, el.titleInput.value || "");
      window.localStorage.setItem(SUBTITLE_STORAGE_KEY, el.subtitleInput.value || "");
    } catch (e) {}
  }
  el.titleInput.addEventListener("input", persistTitleFields);
  el.subtitleInput.addEventListener("input", persistTitleFields);

  function showError(msg) {
    el.errorBox.textContent = "發生錯誤：" + msg;
    el.errorBox.classList.remove("hidden");
  }
  function clearError() {
    el.errorBox.classList.add("hidden");
    el.errorBox.textContent = "";
  }

  // ---------------- Tabs ----------------
  el.tabFile.addEventListener("click", function () {
    el.tabFile.classList.add("active");
    el.tabUrl.classList.remove("active");
    el.panelFile.classList.remove("hidden");
    el.panelUrl.classList.add("hidden");
  });
  el.tabUrl.addEventListener("click", function () {
    el.tabUrl.classList.add("active");
    el.tabFile.classList.remove("active");
    el.panelUrl.classList.remove("hidden");
    el.panelFile.classList.add("hidden");
  });

  // ---------------- Upload/Download Video ----------------
  el.urlSubmitBtn.addEventListener("click", function () {
    var url = (el.urlInput.value || "").trim();
    if (!url) { showError("請輸入影片網址"); return; }
    submitUrl(url);
  });

  function submitUrl(url) {
    clearError();
    el.uploadStatus.textContent = "";
    el.stepProgress.classList.remove("hidden");
    el.stepProgressTitle.textContent = "下載影片中...";
    el.progressBar.style.width = "0%";
    el.progressMsg.textContent = "準備下載...";

    fetch("/api/from_url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          showError(data.error);
          el.stepProgress.classList.add("hidden");
          return;
        }
        state.jobId = data.job_id;
        pollUntilReady();
      })
      .catch(function (e) {
        showError(String(e));
        el.stepProgress.classList.add("hidden");
      });
  }

  function pollUntilReady() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(checkReady, 1000);
    checkReady();
  }

  function checkReady() {
    fetch("/api/status/" + state.jobId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error && !data.status) { showError(data.error); clearInterval(state.pollTimer); return; }

        el.progressBar.style.width = (data.progress || 0) + "%";
        el.progressMsg.textContent = data.message || "";
        if (data.status === "preparing") {
          el.stepProgressTitle.textContent = "分析影片中...";
        }

        if (data.status === "ready") {
          clearInterval(state.pollTimer);
          el.stepProgress.classList.add("hidden");
          applyPreparedData(data);
        } else if (data.status === "error") {
          clearInterval(state.pollTimer);
          showError(data.error);
          el.stepProgress.classList.add("hidden");
        }
      })
      .catch(function (e) { showError(String(e)); });
  }

  function applyPreparedData(data) {
    state.duration = data.duration;
    state.videoW = data.width;
    state.videoH = data.height;
    state.cropBox = data.suggested_crop;

    el.uploadStatus.textContent = "已就緒！影片長度約 " + Math.round(data.duration) + " 秒。";
    el.previewSeek.max = data.duration;
    el.previewSeek.value = 0;
    el.previewSeekLabel.textContent = "0.0 秒";

    // Grab first frame (t=0) initially instead of relying on suggested preview if they scrub from 0
    el.stepCrop.classList.remove("hidden");
    el.stepParams.classList.remove("hidden");
    
    // Trigger preview fetch at t=0
    fetchPreviewAt(0);
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
    if (files && files.length) {
      if (files[0].type && !files[0].type.startsWith("video/")) {
        showError("請上傳影片檔案 (例如 .mp4, .mov)，不支援此格式：" + files[0].name);
        return;
      }
      uploadFile(files[0]);
    }
  });
  el.fileInput.addEventListener("change", function () {
    if (el.fileInput.files.length) {
      if (el.fileInput.files[0].type && !el.fileInput.files[0].type.startsWith("video/")) {
        showError("請上傳影片檔案 (例如 .mp4, .mov)，不支援此格式：" + el.fileInput.files[0].name);
        return;
      }
      uploadFile(el.fileInput.files[0]);
    }
  });

  function uploadFile(file) {
    clearError();
    el.uploadStatus.textContent = "上傳並分析影片中，請稍候...(大檔案可能需要一點時間)";
    var fd = new FormData();
    fd.append("video", file);

    fetch("/api/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); el.uploadStatus.textContent = ""; return; }
        state.jobId = data.job_id;
        applyPreparedData(data);
      })
      .catch(function (e) {
        showError(String(e));
        el.uploadStatus.textContent = "";
      });
  }

  function loadPreviewImage(url) {
    el.previewImg.onload = function () { drawCropBox(); };
    el.previewImg.src = url;
  }

  // ---------------- Crop Drag ----------------
  function scaleFactor() {
    var displayedW = el.previewImg.clientWidth || 1;
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

  (function setupCropDrag() {
    var dragging = null;
    var startMouse = { x: 0, y: 0 };
    var startBox = null;

    function toOrig(dx, dy) {
      var s = scaleFactor();
      return { dx: dx / s, dy: dy / s };
    }

    el.cropBox.addEventListener("mousedown", function (e) {
      dragging = "move";
      startMouse = { x: e.clientX, y: e.clientY };
      startBox = Object.assign({}, state.cropBox);
      e.preventDefault();
    });
    el.cropBox.querySelectorAll(".handle").forEach(function (h) {
      h.addEventListener("mousedown", function (e) {
        dragging = h.classList[1];
        startMouse = { x: e.clientX, y: e.clientY };
        startBox = Object.assign({}, state.cropBox);
        e.stopPropagation();
        e.preventDefault();
      });
    });

    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var d = toOrig(e.clientX - startMouse.x, e.clientY - startMouse.y);
      var box = Object.assign({}, startBox);

      if (dragging === "move") {
        box.x = clamp(startBox.x + d.dx, 0, state.videoW - startBox.w);
        box.y = clamp(startBox.y + d.dy, 0, state.videoH - startBox.h);
      } else {
        if (dragging.indexOf("n") === 0) {
          box.y = clamp(startBox.y + d.dy, 0, startBox.y + startBox.h - 20);
          box.h = startBox.y + startBox.h - box.y;
        }
        if (dragging.indexOf("s") === 0) {
          box.h = clamp(startBox.h + d.dy, 20, state.videoH - startBox.y);
        }
        if (dragging.indexOf("w") !== -1 && dragging.length === 2) {
          box.x = clamp(startBox.x + d.dx, 0, startBox.x + startBox.w - 20);
          box.w = startBox.x + startBox.w - box.x;
        }
        if (dragging.indexOf("e") !== -1 && dragging.length === 2) {
          box.w = clamp(startBox.w + d.dx, 20, state.videoW - startBox.x);
        }
      }
      state.cropBox = box;
      drawCropBox();
    });
    document.addEventListener("mouseup", function () { dragging = null; });
  })();

  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  // ---------------- Seek / Preview ----------------
  var seekDebounce = null;
  el.previewSeek.addEventListener("input", function () {
    el.previewSeekLabel.textContent = Number(el.previewSeek.value).toFixed(1) + " 秒";
    if (seekDebounce) clearTimeout(seekDebounce);
    seekDebounce = setTimeout(function() {
      fetchPreviewAt(Number(el.previewSeek.value));
    }, 300);
  });

  function adjustSeek(delta) {
    if (!state.jobId) return;
    var current = Number(el.previewSeek.value);
    var next = clamp(current + delta, 0, Number(el.previewSeek.max));
    el.previewSeek.value = next;
    el.previewSeekLabel.textContent = next.toFixed(1) + " 秒";
    
    if (seekDebounce) clearTimeout(seekDebounce);
    seekDebounce = setTimeout(function() {
      fetchPreviewAt(next);
    }, 300);
  }

  document.getElementById("btnSeekM1").addEventListener("click", function() { adjustSeek(-1); });
  document.getElementById("btnSeekM05").addEventListener("click", function() { adjustSeek(-0.5); });
  document.getElementById("btnSeekP05").addEventListener("click", function() { adjustSeek(0.5); });
  document.getElementById("btnSeekP1").addEventListener("click", function() { adjustSeek(1); });

  function fetchPreviewAt(t) {
    if (!state.jobId) return;
    fetch("/api/preview_at", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, t: t }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showError(data.error); return; }
        loadPreviewImage(data.preview_url);
      });
  }

  // ---------------- Capture Frame ----------------
  el.captureBtn.addEventListener("click", function() {
    if (!state.jobId || !state.cropBox) return;
    el.captureBtn.disabled = true;
    el.captureBtn.textContent = "擷取中...";

    fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        t: Number(el.previewSeek.value),
        crop_box: {
          x: Math.round(state.cropBox.x),
          y: Math.round(state.cropBox.y),
          w: Math.round(state.cropBox.w),
          h: Math.round(state.cropBox.h),
        }
      }),
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      el.captureBtn.disabled = false;
      el.captureBtn.textContent = "📸 擷取當前畫面";
      if (data.error) { showError(data.error); return; }
      
      state.capturedFrames.push({
        t: Number(el.previewSeek.value),
        filename: data.filename,
        url: data.url
      });
      renderGallery();
    })
    .catch(function(e) {
      el.captureBtn.disabled = false;
      el.captureBtn.textContent = "📸 擷取當前畫面";
      showError(String(e));
    });
  });

  function renderGallery() {
    el.gallery.innerHTML = "";
    el.galleryCount.textContent = state.capturedFrames.length;
    
    if (state.capturedFrames.length > 0) {
      el.galleryWrap.classList.remove("hidden");
    } else {
      el.galleryWrap.classList.add("hidden");
    }

    state.capturedFrames.forEach(function(item, idx) {
      var div = document.createElement("div");
      div.className = "gallery-item";
      
      var img = document.createElement("img");
      img.src = item.url;
      img.onclick = function() {
        el.modalImg.src = item.url;
        el.imageModal.classList.remove("hidden");
      };
      
      var info = document.createElement("div");
      info.className = "info";
      info.textContent = "片段 " + (idx + 1) + " (時間點: " + item.t + " 秒)";
      
      var btn = document.createElement("button");
      btn.className = "remove-btn";
      btn.textContent = "移除";
      btn.onclick = function() {
        state.capturedFrames.splice(idx, 1);
        renderGallery();
      };

      div.appendChild(img);
      div.appendChild(info);
      div.appendChild(btn);
      el.gallery.appendChild(div);
    });
  }

  // ---------------- Enhance Preview ----------------
  el.enhancePreviewBtn.addEventListener("click", function () {
    if (!el.enhancePreviewWrap.classList.contains("hidden")) {
      el.enhancePreviewWrap.classList.add("hidden");
      el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
      return;
    }

    if (!state.jobId || !state.cropBox) { showError("請先上傳影片並確認框選區域"); return; }
    el.enhancePreviewBtn.disabled = true;
    el.enhancePreviewBtn.textContent = "產生中...";
    fetch("/api/preview_enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: state.jobId,
        t: Number(el.previewSeek.value),
        crop_box: {
          x: Math.round(state.cropBox.x),
          y: Math.round(state.cropBox.y),
          w: Math.round(state.cropBox.w),
          h: Math.round(state.cropBox.h),
        },
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        el.enhancePreviewBtn.disabled = false;
        if (data.error) {
          el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
          showError(data.error); 
          return;
        }
        el.enhancePreviewBtn.textContent = "🙈 隱藏預覽圖片";
        el.enhancePreviewImg.src = data.preview_url;
        el.enhancePreviewWrap.classList.remove("hidden");
      })
      .catch(function (e) {
        el.enhancePreviewBtn.disabled = false;
        el.enhancePreviewBtn.textContent = "👀 預覽增強效果";
        showError(String(e));
      });
  });

  // ---------------- Generate PDF ----------------
  el.startBtn.addEventListener("click", function () {
    clearError();
    if (!state.jobId) return;
    if (state.capturedFrames.length === 0) {
      showError("請先擷取至少一張樂譜片段！");
      return;
    }

    el.stepProgressTitle.textContent = "正在產生 PDF...";
    el.stepProgress.classList.remove("hidden");
    el.stepResult.classList.add("hidden");
    el.progressBar.style.width = "100%";
    el.progressMsg.textContent = "這可能需要幾秒鐘...";

    var filenames = state.capturedFrames.map(function(f) { return f.filename; });

    var payload = {
      job_id: state.jobId,
      title: el.titleInput.value || "鋼琴五線譜",
      subtitle: el.subtitleInput.value || "",
      page_size: el.pageSizeInput.value,
      enhance: !!el.enhanceInput.checked,
      filenames: filenames,
    };

    fetch("/api/build_pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          showError(data.error);
          el.stepProgress.classList.add("hidden");
          return;
        }
        
        el.stepProgress.classList.add("hidden");
        el.stepResult.classList.remove("hidden");
        el.resultSummary.textContent = "共拼接了 " + data.kept_count + " 張樂譜片段。";
        el.downloadBtn.href = data.pdf_url;
      })
      .catch(function (e) {
        showError(String(e));
        el.stepProgress.classList.add("hidden");
      });
  });

  el.restartBtn.addEventListener("click", function () {
    location.reload();
  });
})();
