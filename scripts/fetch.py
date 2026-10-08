"""구글 뉴스 RSS에서 키워드 기사를 모아 docs/data/YYYY-MM.json 에 누적한다.

- 표준 라이브러리만 사용 (pip 설치 불필요)
- 키워드·제외어·조회창은 docs/config.json 에서 읽는다
- 이미 있는 기사(id 또는 같은 언론사+같은 제목)는 건너뛰고, 기존 기사는 절대 지우지 않는다
- --backfill-days N : 과거 N일치를 하루 단위로 소급 수집 (처음 시작할 때 1회)
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; news-archive-bot/1.0)"


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def fetch_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return ET.fromstring(res.read())
        except Exception as e:  # 네트워크 일시 오류는 재시도
            print(f"  재시도 {attempt + 1}/3: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return None


def clean_title(title, source):
    # 구글 RSS 제목은 "기사제목 - 언론사" (가끔 언론사가 두 번 붙음)
    title = (title or "").strip()
    suffix = " - " + source
    while source and title.endswith(suffix):
        title = title[: -len(suffix)].rstrip()
    return title


def norm(text):
    return re.sub(r"[\W_]+", "", text).lower()


def parse_items(root, keyword):
    items = []
    for it in root.iter("item"):
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        guid = (it.findtext("guid") or it.findtext("link") or "").strip()
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        items.append({
            "id": hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16],
            "title": clean_title(it.findtext("title"), source),
            "source": source,
            "sourceUrl": src_el.get("url", "") if src_el is not None else "",
            "link": (it.findtext("link") or "").strip(),
            "pub": pub.isoformat(timespec="minutes"),
            "q": [keyword],
        })
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=0)
    args = ap.parse_args()

    cfg = load_json(DOCS / "config.json", {})
    keywords = cfg.get("queries") or []
    exclude = cfg.get("exclude") or []
    window = cfg.get("window") or "6h"
    if not keywords:
        sys.exit("docs/config.json 에 queries(키워드)가 없습니다.")

    # (키워드, 실제 검색어) 목록 — 구글 RSS는 한 번에 최대 100건이라 기간을 잘게 나눠 조회
    searches = []
    if args.backfill_days > 0:
        today = datetime.now(KST).date()
        for i in range(args.backfill_days, 0, -1):
            d = today - timedelta(days=i)
            for kw in keywords:
                searches.append((kw, f"{kw} after:{d} before:{d + timedelta(days=1)}"))
    for kw in keywords:
        searches.append((kw, f"{kw} when:{window}"))

    found = {}
    for kw, query in searches:
        root = fetch_rss(query)
        if root is None:
            print(f"[실패] {query}", file=sys.stderr)
            continue
        items = parse_items(root, kw)
        print(f"{query}: {len(items)}건")
        if len(items) >= 100:
            print("  ⚠ 100건 상한에 걸림 — window 를 줄이세요", file=sys.stderr)
        for it in items:
            if any(x in it["title"] for x in exclude):
                continue
            if it["id"] in found:
                if kw not in found[it["id"]]["q"]:
                    found[it["id"]]["q"].append(kw)
            else:
                found[it["id"]] = it
        time.sleep(1.5)

    # 기존 데이터 로드
    shards, by_id, keys = {}, {}, set()
    for path in sorted(DATA.glob("????-??.json")):
        arr = load_json(path, [])
        shards[path.stem] = arr
        for x in arr:
            by_id[x["id"]] = x
            keys.add((norm(x["title"]), x["source"]))

    now = datetime.now(KST).isoformat(timespec="minutes")
    added = 0
    for it in sorted(found.values(), key=lambda x: x["pub"]):
        old = by_id.get(it["id"])
        if old:
            for kw in it["q"]:
                if kw not in old.setdefault("q", []):
                    old["q"].append(kw)
            continue
        key = (norm(it["title"]), it["source"])
        if key in keys:
            continue
        it["got"] = now
        shards.setdefault(it["pub"][:7], []).append(it)
        by_id[it["id"]] = it
        keys.add(key)
        added += 1

    DATA.mkdir(parents=True, exist_ok=True)
    months = []
    for month in sorted(shards, reverse=True):
        arr = sorted(shards[month], key=lambda x: x["pub"], reverse=True)
        (DATA / f"{month}.json").write_text(
            json.dumps(arr, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        months.append({"m": month, "n": len(arr)})
    (DATA / "index.json").write_text(
        json.dumps({"updated": now, "added": added, "months": months}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"새 기사 {added}건 추가 (전체 {sum(m['n'] for m in months)}건)")


if __name__ == "__main__":
    main()
