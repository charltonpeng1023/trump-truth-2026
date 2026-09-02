#!/usr/bin/env python3
"""
圖片文字辨識 — 由 GitHub Actions 在每次資料更新前執行。

川普有三分之一的貼文只有圖片或影片、沒有文字，那些貼文對關鍵字統計
來說等於空白。這支程式把圖片裡的文字用 OCR 抓出來，讓那些貼文也能
被搜尋、分類和警示。

設計上刻意保守：
  * 只處理圖片，不處理影片
  * 每次執行有時間上限，跑不完的下次接著跑，不會卡住整條更新流程
  * 辨識結果會過濾雜訊，品質太差的直接捨棄，寧可不要也不要餵髒資料
  * 只把辨識出的文字存進 ocr.json，圖片本身不入庫
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SOURCE = "https://ix.cnn.io/data/truth-social/truth_archive.json"
CACHE = "ocr.json"
TIME_BUDGET = 480          # 每次執行最多跑 8 分鐘，剩下的下次再處理
MAX_BYTES = 12 * 1024 * 1024
EARLIEST = datetime(2026, 1, 1, tzinfo=timezone.utc)
IMG = re.compile(r"\.(jpg|jpeg|png|gif|webp)(\?|$)", re.I)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "trump-truth-2026-ocr/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def clean(raw: str) -> str:
    """把 OCR 的雜訊濾掉。辨識不好的圖寧可回傳空字串。"""
    lines = []
    for line in raw.splitlines():
        line = " ".join(line.split())
        if len(line) < 4:
            continue
        letters = sum(c.isalpha() for c in line)
        # 一行裡字母佔比太低，多半是把圖形雜訊當成字
        if letters / max(1, len(line)) < 0.55:
            continue
        lines.append(line)
    text = " ".join(lines)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if len(text) < 20:
        return ""
    words = re.findall(r"[A-Za-z]{2,}", text)
    # 真正的句子會有一定數量的多字母單詞
    if len(words) < 5:
        return ""
    return text[:1200]


def ocr_one(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            blob = r.read(MAX_BYTES)
    except Exception as e:
        print(f"    下載失敗：{type(e).__name__}", file=sys.stderr)
        return ""
    suffix = os.path.splitext(url.split("?")[0])[1][:5] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        p = subprocess.run(
            ["tesseract", path, "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True, text=True, timeout=60)
        return clean(p.stdout or "")
    except Exception as e:
        print(f"    辨識失敗：{type(e).__name__}", file=sys.stderr)
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f).get("texts", {})
    print(f"已辨識過 {len(cache):,} 張")

    raw = fetch_json(SOURCE)
    todo = []
    for x in raw:
        ts = x.get("created_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < EARLIEST:
            continue
        for i, url in enumerate(x.get("media") or []):
            if not IMG.search(url):
                continue
            key = f"{x['id']}:{i}"
            if key in cache:
                continue
            todo.append((dt, key, url))

    todo.sort(key=lambda t: t[0], reverse=True)   # 新的先做
    print(f"待辨識 {len(todo):,} 張，這次最多跑 {TIME_BUDGET} 秒")

    start = time.time()
    done = hit = 0
    for _dt, key, url in todo:
        if time.time() - start > TIME_BUDGET:
            print("  時間到，其餘留給下次執行")
            break
        text = ocr_one(url)
        cache[key] = text
        done += 1
        if text:
            hit += 1
            print(f"  {key} → {text[:70]}")

    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M"),
            "done": len(cache),
            "texts": cache,
        }, f, ensure_ascii=False)

    left = max(0, len(todo) - done)
    print(f"這次處理 {done} 張，其中 {hit} 張抓到可用文字；還有 {left:,} 張待處理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
