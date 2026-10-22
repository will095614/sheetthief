#!/bin/bash
# 鋼琴五線譜擷取工具 - Mac 一鍵啟動
# 第一次啟動會建立虛擬環境並安裝套件，需要幾分鐘；之後每次只要幾秒鐘。

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
  echo
  read -r -p "按 Enter 鍵關閉這個視窗..." _
  exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 Python 3。"
  echo "請先到 https://www.python.org/downloads/ 安裝 Python 3.9 以上版本，再執行這個檔案。"
  pause_and_exit
fi

if [ ! -d venv ]; then
  echo "第一次啟動：建立虛擬環境..."
  if ! python3 -m venv venv; then
    echo "建立虛擬環境失敗，請確認 Python 安裝是否完整。"
    pause_and_exit
  fi
fi

# shellcheck disable=SC1091
source venv/bin/activate || pause_and_exit

if [ ! -f venv/.deps_installed ]; then
  echo "安裝所需套件，第一次需要幾分鐘..."
  python -m pip install --upgrade pip --quiet
  if ! pip install -r requirements.txt --quiet; then
    echo "套件安裝失敗，請確認網路連線後再試一次。"
    pause_and_exit
  fi
  touch venv/.deps_installed
fi

PORT="${PORT:-5002}"
echo
echo "啟動伺服器... 瀏覽器會自動開啟 http://127.0.0.1:${PORT}"
echo "（要停止工具，請關閉這個視窗或按 Control + C）"
echo

(
  sleep 2
  if command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:${PORT}"
  fi
) &

python app.py
