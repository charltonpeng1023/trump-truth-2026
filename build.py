#!/usr/bin/env python3
"""
川普 Truth Social 貼文分析器 — 資料建置腳本

由 GitHub Actions 每 6 小時自動執行一次：
  1. 抓取 CNN 維護的 @realDonaldTrump 公開存檔
  2. 取出滾動視窗內的貼文（2026 年至今；跨年後改為最近 12 個月）
  3. 依 topics.json 的關鍵字計算每則貼文的主題標記與逐月統計
  4. 輸出 data.json 供網頁載入

要新增追蹤主題，只要改 topics.json，不用動這支程式。
"""

import json
import os
import re
import html
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SOURCE = "https://ix.cnn.io/data/truth-social/truth_archive.json"
OCR_FILE = "ocr.json"
ET = ZoneInfo("America/New_York")
EARLIEST = datetime(2026, 1, 1, tzinfo=timezone.utc)
WINDOW_DAYS = 365

# 「語氣」這一組統計的是川普自己怎麼寫字，所以只比對他打出來的內文，
# 不把圖片裡辨識到的字算進去（新聞截圖和迷因本來就常常整排大寫）。
TEXT_ONLY_GROUP = "語氣"

# OCR 難免有雜訊。用常見英文字的比例當門檻，比例太低的多半是把
# 圖形當成字，整段捨棄，寧可漏掉也不要餵錯的字進統計。
COMMON = set("""the and of to in a is that for on with it as was are by this be from at
have has not will you we he his they said but all new their who more one out about up
than over into after been can if no now our us what when which would""".split())
MIN_COMMON_RATIO = 0.10


def usable(t: str) -> bool:
    words = re.findall(r"[a-z\']+", t.lower())
    if len(words) < 5:
        return False
    return sum(1 for w in words if w in COMMON) / len(words) >= MIN_COMMON_RATIO


def fetch(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "trump-truth-2026/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def compile_topics(cfg: dict):
    """把 topics.json 攤平成 (群組, 標籤, 已編譯的 regex) 清單。"""
    out = []
    for group in cfg["groups"]:
        for t in group["topics"]:
            flags = 0 if t.get("cs") else re.IGNORECASE
            try:
                rx = re.compile(t["pattern"], flags)
            except re.error as e:
                print(f"  略過主題「{t['label']}」：正規表達式有誤 {e}", file=sys.stderr)
                continue
            out.append((group["name"], t["label"], rx, bool(t.get("alert"))))
    return out


def main() -> int:
    print(f"抓取來源：{SOURCE}")
    raw = fetch(SOURCE)
    print(f"  存檔總則數 {len(raw):,}")

    now = datetime.now(timezone.utc)
    start = max(EARLIEST, now - timedelta(days=WINDOW_DAYS))
    print(f"  取用區間 {start.date()} 起")

    # 圖片文字（由 ocr.py 產生）。有三分之一的貼文只有圖沒有字，
    # 把辨識出來的文字併進來，那些貼文才進得了關鍵字統計。
    ocr = {}
    if os.path.exists(OCR_FILE):
        with open(OCR_FILE, encoding="utf-8") as f:
            ocr = json.load(f).get("texts", {})
    print(f"  圖片文字 {sum(1 for v in ocr.values() if v):,} 張有內容 / 共辨識 {len(ocr):,} 張")

    with open("topics.json", encoding="utf-8") as f:
        cfg = json.load(f)
    topics = compile_topics(cfg)
    print(f"  主題數 {len(topics)}")

    posts, skipped = [], 0
    for x in raw:
        ts = x.get("created_at")
        if not ts:
            skipped += 1
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            skipped += 1
            continue
        if dt < start:
            continue

        text = html.unescape((x.get("content") or "")).strip()
        media = x.get("media") or []
        joined = " ".join(media)
        kind = 1 if (media and not text) else (2 if media else 0)

        img_text = " ".join(
            t for t in (ocr.get(f"{x['id']}:{i}", "") for i in range(len(media)))
            if t and usable(t)
        ).strip()
        # 關鍵字比對時把圖片文字一起算進去
        haystack = (text + " " + img_text).strip()

        # 主題以索引陣列儲存，不用位元遮罩：主題數已超過 JavaScript
        # 位元運算的 32 位元上限，用陣列才不會在瀏覽器端算錯。
        hits = []
        for i, (g, _l, rx, _a) in enumerate(topics):
            target = text if g == TEXT_ONLY_GROUP else haystack
            if target and rx.search(target):
                hits.append(i)

        flags = 0
        if re.match(r"^RT @", text):
            flags |= 1
        if re.search(r"\.mp4", joined, re.I):
            flags |= 2
        if re.search(r"\.(jpg|jpeg|png|gif|webp)", joined, re.I):
            flags |= 4

        posts.append([
            x["id"],
            dt.astimezone(ET).strftime("%Y-%m-%dT%H:%M"),
            text,
            kind,
            int(x.get("favourites_count") or 0),
            int(x.get("reblogs_count") or 0),
            int(x.get("replies_count") or 0),
            hits,
            flags,
            img_text,
        ])

    posts.sort(key=lambda p: p[1], reverse=True)
    print(f"  區間內 {len(posts):,} 則（跳過 {skipped} 筆時間格式異常）")
    if not posts:
        print("沒有取到任何貼文，中止以免覆蓋掉好的資料。", file=sys.stderr)
        return 1

    # 逐月統計：每個主題一組，外加總則數
    months = sorted({p[1][:7] for p in posts})
    monthly = {"__all__": {m: 0 for m in months}}
    for _g, label, _rx, _a in topics:
        monthly[label] = {m: 0 for m in months}
    for p in posts:
        m = p[1][:7]
        monthly["__all__"][m] += 1
        for i in p[7]:
            monthly[topics[i][1]][m] += 1

    # 逐日則數，供頁面上的日條圖使用
    daily = {}
    for p in posts:
        d = p[1][:10]
        daily[d] = daily.get(d, 0) + 1

    days_span = (datetime.fromisoformat(posts[0][1]).date()
                 - datetime.fromisoformat(posts[-1][1]).date()).days + 1
    data = {
        "generated_at": now.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "generated_at_tpe": now.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M"),
        "source": SOURCE,
        "archive_total": len(raw),
        "range": [posts[-1][1][:10], posts[0][1][:10]],
        "days_span": days_span,
        "ocr": {"scanned": len(ocr),
                "with_text": sum(1 for v in ocr.values() if v and usable(v))},
        "topics": [{"group": g, "label": l, "alert": a} for g, l, _rx, a in topics],
        "monthly": monthly,
        "daily": daily,
        "posts": posts,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize("data.json")
    print(f"寫出 data.json：{len(posts):,} 則、{size/1024/1024:.2f} MB")
    print(f"  區間 {data['range'][0]} 至 {data['range'][1]}（{days_span} 天）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
