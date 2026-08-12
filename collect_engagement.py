# -*- coding: utf-8 -*-
"""
DeviantArt投稿の反応(favourites/comments)を回収して engagement.json へ書き出す。
/gallery/all は deviation ごとの stats を返すので、deviationid で posted_log.json と
結合できる。autonomy/analyze_variants.py が「どのタイトル変種が反応を取ったか」を採点する。

GitHub Actions で upload 後に実行し、posted_log.json と一緒に commit する。
失敗しても exit 0（計測は投稿を絶対に止めない）。
"""
import json
import os
import sys
import time

import requests

from upload import (
    AuthenticationError,
    DA_ACCESS_TOKEN,
    DA_REFRESH_TOKEN,
    TOKENS_FILE,
    get_valid_token,
)

OUT_PATH = "engagement.json"
MAX_DEVIATIONS = 72  # 直近72作品分を追跡
PAGE_SIZE = 24


def load_run_tokens():
    """同一run内で upload.py が書いた tokens.json（リフレッシュ済み）を優先する。
    Secrets の DA_ACCESS_TOKEN/DA_REFRESH_TOKEN は run 開始時点の値のため、
    upload.py が refresh_token を消費済みだと必ず invalid_request になる。"""
    try:
        with open(TOKENS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        at, rt = d.get("access_token", ""), d.get("refresh_token", "")
        if at or rt:
            return at, rt
    except Exception:
        pass
    return DA_ACCESS_TOKEN, DA_REFRESH_TOKEN


def main():
    try:
        access_token, _ = get_valid_token(*load_run_tokens())
    except AuthenticationError as e:
        print(f"skip: DeviantArt authentication unavailable: {e}")
        return 0
    if not access_token:
        print("skip: no valid DeviantArt token")
        return 0

    existing = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f).get("deviations", {})
        except Exception:
            existing = {}

    fetched = {}
    offset = 0
    while offset < MAX_DEVIATIONS:
        try:
            r = requests.get(
                "https://www.deviantart.com/api/v1/oauth2/gallery/all",
                params={
                    "access_token": access_token,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "mature_content": "true",
                },
                timeout=60,
            )
        except Exception as e:
            print(f"fetch error at offset {offset}: {e}")
            break
        if r.status_code != 200:
            print(f"gallery fetch failed: {r.status_code} {r.text[:200]}")
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for d in results:
            did = str(d.get("deviationid", ""))
            if not did:
                continue
            stats = d.get("stats", {}) or {}
            fetched[did] = {
                "favourites": int(stats.get("favourites", 0) or 0),
                "comments": int(stats.get("comments", 0) or 0),
                "url": d.get("url", ""),
                "title": d.get("title", ""),
                "published_time": d.get("published_time", ""),
            }
        if not data.get("has_more"):
            break
        offset = data.get("next_offset") or (offset + PAGE_SIZE)

    if not fetched:
        print("no deviations fetched; keeping existing engagement.json")
        return 0

    existing.update(fetched)
    out = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "deviations": existing,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"engagement.json updated: {len(fetched)} fetched / {len(existing)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
