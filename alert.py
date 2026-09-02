#!/usr/bin/env python3
"""
警示檢查 — 每 15 分鐘由 GitHub Actions 執行一次。

只做一件事：看看川普有沒有發出提到「警示主題」的新貼文。
警示主題在 topics.json 裡用 "alert": true 標記，改設定檔就會跟著換。

第一次執行時不會發警示，只會把當下已經存在的命中貼文記進 seen.json
當作基準，避免一開跑就把過去幾個月的舊貼文全部通知一遍。
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
SEEN_FILE = "seen.json"
BODY_FILE = "alert_body.md"
ET = ZoneInfo("America/New_York")
TPE = ZoneInfo("Asia/Taipei")
LOOKBACK_DAYS = 3          # 只看最近三天，避免補抓太舊的東西
SEEN_CAP = 2000            # seen.json 只留最近這麼多筆，不會無限長大
SITE = "https://charltonpeng1023.github.io/trump-truth-2026/"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "trump-truth-2026-alert/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def alert_topics():
    with open("topics.json", encoding="utf-8") as f:
        cfg = json.load(f)
    out = []
    for group in cfg["groups"]:
        for t in group["topics"]:
            if not t.get("alert"):
                continue
            flags = 0 if t.get("cs") else re.IGNORECASE
            out.append((t["label"], re.compile(t["pattern"], flags)))
    return out


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"::{name}={value}")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def main() -> int:
    topics = alert_topics()
    if not topics:
        print("topics.json 裡沒有任何 alert 主題，不做事。")
        set_output("count", 0)
        return 0
    print("警示主題：" + "、".join(l for l, _ in topics))

    raw = fetch(SOURCE)
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    matched = []
    for x in raw:
        ts = x.get("created_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        text = html.unescape((x.get("content") or "")).strip()
        if not text:
            continue
        hit = [label for label, rx in topics if rx.search(text)]
        if hit:
            matched.append((dt, x["id"], text, hit))
    matched.sort(key=lambda m: m[0], reverse=True)
    print(f"最近 {LOOKBACK_DAYS} 天內命中 {len(matched)} 則")

    first_run = not os.path.exists(SEEN_FILE)
    seen = []
    if not first_run:
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen = json.load(f).get("ids", [])
    seen_set = set(seen)

    fresh = [m for m in matched if m[1] not in seen_set]

    # 不管有沒有新的，都把目前命中的 id 記起來
    new_seen = [m[1] for m in matched] + seen
    dedup, out_ids = set(), []
    for i in new_seen:
        if i not in dedup:
            dedup.add(i)
            out_ids.append(i)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
                   "ids": out_ids[:SEEN_CAP]}, f, ensure_ascii=False, indent=1)

    if first_run:
        print(f"第一次執行，把現有的 {len(matched)} 則記為基準，不發警示。")
        set_output("count", 0)
        return 0

    if not fresh:
        print("沒有新的命中貼文。")
        set_output("count", 0)
        return 0

    labels = sorted({l for m in fresh for l in m[3]})
    title = f"川普提到{'、'.join(labels)}（{len(fresh)} 則）"

    lines = [f"川普在 Truth Social 發出 **{len(fresh)}** 則提到"
             f"**{'、'.join(labels)}**的貼文。", ""]
    for dt, pid, text, hit in fresh:
        et = dt.astimezone(ET).strftime("%Y-%m-%d %H:%M")
        tpe = dt.astimezone(TPE).strftime("%m-%d %H:%M")
        lines += [
            f"### {tpe}（台北）／{et} ET　·　{'、'.join(hit)}",
            "",
            "> " + text.replace("\n", "\n> ")[:1500],
            "",
            f"原文：https://truthsocial.com/@realDonaldTrump/{pid}",
            "",
            "---",
            "",
        ]
    lines += [f"完整追蹤器：{SITE}", "",
              "_這則警示由 GitHub Actions 自動產生。要調整警示主題，改 `topics.json` 裡的 `alert` 旗標。_"]
    with open(BODY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"發出警示：{title}")
    set_output("count", len(fresh))
    set_output("title", title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
