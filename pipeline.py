# -*- coding: utf-8 -*-
"""
鋼琴五線譜擷取 - 核心處理邏輯

流程：
1. 使用者提供影片，由這裡取得影片資訊與預覽圖。
2. 自動偵測畫面上「五線譜」所在的位置，建議框選區域。
3. 可自動掃描整部影片，找出每一頁譜面並去重複（見 auto_scan_video）。
4. 最終將擷取到的圖片拼接成 A4/Letter 頁面，輸出成 PDF。
"""

import collections
import os
import threading

import cv2
import numpy as np
from PIL import Image

Image.init()


# ---------------------------------------------------------------------------
# 基礎工具
# ---------------------------------------------------------------------------

def get_video_info(path):
    """回傳 (duration_sec, width, height, fps)"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("無法開啟影片，請確認檔案格式是否支援 (建議 mp4/mov/mkv)")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps > 0 else 0
    finally:
        cap.release()
    return duration, width, height, fps


# 同一個影片檔反覆抓畫面 (拖動時間軸) 時，重複開啟檔案非常慢，
# 因此每個影片保留一個解碼器。數量刻意壓在很小的值，避免一次開太多檔案。
_CAPTURE_CACHE = collections.OrderedDict()
_CAPTURE_LOCK = threading.Lock()
_CAPTURE_CACHE_MAX = 2


def _get_capture(path):
    with _CAPTURE_LOCK:
        cap = _CAPTURE_CACHE.get(path)
        if cap is not None and cap.isOpened():
            _CAPTURE_CACHE.move_to_end(path)
            return cap
        _CAPTURE_CACHE.pop(path, None)
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        _CAPTURE_CACHE[path] = cap
        while len(_CAPTURE_CACHE) > _CAPTURE_CACHE_MAX:
            _, old = _CAPTURE_CACHE.popitem(last=False)
            try:
                old.release()
            except Exception:
                pass
        return cap


def release_video(path):
    """釋放某個影片的解碼器 (工作結束或清理時呼叫)"""
    with _CAPTURE_LOCK:
        cap = _CAPTURE_CACHE.pop(path, None)
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass


def _read_frame_at(cap, t_sec):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t_sec)) * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def grab_frame_at(path, t_sec):
    """抓取影片在 t_sec 秒時的畫面，回傳 PIL.Image (RGB) 或 None"""
    cap = _get_capture(path)
    if cap is None:
        return None
    with _CAPTURE_LOCK:
        if not cap.isOpened():
            return None
        frame = _read_frame_at(cap, t_sec)
    if frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


# ---------------------------------------------------------------------------
# 五線譜偵測
# ---------------------------------------------------------------------------

def _to_gray_small(pil_img, max_w=720):
    """轉灰階並縮到 max_w 寬 (偵測用，越寬越慢)"""
    gray = np.array(pil_img.convert("L"))
    h, w = gray.shape[:2]
    if w > max_w:
        scale = max_w / float(w)
        gray = cv2.resize(gray, (max_w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return gray


def _dark_ink(gray):
    """
    取出「深色細線/文字」的遮罩。
    同時併用自適應門檻 (光線不均、深色背景的影片) 與固定門檻 (白底譜面)，
    並用水平閉運算把影片壓縮造成的斷線接起來。注意不能用 medianBlur，
    那會把只有 1~2 px 寬的譜線整條抹掉。
    """
    block = max(15, (min(gray.shape[:2]) // 16) | 1)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 12
    )
    # 白底黑線的譜面在影片壓縮後線條會變淡，自適應門檻有時抓不到；
    # 但固定門檻只能用在「附近是亮底」的像素，否則深色背景會被當成一整片墨。
    blur = cv2.GaussianBlur(gray, (0, 0), 12)
    fixed = (((gray < 175) & (blur > 150)).astype(np.uint8)) * 255
    dark = cv2.bitwise_or(adaptive, fixed)
    return cv2.morphologyEx(
        dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
    )


def _horizontal_lines(dark, min_len):
    """只留下水平長線 (五線譜的譜線)，回傳遮罩"""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, int(min_len)), 1))
    return cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)


def _group_rows(mask, gap=2):
    """把連續的 True 列合併成 (start, end)；gap 允許中間斷幾個像素"""
    rows = np.flatnonzero(mask)
    if rows.size == 0:
        return []
    groups = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev <= gap + 1:
            prev = r
            continue
        groups.append((start, prev))
        start = prev = r
    groups.append((start, prev))
    return groups


def _max_run_lengths(mask):
    """每一個橫列最長的連續 True 長度 (一次算完，比逐列迴圈快很多)"""
    m = mask > 0
    if m.size == 0:
        return np.zeros(m.shape[0], dtype=np.int32)
    padded = np.pad(m, ((0, 0), (1, 1))).astype(np.int8)
    diff = np.diff(padded, axis=1)
    starts = np.argwhere(diff == 1)
    ends = np.argwhere(diff == -1)
    best = np.zeros(m.shape[0], dtype=np.int32)
    if starts.size == 0 or ends.size == 0:
        return best
    lengths = ends[:, 1] - starts[:, 1]
    np.maximum.at(best, starts[:, 0], lengths)
    return best


def _staff_line_rows(horiz, height, width):
    """
    從水平線遮罩挑出「譜線」的列。
    用「最長連續線段」而不是「佔畫面幾成寬」：譜面只佔畫面中間一條時
    (左右有大片黑邊/留白) 也抓得到。太厚的線 (畫面邊界、表格、粗黑邊)
    用線寬中位數自動排除。
    """
    runs = _max_run_lengths(horiz)
    min_run = max(24, int(width * 0.16))
    groups = _group_rows(runs >= min_run, gap=1)
    if not groups:
        return []
    thickness = np.array([b - a + 1 for a, b in groups], dtype=np.float32)
    max_thickness = max(3.0, float(np.median(thickness)) * 3.0)
    return [
        int((a + b) / 2)
        for a, b in groups
        if (b - a + 1) <= max_thickness and 2 <= (a + b) / 2 <= height - 3
    ]


def _count_staff_systems(line_rows, height):
    """
    把譜線分組成「譜表」(通常 5 條線一組)，回傳 (完整譜表數, 譜線總數)。
    線距用中位數自動推算，因此不同解析度、不同排版都適用。
    """
    if len(line_rows) < 2:
        return 0, len(line_rows)

    gaps = np.diff(line_rows)
    gaps = gaps[gaps > 0]
    if gaps.size == 0:
        return 0, len(line_rows)
    tol = max(2.0, float(np.median(gaps)) * 1.8)

    systems = 0
    run = 1
    for g in gaps:
        if g <= tol:
            run += 1
        else:
            if 4 <= run <= 6:
                systems += 1
            run = 1
    if 4 <= run <= 6:
        systems += 1
    return systems, len(line_rows)


def _notes_signature(dark, horiz):
    """
    去掉譜線後剩下的「音符墨跡」，縮成固定大小當作這一頁的指紋。
    兩張同頁面的截圖距離會接近 0，不同頁面則明顯不同。
    """
    no_lines = cv2.bitwise_and(dark, cv2.bitwise_not(cv2.dilate(horiz, np.ones((3, 3), np.uint8))))
    small = cv2.resize(no_lines.astype(np.float32) / 255.0, (48, 32), interpolation=cv2.INTER_AREA)
    return small


def analyze_score_frame(pil_img):
    """
    判斷一張 (已裁切到譜面區域的) 圖片「像不像五線譜」，回傳 dict：

    score      0~1 的信心分數
    is_score   是否判定為譜面
    lines      偵測到的水平譜線數
    systems    完整譜表（5 線一組）數
    paper      白色紙面比例
    sharpness  清晰度（Laplacian 變異數，越高越清楚）
    signature  去背後的音符指紋 (numpy 陣列，供去重複用)
    """
    gray = _to_gray_small(pil_img)
    h, w = gray.shape[:2]
    dark = _dark_ink(gray)
    horiz = _horizontal_lines(dark, w * 0.06)

    line_rows = _staff_line_rows(horiz, h, w)
    systems, lines = _count_staff_systems(line_rows, h)

    paper = float(np.mean(gray > 185))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    signature = _notes_signature(dark, horiz)
    # 譜線上下的「音符墨跡」比例：沒有音符的空白五線譜、表格、文字行分數會很低
    note_ink = float(signature.mean())

    # 又長又直的垂直線通常是表格框線，譜面只會有短短的小節線
    vertical = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, int(h * 0.18)))),
    )
    vertical_rules = len(_group_rows(vertical.mean(axis=0) / 255.0 >= 0.5, gap=1))

    line_score = min(1.0, lines / 12.0)
    paper_score = float(np.clip((paper - 0.20) / 0.50, 0.0, 1.0))
    system_score = min(1.0, systems / 2.0)
    note_score = min(1.0, note_ink / 0.02)
    score = (0.34 * line_score + 0.18 * paper_score + 0.26 * system_score
             + 0.10 * min(1.0, lines / 25.0) + 0.12 * note_score)
    score -= min(0.20, 0.05 * vertical_rules)
    score = float(np.clip(score, 0.0, 1.0))

    # 插圖/封面常常有大片深色墨跡，但只會「碰巧」有一兩個譜表；
    # 真正的密集譜面會有許多譜表，所以用「墨跡多但譜表很少」來排除。
    illustrated = note_ink > 0.12 and systems < 4
    if illustrated:
        score *= 0.6

    is_score = (
        lines >= 5 and paper >= 0.25
        and note_ink >= 0.004 and not illustrated and score >= 0.45
        # 譜表數是很好的過濾條件，但小畫面偶爾會讓譜線黏在一起數不出來，
        # 這時只要譜線夠多 (>= 10 條) 仍然視為譜面。
        and (systems >= 1 or lines >= 10)
    )
    return {
        "score": score,
        "is_score": bool(is_score),
        "lines": int(lines),
        "systems": int(systems),
        "paper": paper,
        "note_ink": note_ink,
        "vertical_rules": int(vertical_rules),
        "sharpness": sharpness,
        "signature": signature,
    }


def signature_distance(a, b):
    """兩張譜面指紋的差異 (0 = 完全一樣)"""
    if a is None or b is None:
        return 1.0
    return float(np.mean(np.abs(a - b)))


def _dedupe_threshold(sensitivity):
    """
    sensitivity: 0~100。數字越大越容易把「很像」的畫面當成同一頁。
    """
    s = max(0.0, min(100.0, float(sensitivity)))
    return 0.010 + (s / 100.0) * 0.090


# ---------------------------------------------------------------------------
# 自動建議譜面框選區域
# ---------------------------------------------------------------------------

def _fallback_crop_box(path, duration, width, height):
    """真的偵測不到譜線時，用「明亮區域」猜一個大略位置"""
    ts = [duration * (i + 1) / 11.0 for i in range(10)] if duration > 0 else [0]
    profiles = []
    for t in ts:
        img = grab_frame_at(path, t)
        if img is None:
            continue
        arr = np.array(img).astype(np.float32)
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        light = (mx > 190) & ((mx - mn) < 40)
        profiles.append(light.mean(axis=1))

    if not profiles:
        return {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.4)}

    med = np.median(np.array(profiles), axis=0)
    y0 = int(height * 0.55)
    for i in range(len(med) - 5):
        if med[i] < 0.3 and np.mean(med[i + 1:i + 6]) > 0.7:
            y0 = i + 1
            break
    return {"x": 0, "y": y0, "w": int(width), "h": int(min(height - y0, height * 0.4))}


def _paper_region(gray, band=None):
    """
    找出畫面中「整片明亮紙張」的範圍 (回傳 (y0,y1), (x0,x1) 或 None)。
    教學影片常常把譜面放在畫面中間、四周是深色背景或講者畫面，
    用這個範圍可以把四周黑邊修掉，同時保留頁首標題。
    """
    h, w = gray.shape[:2]
    bright = gray > 165
    paper_ratio = float(bright.mean())
    if paper_ratio < 0.08:
        return None

    row_frac = bright.mean(axis=1)
    row_thr = max(0.10, paper_ratio * 0.30)
    row_runs = [r for r in _group_rows(row_frac >= row_thr, gap=max(1, int(h * 0.02)))
                if r[1] - r[0] >= h * 0.12]
    if not row_runs:
        return None

    def overlap(run):
        if band is None:
            return run[1] - run[0]
        return max(0, min(run[1], band[1]) - max(run[0], band[0]))

    row_run = max(row_runs, key=lambda r: (overlap(r), r[1] - r[0]))
    y0, y1 = row_run
    if band is not None and overlap(row_run) == 0:
        return None

    sub = bright[y0:y1 + 1, :]
    col_frac = sub.mean(axis=0)
    col_thr = max(0.20, float(col_frac.max()) * 0.45)
    col_runs = [c for c in _group_rows(col_frac >= col_thr, gap=max(1, int(w * 0.02)))
                if c[1] - c[0] >= w * 0.12]
    if col_runs:
        x0, x1 = max(col_runs, key=lambda c: c[1] - c[0])
    else:
        x0, x1 = 0, w - 1

    if (y1 - y0) < h * 0.10 or (x1 - x0) < w * 0.12:
        return None
    return (y0, y1), (x0, x1)


def suggest_layout(path, duration, samples=12):
    """
    抽樣數張畫面，找出譜面實際佔用的水平帶狀區域，並回傳：
        crop    建議框選區域 {x, y, w, h} (原始影片像素座標)
        best_t  最適合當預覽、偵測最清楚的時間點
        score   該時間點的信心分數
    """
    duration, width, height, fps = get_video_info(path)
    if duration <= 0:
        duration = 0.0

    if duration <= 0:
        times = [0.0]
    else:
        times = [duration * (i + 1) / (samples + 1) for i in range(samples)]

    line_rows_per_frame = []
    frame_h = None
    best = {"t": times[0] if times else 0.0, "lines": -1}

    for t in times:
        img = grab_frame_at(path, t)
        if img is None:
            continue
        gray = _to_gray_small(img, 720)
        if frame_h is None:
            frame_h = gray.shape[0]
        dark = _dark_ink(gray)
        horiz = _horizontal_lines(dark, gray.shape[1] * 0.06)
        rows = _staff_line_rows(horiz, gray.shape[0], gray.shape[1])
        line_rows_per_frame.append(rows)
        if len(rows) > best["lines"]:
            best["lines"] = len(rows)
            best["t"] = t

    if frame_h is None:
        box = _fallback_crop_box(path, duration, width, height)
        return {"crop": box, "best_t": 0.0, "score": 0.0}

    # 用「抓得到最多譜線」的那幾張畫面決定譜面所在的垂直範圍，比亮度啟發式可靠。
    candidate_sets = sorted(line_rows_per_frame, key=len, reverse=True)[:3]
    rows = sorted({r for rs in candidate_sets for r in rs})

    span = None
    if len(rows) >= 4:
        clusters = _group_rows(np.isin(np.arange(frame_h), rows),
                               gap=max(2, int(frame_h * 0.12)))
        if clusters:
            best_cluster = max(
                clusters,
                key=lambda ab: sum(1 for r in rows if ab[0] <= r <= ab[1]),
            )
            count = sum(1 for r in rows if best_cluster[0] <= r <= best_cluster[1])
            if count >= 4:
                span = best_cluster

    if span and (span[1] - span[0]) >= height * 0.05:
        scale = height / float(frame_h)
        pad = int(height * 0.03)
        y0 = max(0, int(span[0] * scale) - pad)
        y1 = min(height, int((span[1] + 1) * scale) + pad)
        crop = {"x": 0, "y": y0, "w": int(width), "h": max(24, y1 - y0)}
    else:
        crop = _fallback_crop_box(path, duration, width, height)

    preview = grab_frame_at(path, best["t"])
    score = 0.0
    if preview is not None:
        try:
            base_feat = analyze_score_frame(crop_image(preview, crop))
            score = base_feat["score"]
        except Exception:
            base_feat = {"score": 0.0, "is_score": False}

        # 用「白紙區域」微調框選範圍：把四周的黑邊、講者畫面修掉，
        # 同時保留頁首標題 (只框譜線會把標題切掉)。只有在原本就抓到譜面、
        # 而且微調後仍然抓得到譜面時才採用，避免框歪反而變差。
        if base_feat.get("is_score"):
            try:
                gray_full = _to_gray_small(preview, 720)
                band_small = (
                    int(crop["y"] / float(height) * frame_h),
                    int((crop["y"] + crop["h"]) / float(height) * frame_h),
                )
                region = _paper_region(gray_full, band_small)
                if region:
                    (ry0, ry1), (rx0, rx1) = region
                    sx = width / float(gray_full.shape[1])
                    sy = height / float(frame_h)
                    x0 = max(0, int(rx0 * sx))
                    y0 = max(0, int(ry0 * sy))
                    x1 = min(width, int((rx1 + 1) * sx))
                    y1 = min(height, int((ry1 + 1) * sy))
                    refined = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                    if refined["w"] >= width * 0.15 and refined["h"] >= height * 0.10:
                        refined_feat = analyze_score_frame(crop_image(preview, refined))
                        if refined_feat.get("is_score") or refined_feat["score"] >= base_feat["score"] * 0.95:
                            crop = refined
                            score = max(score, refined_feat["score"])
            except Exception:
                pass

    return {"crop": crop, "best_t": float(best["t"]), "score": float(score)}


def suggest_crop_box(path, duration, samples=12):
    """相容舊介面：只回傳建議框選區域"""
    return suggest_layout(path, duration, samples=samples)["crop"]


# ---------------------------------------------------------------------------
# 自動掃描整部影片
# ---------------------------------------------------------------------------

def auto_scan_video(path, crop_box, interval=2.0, sensitivity=50,
                    progress_cb=None, cancel_cb=None):
    """
    以固定的時間間隔掃描整部影片，找出所有「正在顯示譜面」的時間點，
    並把連續、內容相同的畫面合併成一頁（取其中解析度最清楚的一張）。

    回傳 list: [{"t": 秒, "score": 信心, "lines": 譜線數, "sharpness": 清晰度}]
    """
    duration, width, height, fps = get_video_info(path)
    if duration <= 0:
        raise RuntimeError("讀不到影片長度，無法自動掃描")

    interval = max(0.2, float(interval))
    dedupe_thr = _dedupe_threshold(sensitivity)
    total = max(1, int(duration / interval))
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("無法開啟影片")

    groups = []          # [{"sig":..., "frames":[{...}]}]
    current = None
    scanned = 0
    found = 0

    try:
        for i in range(total):
            if cancel_cb and cancel_cb():
                break
            t = i * interval
            with _CAPTURE_LOCK:
                frame = _read_frame_at(cap, t)
            scanned += 1
            if frame is None:
                continue

            crop = frame[crop_box["y"]:crop_box["y"] + crop_box["h"],
                         crop_box["x"]:crop_box["x"] + crop_box["w"]]
            if crop.size == 0:
                continue
            pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))

            try:
                feat = analyze_score_frame(pil)
            except Exception:
                feat = {"is_score": False}

            if feat.get("is_score"):
                sig = feat["signature"]
                if current is not None and signature_distance(current["sig"], sig) <= dedupe_thr:
                    current["frames"].append(
                        {"t": t, "score": feat["score"], "sharpness": feat["sharpness"], "lines": feat["lines"]}
                    )
                else:
                    current = {"sig": sig, "frames": [
                        {"t": t, "score": feat["score"], "sharpness": feat["sharpness"], "lines": feat["lines"]}
                    ]}
                    groups.append(current)
                    found += 1
            else:
                current = None

            if progress_cb and (i % 5 == 0 or i == total - 1):
                pct = int((i + 1) / total * 100)
                progress_cb(min(99, pct), "掃描中... 已找到 {} 頁 ({}/{})".format(found, i + 1, total))
    finally:
        cap.release()

    if progress_cb:
        progress_cb(100, "掃描完成，共找到 {} 頁".format(found))

    result = []
    for g in groups:
        # 同一頁停留期間，挑最清楚的一張 (分數接近時以清晰度決勝)
        best = max(g["frames"], key=lambda f: (round(f["score"], 2), f["sharpness"]))
        result.append({
            "t": float(best["t"]),
            "score": float(best["score"]),
            "lines": int(best["lines"]),
            "sharpness": float(best["sharpness"]),
        })
    result.sort(key=lambda f: f["t"])
    return result


# ---------------------------------------------------------------------------
# 主要流程
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONT = os.path.join(BASE_DIR, "assets", "fonts", "NotoSansCJK-Regular.ttc")


def crop_image(img, box):
    """依框選區域裁切，並自動夾在圖片範圍內 (框超出邊界不會產生黑邊)"""
    x = int(round(box["x"]))
    y = int(round(box["y"]))
    w = int(round(box["w"]))
    h = int(round(box["h"]))
    img_w, img_h = img.size
    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = max(1, min(w, img_w - x))
    h = max(1, min(h, img_h - y))
    return img.crop((x, y, x + w, y + h))


# 舊名稱，保留給既有程式碼使用
_crop = crop_image


# ---------------------------------------------------------------------------
# 智慧畫質增強
# ---------------------------------------------------------------------------

def enhance_quality(pil_img, scale=2):
    """
    智慧畫質增強 (白得更白、黑得更黑但不失真)：
    1. 放大圖片保留細節。
    2. 轉為灰階後，尋找亮度分佈的極端值 (例如前 3% 暗與前 95% 亮)。
    3. 進行對比度拉伸 (Contrast Stretching)，將這些極端值對應到純黑(0)與純白(255)，
       中間的顏色則做線性過渡。
    這可以有效消除背景的灰暗感，讓黑色線條更深，但又不會像二值化那樣產生鋸齒。
    """
    w, h = pil_img.size
    big = pil_img.resize((w * scale, h * scale), Image.LANCZOS)
    arr = np.array(big.convert("L"))

    # 尋找 3% 和 95% 的亮度作為拉伸基準
    p_low, p_high = np.percentile(arr, (3, 95))
    if p_high > p_low:
        arr = np.clip((arr - p_low) * 255.0 / (p_high - p_low), 0, 255).astype(np.uint8)

    # 輕微銳化
    blurred = cv2.GaussianBlur(arr, (0, 0), 1.0)
    arr = cv2.addWeighted(arr, 1.2, blurred, -0.2, 0)

    return Image.fromarray(arr).convert("RGB")


# ---------------------------------------------------------------------------
# PDF 拼接
# ---------------------------------------------------------------------------

PAGE_SIZES_PX = {
    "A4": (1240, 1754),      # 150 dpi
    "Letter": (1275, 1650),  # 150 dpi
}
PDF_DPI = 150


def build_pdf(image_paths, out_path, title="鋼琴五線譜", subtitle="", page_size="A4", enhance=False):
    if not image_paths:
        raise RuntimeError("沒有可用的譜面圖片可以組成 PDF")

    subtitle = (subtitle or "").strip()

    page_w, page_h = PAGE_SIZES_PX.get(page_size, PAGE_SIZES_PX["A4"])
    margin = 40
    header_h = 95 if subtitle else 70
    gap = 18
    content_w = page_w - margin * 2
    content_h = page_h - header_h - margin  # 單張圖片最多可用的高度

    pages = []
    page = Image.new("RGB", (page_w, page_h), "white")
    cursor_y = margin

    def new_page():
        nonlocal page, cursor_y
        pages.append(page)
        page = Image.new("RGB", (page_w, page_h), "white")
        cursor_y = margin

    def draw_header(pg, page_no):
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(pg)
            try:
                font = ImageFont.truetype(BUNDLED_FONT, 28)
            except Exception:
                font = ImageFont.load_default()
            draw.text((margin, 20), title, fill="black", font=font)
            if subtitle:
                try:
                    sub_font = ImageFont.truetype(BUNDLED_FONT, 16)
                except Exception:
                    sub_font = font
                draw.text((margin, 56), subtitle, fill=(100, 100, 100), font=sub_font)
            draw.text(
                (page_w - margin - 60, page_h - 32),
                "- {} -".format(page_no),
                fill=(120, 120, 120),
                font=font,
            )
        except Exception:
            pass

    draw_header(page, len(pages) + 1)
    cursor_y = header_h

    for p in image_paths:
        with Image.open(p) as src:
            im = src.convert("RGB")
        if enhance:
            im = enhance_quality(im, scale=2)

        new_w = content_w
        new_h = int(im.height * (content_w / float(im.width)))

        # 單張圖比一頁還高時，等比縮小塞進一頁 (以前會直接被裁掉)
        if new_h > content_h:
            fit = content_h / float(new_h)
            new_w = max(1, int(new_w * fit))
            new_h = content_h

        if cursor_y + new_h + margin > page_h:
            new_page()
            draw_header(page, len(pages) + 1)
            cursor_y = header_h

        im_resized = im.resize((new_w, new_h), Image.LANCZOS)
        page.paste(im_resized, (margin + (content_w - new_w) // 2, cursor_y))
        cursor_y += new_h + gap

    pages.append(page)

    first, rest = pages[0], pages[1:]
    # resolution 一定要指定：否則 PDF 會以 72 dpi 解釋，A4 會變成 17x24 英吋
    first.save(out_path, save_all=True, append_images=rest, resolution=PDF_DPI, title=title)
    return out_path
