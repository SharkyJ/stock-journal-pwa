"""종목 이벤트(실적 발표) 수집 (결정적).

보유 종목(ETF·레버리지 상품 제외)의 다음 실적 발표일과 최근 실적 결과를 모은다.
- 미국: Nasdaq 공개 API(무키) earnings-date(다음 발표일·컨센서스 EPS), earnings-surprise(최근 분기 결과).
  다음 발표일은 Worker /fund 의 next_earnings 와 대조한다(다르면 status "mismatch").
- 한국: Worker /fund 의 next_earnings 만 쓴다(결과 출처 없음).
수집 로직은 bluedbs/invest-trading-bot src/earnings.js 를 참고했다. 실패해도 리포트를 막지 않는다(fail-open).

사용: python3 collect_events.py --input work/input.json --market work/market.json --out work/events.json
종료 코드: 항상 0
"""
import argparse
import json
import re
import time
from datetime import date

import common as C

NASDAQ = "https://api.nasdaq.com/api"
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
ETF_NAME = re.compile(r"KODEX|TIGER|SOL |ACE |RISE |KBSTAR|HANARO|ARIRANG|KOSEF|PLUS |ETF|ETN|레버리지|인버스|선물", re.I)
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def is_etf(h, item, cfg):
    if h["ticker"] in cfg["underlying"]:
        return True
    if str((item or {}).get("qtype", "")).upper() in ("ETF", "MUTUALFUND", "ETN"):
        return True
    return bool(ETF_NAME.search(h["name"]))


def mdy(s):
    """'12/16/2026' 또는 'Dec 16, 2026' → 'YYYY-MM-DD'."""
    s = str(s or "").strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", s)
    if m and m.group(1).lower() in MON:
        return f"{m.group(3)}-{MON[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def num(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_earnings_date(d):
    """Nasdaq earnings-date 응답 → {date, time, confirm, fiscal, consensus_eps, prev_eps} 또는 None."""
    data = (d or {}).get("data") or {}
    rep = re.sub(r"\s+", " ", str(data.get("reportText") or ""))
    ann = str(data.get("announcement") or "")
    m = re.search(r":\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*$", ann)
    dt = mdy(m.group(1)) if m else None
    if not dt:
        m = re.search(r"report earnings on\s+(\d{1,2}/\d{1,2}/\d{4})", rep)
        dt = mdy(m.group(1)) if m else None
    if not dt:
        return None
    tm = "장 마감 후" if re.search(r"after market close", rep, re.I) else \
        "장 시작 전" if re.search(r"before market open", rep, re.I) else ""
    out = {"date": dt, "time": tm, "confirm": "예상" if re.search(r"estimated|algorithm", rep, re.I) else "예정"}
    m = re.search(r"fiscal Quarter ending ([A-Za-z]{3} \d{4})", rep)
    if m:
        out["fiscal"] = m.group(1)
    m = re.search(r"consensus EPS forecast for the quarter is \$?(-?[\d.]+)", rep)
    if m:
        out["consensus_eps"] = float(m.group(1).rstrip("."))
    m = re.search(r"reported EPS for the same quarter last year was \$?(-?[\d.]+)", rep)
    if m:
        out["prev_eps"] = float(m.group(1).rstrip("."))
    return out


def parse_surprise(d, limit=4):
    """Nasdaq earnings-surprise 응답 → 최근 분기 결과 목록."""
    rows = (((d or {}).get("data") or {}).get("earningsSurpriseTable") or {}).get("rows") or []
    out = []
    for r in rows[:limit]:
        dt = mdy(r.get("dateReported"))
        if not dt:
            continue
        out.append({"date": dt, "fiscal": r.get("fiscalQtrEnd") or "", "actual_eps": num(r.get("eps")),
                    "consensus_eps": num(r.get("consensusForecast")), "surprise_pct": num(r.get("percentageSurprise"))})
    return out


def collect(inp, market, cfg, fetch=None):
    ec = cfg.get("events") or {}
    out = {"date": inp["date"], "status": "ok", "items": {}, "order": [], "skipped": [], "errors": []}
    if not ec.get("enabled", True):
        out["status"] = "disabled"
        return out
    fetch = fetch or (lambda u: C.http_get(u, cfg, headers=NASDAQ_HEADERS))
    mi = {t["ticker"]: market["items"].get(t["id"]) for t in market.get("tickers", [])}
    today = date.fromisoformat(inp["date"])
    n = 0

    def add(o):
        nonlocal n
        n += 1
        o["id"] = f"E{n}"
        o["days"] = (date.fromisoformat(o["date"]) - today).days
        out["items"][o["id"]] = o
        out["order"].append(o["id"])

    for h in inp["holdings"]:
        it = mi.get(h["ticker"]) or {}
        if is_etf(h, it, cfg):
            out["skipped"].append(h["ticker"])
            continue
        base = {"kind": "earnings", "ticker": h["ticker"], "name": h["name"]}
        wk = it.get("next_earnings")
        if h["market"] != "US" or not ec.get("nasdaq", True):
            if wk:
                add({**base, "date": wk, "status": "예정", "confirm": "예상", "time": "", "src": "Worker /fund",
                     "check": "단일 출처", "url": ""})
            continue
        sym = C.ysym(h["ticker"], "US")
        page = f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/earnings"
        nx = None
        try:
            nx = parse_earnings_date(json.loads(fetch(f"{NASDAQ}/analyst/{sym}/earnings-date")))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{sym} earnings-date: {e}")
        time.sleep(cfg["http"]["delay_sec"])
        try:
            res = parse_surprise(json.loads(fetch(f"{NASDAQ}/company/{sym}/earnings-surprise")), ec.get("results", 4))
        except Exception as e:  # noqa: BLE001
            res = []
            out["errors"].append(f"{sym} earnings-surprise: {e}")
        time.sleep(cfg["http"]["delay_sec"])
        for r in res:
            add({**base, **r, "status": "발표", "confirm": "확정", "time": "", "src": "Nasdaq", "url": page,
                 "check": ""})
        if nx and date.fromisoformat(nx["date"]) >= today:
            chk, st = "Worker 일정 없음", "ok"
            if wk:
                gap = abs((date.fromisoformat(wk) - date.fromisoformat(nx["date"])).days)
                chk = "Worker 일정 일치" if gap == 0 else f"Worker 일정 {wk} (차이 {gap}일)"
                st = "ok" if gap <= 1 else "mismatch"
            add({**base, **nx, "status": "예정", "src": "Nasdaq", "url": page, "check": chk, "xstatus": st})
        elif wk and date.fromisoformat(wk) >= today:
            add({**base, "date": wk, "status": "예정", "confirm": "예상", "time": "", "src": "Worker /fund",
                 "check": "Nasdaq 일정 없음", "url": page})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--market", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = C.load_config()
    inp, errs = C.normalize_input(C.load_json(a.input))
    errs += C.date_errors(inp)
    if errs:
        C.fail("input.json 오류: " + "; ".join(errs))
    res = collect(inp, C.load_json(a.market), cfg)
    C.save_json(a.out, res)
    print(json.dumps({"ok": True, "items": len(res["order"]), "skipped": res["skipped"], "errors": res["errors"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
