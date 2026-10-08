"""구글 뉴스 RSS에서 키워드 기사를 모아 docs/data/YYYY-MM.json 에 누적한다.

- 표준 라이브러리만 사용 (pip 설치 불필요)
- 키워드·제외어·조회창·자동 태그 규칙·언론사 별칭은 docs/config.json 에서 읽는다
- 이미 있는 기사(id 또는 같은 언론사+같은 제목)는 건너뛴다
- 구글 RSS가 언론사 자리에 포털(네이트·다음 등)을 적어 보내는 기사는
  포털 기사 페이지를 열어 실제 발행 언론사를 찾아 바로잡는다 (item.portal = 경유 포털)
- --backfill-days N : 과거 N일치를 하루 단위로 소급 수집 (처음 시작할 때 1회)
"""
import argparse
import hashlib
import html as html_lib
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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# 기사 원문이 아니라 포털에 실린 사본 → 실제 언론사를 따로 찾아야 함
PORTALS = {
    "nate.com": "네이트",
    "daum.net": "다음",
    "naver.com": "네이버",
    "zum.com": "줌",
    "msn.com": "MSN",
}
RESOLVE_LIMIT = 100  # 한 번 실행에서 포털 기사 언론사 확인 최대 건수 (나머지는 다음 실행에)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def http(url, data=None, timeout=20):
    headers = {"User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        data = data.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def fetch_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )
    for attempt in range(3):
        try:
            return ET.fromstring(http(url, timeout=30))
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


def host_of(url):
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return re.sub(r"^(www|m|mobile)\.", "", host)


def portal_of(source_url):
    host = host_of(source_url)
    for domain, name in PORTALS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return ""


def auto_tags(title, rules):
    # config.json 의 autoTags: {"태그": ["제목에 들어가면 붙일 단어", ...]}
    return [tag for tag, words in rules.items() if any(w in title for w in words)]


# ---------- 포털 기사 → 실제 언론사 ----------
def decode_google_link(link):
    """news.google.com/rss/articles/... 링크를 실제 기사 주소로 변환"""
    art_id = link.split("/articles/")[1].split("?")[0]
    page = http(f"https://news.google.com/articles/{art_id}").decode("utf-8", "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return ""
    inner = json.dumps(
        ["garturlreq",
         [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
          "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
         art_id, int(ts.group(1)), sg.group(1)],
        separators=(",", ":"), ensure_ascii=False,
    )
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"), ensure_ascii=False)
    res = http("https://news.google.com/_/DotsSplashUi/data/batchexecute",
               data=urllib.parse.urlencode({"f.req": freq})).decode("utf-8", "replace")
    m = re.search(r'https?://[^\\"]+', res)
    return m.group(0) if m else ""


# 1단계: 페이지 구조(HTML)에서 언론사 찾기
PRESS_HTML_PATTERNS = [
    # 다음·네이버: <meta property="og:article:author" content="스포츠동아">
    r'<meta[^>]+property="og:article:author"[^>]+content="([^"]+)"',
    r'<meta[^>]+content="([^"]+)"[^>]+property="og:article:author"',
    # 네이트 PC: <a ... class="medium">MHN스포츠</a> / 모바일: <div class="medium"><div class="author"><em><b>스포츠서울</b>
    r'class="medium"[^>]*>\s*(?:<div class="author">\s*<em>\s*<b>)?\s*([^<]+?)\s*<',
]
# 2단계: 기사 본문 글자에서 찾기 — 하단 카피라이트, 첫 줄 "[언론사 | 기자]", "(서울=연합뉴스)"
PRESS_TEXT_PATTERNS = [
    r"(?:Copyright|저작권자)\s*(?:ⓒ|©|\([cC]\))?\s*(?:\d{4}(?:\s*[-~]\s*\d{4})?\s*)?(?:ⓒ|©)?\s*"
    r"([가-힣A-Za-z0-9㈜()·&\s]{2,30}?)\s*(?:\.|,|&|<|>|\||All\s|무단|www\.|[a-z0-9-]+\.(?:com|co\.kr|kr|net)\b)",
    r"(?:ⓒ|©)\s*(?:\d{4}\s*)?([가-힣][가-힣A-Za-z0-9㈜·\s]{1,20}?)\s*(?:\.|,|&|<|무단|All\s)",
    r"\[([가-힣A-Za-z0-9]{2,15})\s*[|=]\s*[가-힣\s]{2,10}기자\]",
    r"\([가-힣]{2,4}=([가-힣A-Za-z0-9]{2,10})\)",
]
# 포털·플랫폼 자신의 저작권 표시는 제외
NOT_PRESS = re.compile(r"^(Daum|Kakao|NATE|Nate|SK|네이트|다음|카카오|네이버|NAVER|Naver|ZUM|줌|MSN|Microsoft|Google)", re.I)


def clean_press(name):
    name = re.sub(r"㈜|\(주\)|주식회사", "", html_lib.unescape(name)).split("|")[0].strip()
    if not name or NOT_PRESS.match(name) or name in PORTALS.values() or len(name) > 20:
        return ""
    return name


def press_from_page(url):
    raw = http(url)
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("cp949", "replace")  # 네이트는 EUC-KR
    for pat in PRESS_HTML_PATTERNS:
        for m in re.finditer(pat, html):
            name = clean_press(m.group(1))
            if name:
                return name
    text = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", html)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    for pat in PRESS_TEXT_PATTERNS:
        for m in re.finditer(pat, text):
            name = clean_press(m.group(1))
            if name:
                return name
    return ""


def resolve_portal(item):
    """확인 시도 1회. 실패하면 다음 실행에서 재시도하고, 3번 실패하면 '언론사 미확인'으로 확정"""
    if item.get("resolved"):          # '언론사 미확인' 재확인은 1번만
        item["recheck"] = True
        item["tries"] = 2
    item["tries"] = item.get("tries", 0) + 1
    real = press = ""
    try:
        real = decode_google_link(item["link"]) if "news.google.com" in item["link"] else item["link"]
        time.sleep(1)
        press = press_from_page(real) if real else ""
        time.sleep(1)
    except Exception as e:
        print(f"  언론사 확인 실패 ({item['title'][:20]}…): {e}", file=sys.stderr)
    if real:
        item["link"] = real
    if press:
        item["source"] = press
    elif item["tries"] >= 3:
        item["source"] = "언론사 미확인"
    else:
        return False
    item["resolved"] = True
    item.pop("tries", None)
    return True


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
    shards, by_id = {}, {}
    for path in sorted(DATA.glob("????-??.json")):
        arr = load_json(path, [])
        shards[path.stem] = arr
        for x in arr:
            by_id[x["id"]] = x

    # 새 기사 추가 (중복 판정은 언론사 확인 뒤에)
    now = datetime.now(KST).isoformat(timespec="minutes")
    new_items = []
    for it in sorted(found.values(), key=lambda x: x["pub"]):
        old = by_id.get(it["id"])
        if old:
            for kw in it["q"]:
                if kw not in old.setdefault("q", []):
                    old["q"].append(kw)
            continue
        it["got"] = now
        new_items.append(it)
        by_id[it["id"]] = it

    # 포털 경유 기사 → 실제 언론사 확인 (새 기사 우선, 남는 한도로 예전 기사)
    # (카피라이트 확인 방식 추가 전에 '언론사 미확인'이 된 기사도 한 번 더 확인)
    pending = [x for x in new_items + list(by_id.values())
               if (x.get("portal") or portal_of(x.get("sourceUrl")))
               and (not x.get("resolved") or (x["source"] == "언론사 미확인" and not x.get("recheck")))]
    tried, resolved = set(), 0
    for x in pending:
        if x["id"] in tried or len(tried) >= RESOLVE_LIMIT:
            continue
        tried.add(x["id"])
        x["portal"] = x.get("portal") or portal_of(x.get("sourceUrl"))
        if resolve_portal(x):
            resolved += 1
    if pending:
        left = len({x["id"] for x in pending}) - resolved
        print(f"포털 경유 기사 언론사 확인 {resolved}건 (남은 {left}건은 다음 실행에서)")

    # 언론사 이름 정리: 도메인으로 온 이름 → 같은 사이트의 정식 이름 / config 의 sourceAlias
    names = {}
    for x in by_id.values():
        if not x.get("portal") and not re.search(r"[./]", x["source"]):
            names.setdefault(host_of(x.get("sourceUrl")), x["source"])
    alias = cfg.get("sourceAlias") or {}
    for x in by_id.values():
        if not x.get("portal") and re.search(r"[./]", x["source"]):
            x["source"] = names.get(host_of(x.get("sourceUrl"))) or host_of(x.get("sourceUrl")) or x["source"]
        x["source"] = alias.get(x["source"], x["source"])

    # 같은 언론사 + 같은 제목 중복 정리 (원문 우선, 먼저 수집된 것 우선)
    # 새 기사는 버리고, 기존 기사는 지우지 않고 dup 표시만 (메모가 달려 있을 수 있으므로 → 화면에서 숨김)
    new_ids = {x["id"] for x in new_items}
    keep, keys = {}, set()
    for x in sorted(by_id.values(), key=lambda x: (bool(x.get("portal")), x.get("got", ""))):
        key = (norm(x["title"]), x["source"])
        is_dup = key in keys and x["source"] != "언론사 미확인"
        if is_dup and x["id"] in new_ids:
            continue
        if is_dup:
            x["dup"] = True
        else:
            x.pop("dup", None)
            keys.add(key)
        keep[x["id"]] = x
    added = sum(1 for x in new_items if x["id"] in keep)

    # 자동 태그는 매번 전체 기사에 다시 적용 → 규칙을 고치면 과거 기사에도 반영됨
    rules = cfg.get("autoTags") or {}
    for x in keep.values():
        tags = auto_tags(x["title"], rules)
        if tags:
            x["auto"] = tags
        else:
            x.pop("auto", None)

    shards = {}
    for x in keep.values():
        shards.setdefault(x["pub"][:7], []).append(x)

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


def parse_items(root, keyword):
    items = []
    for it in root.iter("item"):
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        source_url = src_el.get("url", "") if src_el is not None else ""
        guid = (it.findtext("guid") or it.findtext("link") or "").strip()
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        item = {
            "id": hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16],
            "title": clean_title(it.findtext("title"), source),
            "source": source,
            "sourceUrl": source_url,
            "link": (it.findtext("link") or "").strip(),
            "pub": pub.isoformat(timespec="minutes"),
            "q": [keyword],
        }
        portal = portal_of(source_url)
        if portal:
            item["portal"] = portal
        items.append(item)
    return items


if __name__ == "__main__":
    main()
