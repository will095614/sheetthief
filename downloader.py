# -*- coding: utf-8 -*-
"""
從影片網址下載影片 (用 yt-dlp)，給「貼上網址」模式使用。

這個檔案故意跟 pipeline.py 分開，因為它牽涉到「對外發出網路請求」，
是這個工具裡唯一需要特別注意安全性的地方 (SSRF: 有心人可能把網址填成
伺服器內網位置，例如 http://127.0.0.1/... 或雲端主機的 metadata 位址，
想藉由這個工具去讀取伺服器內部資源)。所以下載前一定要先經過
`assert_public_http_url` 檢查。
"""

import glob
import ipaddress
import os
import socket
from urllib.parse import urlparse

import yt_dlp


class UnsafeURLError(ValueError):
    pass


def assert_public_http_url(url):
    """
    只允許 http/https，且網域解析出來的 IP 不能是內網/迴環/連結本地位址。
    部署在伺服器上、開放給不完全信任的使用者填網址時，這一步是必要的。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("只接受 http:// 或 https:// 開頭的網址")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("看不出這個網址的網域")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnsafeURLError("無法解析這個網域，請確認網址是否正確")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURLError(
                "這個網址解析到內部/保留網段的位址，基於安全考量不予下載"
            )


def download_video_from_url(url, out_dir, progress_cb=None, max_height=720):
    """
    下載影片到 out_dir，回傳實際檔案路徑。
    progress_cb(percent:int, message:str)
    """
    assert_public_http_url(url)

    def report(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            if total:
                pct = int(downloaded / total * 100)
                report(pct, "下載影片中... {}%".format(pct))
            else:
                mb = downloaded / (1024 * 1024)
                report(5, "下載影片中... 已下載 {:.1f} MB".format(mb))
        elif status == "finished":
            report(100, "下載完成，準備分析...")

    out_tmpl = os.path.join(out_dir, "source.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": (
            "bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            "best[height<={h}][ext=mp4]/best[height<={h}]/best"
        ).format(h=max_height),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 3,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    candidates = sorted(
        glob.glob(os.path.join(out_dir, "source.*")),
        key=lambda p: os.path.getsize(p),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("下載似乎完成了，但找不到輸出的影片檔案")
    return candidates[0]
