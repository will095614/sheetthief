# -*- coding: utf-8 -*-
"""
鋼琴五線譜自動擷取 - 核心處理邏輯

流程：
1. 使用者提供影片，由這裡取得影片資訊與預覽圖。
2. 使用者手動設定框選區域並擷取畫面。
3. 最終將所有擷取到的圖片直向拼接成 A4/Letter 頁面，輸出成 PDF。
"""

import os
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
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps if fps > 0 else 0
    cap.release()
    return duration, width, height, fps


def grab_frame_at(path, t_sec):
    """抓取影片在 t_sec 秒時的畫面，回傳 PIL.Image (RGB) 或 None"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t_sec) * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


# ---------------------------------------------------------------------------
# 自動建議譜面框選區域
# ---------------------------------------------------------------------------

def suggest_crop_box(path, duration, samples=10):
    """
    抽樣數張畫面，找出「細節最豐富、最穩定」的水平帶狀區域，
    當作預設的譜面框選建議 (使用者仍可在介面上手動調整)。
    回傳 dict: {x, y, w, h} (原始影片像素座標)
    """
    duration, width, height, fps = get_video_info(path)
    ts = [duration * (i + 1) / (samples + 1) for i in range(samples)]
    light_profiles = []
    for t in ts:
        img = grab_frame_at(path, t)
        if img is None:
            continue
        arr = np.array(img).astype(np.float32)
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        sat = mx - mn
        light = (mx > 190) & (sat < 40)
        light_profiles.append(light.mean(axis=1))

    if not light_profiles:
        return {"x": 0, "y": int(height * 0.55), "w": width, "h": int(height * 0.4)}

    med = np.median(np.array(light_profiles), axis=0)

    y0 = None
    for i in range(len(med) - 5):
        if med[i] < 0.3 and np.mean(med[i + 1:i + 6]) > 0.7:
            y0 = i + 1
            break

    if y0 is None:
        y0 = int(height * 0.55)

    default_h = int(height * 0.4)
    y1 = min(height, y0 + default_h)

    i = height - 1
    while i > y0 and med[i] > 0.85:
        i -= 1
    if i > y0:
        dark_bottom_run = height - 1 - i
        dark_top = i
        while dark_top > y0 and med[dark_top] < 0.85:
            dark_top -= 1
        candidate_y1 = dark_top + 1
        if dark_bottom_run >= 3 and candidate_y1 - y0 >= 40:
            y1 = min(y1, candidate_y1)

    return {"x": 0, "y": int(y0), "w": int(width), "h": int(y1 - y0)}


# ---------------------------------------------------------------------------
# 主要流程
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONT = os.path.join(BASE_DIR, "assets", "fonts", "NotoSansCJK-Regular.ttc")

def _crop(img, box):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    return img.crop((x, y, x + w, y + h))


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
    "A4": (1240, 1754),
    "Letter": (1275, 1650),
}

def build_pdf(image_paths, out_path, title="鋼琴五線譜", subtitle="", page_size="A4", enhance=False):
    if not image_paths:
        raise RuntimeError("沒有可用的譜面圖片可以組成 PDF")

    subtitle = (subtitle or "").strip()

    page_w, page_h = PAGE_SIZES_PX.get(page_size, PAGE_SIZES_PX["A4"])
    margin = 40
    header_h = 95 if subtitle else 70
    gap = 18
    content_w = page_w - margin * 2

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
                (page_w - margin - 60, page_h - 30),
                "- {} -".format(page_no),
                fill="gray",
                font=font,
            )
        except Exception:
            pass

    draw_header(page, len(pages) + 1)
    cursor_y = header_h

    for p in image_paths:
        im = Image.open(p).convert("RGB")
        if enhance:
            im = enhance_quality(im, scale=2)
            
        scale = content_w / im.width
        new_w = content_w
        new_h = int(im.height * scale)
        im_resized = im.resize((new_w, new_h), Image.LANCZOS)

        if cursor_y + new_h + margin > page_h:
            new_page()
            draw_header(page, len(pages) + 1)
            cursor_y = header_h

        page.paste(im_resized, (margin, cursor_y))
        cursor_y += new_h + gap

    pages.append(page)

    first, rest = pages[0], pages[1:]
    first.save(out_path, save_all=True, append_images=rest)
    return out_path
