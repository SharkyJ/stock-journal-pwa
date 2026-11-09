"""S1 시세 수집 (결정적).

Worker /chart, /fund 를 호출해 지수·선물·종목 수치를 계산하고 교차 검증한다.
수치는 모두 이 스크립트가 계산하며, LLM은 결과의 ID만 인용한다.

사용: python3 collect_market.py --input work/input.json --out work/market.json
종료 코드: 0 통과(일부 항목 unavailable 포함), 2 중단(입력 오류 또는 지수 전부 실패)
"""
import argparse
import json
import time
import urllib.parse
from datetime import date

import common as C


def r2(x, nd=2):
    return None if x is None else round(float(x), nd)


def pct(a, b):
    return None if a is None or not b else (a / b - 1) * 100


def sma(c, n):
    return sum(c[-n:]) / n if len(c) >= n else None


def quote_metrics(d1, cfg, now_ts):
    """r=1d 응답 → 최신가·직전 종가·등락률·상태."""
    price, prev, at = d1.get("price"), d1.get("prev"), d1.get("at")
    out = {"value": r2(price), "prev": r2(prev), "chg_pct": r2(pct(price, prev)),
           "as_of": C.iso_kst(at), "ccy": d1.get("ccy"), "status": "ok", "checks": []}
    if price is None or prev is None:
        out["status"] = "unavailable"
        out["checks"].append("최신가 또는 직전 종가 없음")
    elif at is not None and now_ts - at > cfg["checks"]["stale_hours"] * 3600:
        out["status"] = "stale"
        out["checks"].append(f"시세 시각 {C.iso_kst(at)}")
    return out


def daily_metrics(dy, price, cfg):
    """r=1y 일봉 → 이동평균·고저점, 그리고 최신가 교차 검증."""
    c = [x for x in (dy.get("c") or []) if x is not None]
    h = [x for x in (dy.get("h") or []) if x is not None]
    lo = [x for x in (dy.get("l") or []) if x is not None]
    if len(c) < 20:
        return {}, ["일봉 부족"]
    m = {}
    for n in (20, 60, 200):
        v = sma(c, n)
        m[f"ma{n}"] = r2(v)
        m[f"vs_ma{n}_pct"] = r2(pct(price, v)) if price is not None else None
    m["hi20"], m["lo20"] = r2(max(h[-20:])), r2(min(lo[-20:]))
    m["hi52"], m["lo52"] = r2(max(h)), r2(min(lo))
    checks = []
    last = c[-1]
    if price is not None and last and abs(price / last - 1) * 100 > cfg["checks"]["mismatch_pct"]:
        checks.append(f"최신가 {price} 와 일봉 종가 {last} 차이 {abs(price / last - 1) * 100:.2f}%")
    return m, checks


def kr_revision(rows, fund, today):
    """국장 컨센서스 변화: App_재무추이 의 28~45일 전 기록 대비 (앱 finHist 와 같은 기준)."""
    if not fund or not rows:
        return {}
    t = date.fromisoformat(today)
    old = [r for r in rows if 28 <= (t - date.fromisoformat(r["date"])).days <= 45]
    if not old:
        return {}
    o = sorted(old, key=lambda r: r["date"])[-1]
    days = (t - date.fromisoformat(o["date"])).days
    m = {}
    if o.get("f0end"):
        m[o["f0end"]] = o.get("f0eps")
    if o.get("f1end"):
        m[o["f1end"]] = o.get("f1eps")
    out = {}
    for z in reversed(fund.get("fy") or []):
        base = m.get(z.get("end"))
        if z.get("eps") is not None and base and base > 0:
            out["eps_rev_pct"] = r2((z["eps"] / base - 1) * 100)
            out["eps_rev_days"] = days
            break
    if fund.get("tgt") and o.get("target"):
        out["tgt_chg_pct"] = r2((fund["tgt"] / o["target"] - 1) * 100)
        out["tgt_chg_days"] = days
    return out


def fund_metrics(f, price):
    if not f or f.get("error"):
        return {}, ["재무 자료 없음"]
    px = price or f.get("px")
    m = {"per": r2(f.get("per")), "fwd_per": r2(f.get("fper")), "eps": r2(f.get("eps")),
         "fwd_eps": r2(f.get("feps")), "target": r2(f.get("tgt")), "rec": r2(f.get("rec")),
         "ntm_eps": r2(f.get("ntmEps"))}
    if f.get("ntmEps") and f["ntmEps"] > 0 and px:
        m["ntm_per"] = r2(px / f["ntmEps"])
    if f.get("tgt") and px:
        m["tgt_upside_pct"] = r2((f["tgt"] / px - 1) * 100)
    rev = f.get("rev") or {}
    if rev.get("r30") is not None:
        m["eps_rev_pct"], m["eps_rev_days"] = r2(rev["r30"] * 100), 30
    if f.get("earn"):
        m["next_earnings"] = C.iso_kst(f["earn"])[:10]
    checks = []
    if f.get("stale"):
        checks.append("재무 자료가 캐시 값: " + str(f.get("warn", "")))
    return m, checks


class Collector:
    def __init__(self, cfg, base=None):
        self.cfg, self.base = cfg, (base or cfg["worker_base"]).rstrip("/")
        self.cache = {}

    def get(self, path, sym, r=None):
        key = (path, sym, r)
        if key not in self.cache:
            q = {"s": sym}
            if r:
                q["r"] = r
            url = f"{self.base}/{path}?{urllib.parse.urlencode(q)}"
            try:
                self.cache[key] = json.loads(C.http_get(url, self.cfg))
            except Exception as e:  # noqa: BLE001 — 항목 단위로 unavailable 처리
                self.cache[key] = {"error": str(e)}
            time.sleep(self.cfg["http"]["delay_sec"])
        return self.cache[key]

    def quote(self, sym, label, now_ts, daily=True):
        d1 = self.get("chart", sym, "1d")
        if d1.get("error"):
            return {"sym": sym, "label": label, "status": "unavailable", "checks": [d1["error"]]}
        q = quote_metrics(d1, self.cfg, now_ts)
        q.update({"sym": sym, "label": label})
        if daily and q["status"] != "unavailable":
            dy = self.get("chart", sym, "1y")
            if dy.get("error"):
                q["checks"].append("일봉 실패: " + dy["error"])
            else:
                m, ch = daily_metrics(dy, q["value"], self.cfg)
                q.update(m)
                q["checks"] += ch
                if ch and q["status"] == "ok" and any("차이" in x for x in ch):
                    q["status"] = "mismatch"
        return q


def collect(inp, cfg, base=None, now_ts=None):
    now_ts = now_ts or int(time.time())
    col = Collector(cfg, base)
    items, out = {}, {"date": inp["date"], "mode": inp["mode"], "generated_at": C.iso_kst(now_ts),
                      "indices": [], "futures": [], "tickers": []}
    n = 0

    def add(prefix, obj):
        nonlocal n
        n += 1
        iid = f"{prefix}{n}"
        obj["id"] = iid
        items[iid] = obj
        return iid

    for sym, label in cfg["indices"]:
        out["indices"].append(add("M", col.quote(sym, label, now_ts)))
    for sym, label in cfg["futures"]:
        out["futures"].append(add("M", col.quote(sym, label, now_ts, daily=False)))
    if all(items[i]["status"] == "unavailable" for i in out["indices"]):
        C.fail("지수 시세를 하나도 받지 못함 (Worker 접근 확인)")

    fin_by = {}
    for r in inp.get("fin") or []:
        fin_by.setdefault(str(r.get("ticker", "")).upper(), []).append(r)
    under_ids = {}
    for kind in ("holdings", "watch"):
        for h in inp[kind]:
            sym = C.ysym(h["ticker"], h["market"])
            q = col.quote(sym, h["name"], now_ts)
            u = cfg["underlying"].get(h["ticker"])
            if u:
                q["leverage"] = u[1]
            else:
                f = col.get("fund", sym)
                fm, fch = fund_metrics(f, q.get("value"))
                if h["market"] == "KR":
                    fm.update(kr_revision(fin_by.get(h["ticker"], []), f, inp["date"]))
                q.update({k: v for k, v in fm.items() if v is not None})
                q["checks"] += fch
            tid = add("T", q)
            entry = {"ticker": h["ticker"], "name": h["name"], "market": h["market"],
                     "kind": "보유" if kind == "holdings" else "관심", "id": tid}
            if u:
                if u[0] not in under_ids:
                    under_ids[u[0]] = add("U", col.quote(u[0], f"{h['name']} 기초자산", now_ts))
                entry["under_id"] = under_ids[u[0]]
            out["tickers"].append(entry)
    out["items"] = items
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", help="Worker 주소 (기본값 config.json)")
    a = ap.parse_args()
    cfg = C.load_config()
    inp, errs = C.normalize_input(C.load_json(a.input))
    if errs:
        C.fail("input.json 오류: " + "; ".join(errs))
    res = collect(inp, cfg, a.base)
    C.save_json(a.out, res)
    bad = {k: v["status"] for k, v in res["items"].items() if v["status"] != "ok"}
    print(json.dumps({"ok": True, "items": len(res["items"]), "not_ok": bad}, ensure_ascii=False))


if __name__ == "__main__":
    main()
