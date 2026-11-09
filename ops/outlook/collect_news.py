"""S2 뉴스 수집 (준결정적).

Google News RSS 에서 시장·종목 기사를 모아 중복을 없애고 ID(N1..)를 붙인다.
LLM 은 기사 ID 만 인용하며, URL 은 이 파일의 결과에서 코드가 채운다.
Google News RSS 는 개인·비상업 용도로만 쓸 수 있다(피드 저작권 고지).

사용: python3 collect_news.py --input work/input.json --out work/news.json
종료 코드: 0 통과(일부 쿼리 실패 포함), 2 중단(입력 오류 또는 모든 쿼리 실패)
"""
import argparse
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta
from email.utils import parsedate_to_datetime

import common as C

LANG = {"ko": ("ko", "KR", "KR:ko"), "en": ("en-US", "US", "US:en")}


def rss_url(q, lang, hours):
    hl, gl, ceid = LANG[lang]
    when = f"{hours}h" if hours <= 48 else f"{max(1, hours // 24)}d"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:{when}", "hl": hl, "gl": gl, "ceid": ceid})


def norm_title(t):
    return re.sub(r"[\W_]+", "", t).lower()


def parse_rss(xml_bytes):
    """RSS → [{title, source, url, published}] (published 는 KST ISO)."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        src = (src_el.text or "").strip() if src_el is not None else ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        pub = it.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub).astimezone(C.KST) if pub else None
        except (TypeError, ValueError):
            dt = None
        url = (it.findtext("link") or "").strip()
        if title and url.startswith("http"):
            out.append({"title": title, "source": src, "url": url, "published": dt})
    return out


def pick(items, now, hours, limit, seen_urls, seen_titles):
    """기간 필터 → 최신순 → 중복 제거 → limit 건."""
    lo = now - timedelta(hours=hours)
    res = []
    for o in sorted((x for x in items if x["published"] and x["published"] >= lo),
                    key=lambda x: x["published"], reverse=True):
        nt = norm_title(o["title"])
        if o["url"] in seen_urls or nt in seen_titles:
            continue
        seen_urls.add(o["url"])
        seen_titles.add(nt)
        res.append(o)
        if len(res) >= limit:
            break
    return res


def queries(inp, cfg):
    q = [("market", x["q"], x["lang"], cfg["news"]["market_max"]) for x in cfg["news"]["market_queries"]]
    for kind in ("holdings", "watch"):
        for h in inp[kind]:
            if h["ticker"] in cfg["underlying"]:
                continue  # 레버리지 상품은 기초자산 기준으로 본다(시장 뉴스로 대체)
            code = h["ticker"].split(":", 1)[-1]
            if h["market"] == "KR":
                q.append((h["ticker"], h["name"], "ko", cfg["news"]["ticker_max"]))
            else:
                q.append((h["ticker"], f"{code} stock", "en", cfg["news"]["ticker_max"]))
    return q


def collect(inp, cfg, fetch=None, now=None):
    now = now or C.now_kst()
    hours = cfg["news"]["window_hours"][inp["mode"]]
    fetch = fetch or (lambda url: C.http_get(url, cfg, accept="application/rss+xml"))
    seen_urls, seen_titles, items, errors = set(), set(), [], []
    n = 0
    for scope, q, lang, limit in queries(inp, cfg):
        try:
            got = parse_rss(fetch(rss_url(q, lang, hours)))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{scope} '{q}': {e}")
            continue
        for o in pick(got, now, hours, limit, seen_urls, seen_titles):
            n += 1
            items.append({"id": f"N{n}", "scope": scope, "title": o["title"], "source": o["source"],
                          "url": o["url"], "published": o["published"].isoformat(timespec="minutes")})
        time.sleep(cfg["http"]["delay_sec"])
    return {"date": inp["date"], "mode": inp["mode"], "window_hours": hours,
            "generated_at": now.isoformat(timespec="minutes"), "items": items, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = C.load_config()
    inp, errs = C.normalize_input(C.load_json(a.input))
    if errs:
        C.fail("input.json 오류: " + "; ".join(errs))
    res = collect(inp, cfg)
    if not res["items"] and res["errors"]:
        C.save_json(a.out, res)
        C.fail("뉴스 RSS 를 하나도 받지 못함: " + res["errors"][0])
    C.save_json(a.out, res)
    print(json.dumps({"ok": True, "items": len(res["items"]), "errors": res["errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
