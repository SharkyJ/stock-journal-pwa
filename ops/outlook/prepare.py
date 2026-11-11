"""1단계 입력 준비 (결정적).

  begin : 작업 폴더의 이전 실행 기록(sheet_log·guard·plan)을 지우고 '입력 수집' 보호를 켠다.
          이 상태에서 시트 읽기 응답은 훅이 원문을 저장하고 세션에는 행 수만 보여준다. 쓰기는 모두 막힌다.
          출력: 이번에 읽을 범위 목록.
  input : 훅 기록의 읽기 응답으로 input.json 을 만든다(보유 수량 > 0 필터, 신호 기간, 최신 주간 전망,
          재무추이 45일 등). 날짜는 오늘(Asia/Seoul, OUTLOOK_DATE 로 지정 가능).

사용:
  python3 prepare.py begin --mode daily|weekly
  python3 prepare.py input --mode daily|weekly --out work/input.json
종료 코드: 0 통과, 1 읽기 누락(need 출력), 2 중단
"""
import argparse
import json
import os
import re
import time
from datetime import date, timedelta

import common as C
import sheetlog as L

READS = {
    "holdings_kr": "'국장투자'!A5:D",
    "holdings_us": "'미장투자'!A5:D",
    "watch": "'App_관심종목'!A2:C",
    "signals": "'App_시그널'!B2:H",
    "weekly": "'App_전망'!A2:G",
    "fin_a": "'App_재무추이'!B2:D",
    "fin_b": "'App_재무추이'!L2:P",
}
OPTIONAL = {"watch", "signals", "weekly", "fin_a", "fin_b"}  # 시트가 없으면 빈 값


def reads_for(mode):
    return {k: v for k, v in READS.items() if not (mode == "weekly" and k == "weekly")}


def begin(mode):
    L.WORK.mkdir(parents=True, exist_ok=True)
    for p in [L.LOG, L.GUARD] + list(L.WORK.glob("*plan.json")):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    L.GUARD.write_text(json.dumps({"started": time.time(), "sheets": [], "phase": "input"}), encoding="utf-8")
    return list(reads_for(mode).values())


def find(log, rng):
    """해당 범위의 가장 최근 get_values 응답. 오류 응답(시트 없음)은 {'missing': True}."""
    for r in reversed(log):
        if r.get("phase") != "post" or r.get("tool") != "get_values":
            continue
        rr = (r.get("input") or {}).get("range", "")
        if L.sheet_of(rr) == L.sheet_of(rng) and L.range_spec(rr) == L.range_spec(rng):
            resp = r.get("response") or {}
            if "values" in resp or "range" in resp:
                return resp.get("values") or []
            return {"missing": True, "raw": str(resp)[:200]}
    return None


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("₩", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def iso(v):
    m = re.search(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", str(v or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def cell(row, i):
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def weekdays_back(today, n):
    """오늘 포함 최근 n 거래일(평일)의 가장 이른 날짜."""
    d, k = today, 0
    while True:
        if d.weekday() < 5:
            k += 1
            if k == n:
                return d
        d -= timedelta(days=1)


def build(mode, vals, today):
    holdings, watch = [], []
    for k in ("holdings_kr", "holdings_us"):
        for r in vals.get(k) or []:
            t, q = cell(r, 2).upper(), num(cell(r, 3))
            if t and ":" in t and q and q > 0:
                holdings.append({"ticker": t, "name": cell(r, 1) or t})
    for r in vals.get("watch") or []:
        t = cell(r, 0).upper()
        if t and ":" in t:
            watch.append({"ticker": t, "name": cell(r, 1) or t})
    tick = {h["ticker"] for h in holdings + watch}
    lo = weekdays_back(today, 3) if mode == "daily" else today - timedelta(days=14)
    signals = []
    for r in vals.get("signals") or []:
        d, t = iso(cell(r, 0)), cell(r, 2).upper()
        if d and t in tick and lo.isoformat() <= d <= today.isoformat():
            signals.append({"date": d, "ticker": t, "strategy": cell(r, 5), "signal": cell(r, 6)})
    signals.sort(key=lambda s: (s["date"], s["ticker"]), reverse=True)
    weekly = []
    if mode == "daily":
        rows = [(iso(cell(r, 0)), r) for r in vals.get("weekly") or []]
        last = max((d for d, _ in rows if d), default=None)
        for d, r in rows:
            if d and d == last:
                weekly.append({"ticker": cell(r, 2).upper(), "name": cell(r, 3), "s": cell(r, 4), "m": cell(r, 5),
                               "l": cell(r, 6)})
    fin = []
    kr = {t for t in tick if t.startswith("KRX:")}
    cut = (today - timedelta(days=45)).isoformat()
    for a, b in zip(vals.get("fin_a") or [], vals.get("fin_b") or []):
        d, t = iso(cell(a, 0)), cell(a, 2).upper()
        if d and t in kr and d >= cut:
            fin.append({"date": d, "ticker": t, "f0end": iso(cell(b, 0)) or "", "f0eps": num(cell(b, 1)),
                        "f1end": iso(cell(b, 2)) or "", "f1eps": num(cell(b, 3)), "target": num(cell(b, 4))})
    return {"date": today.isoformat(), "mode": mode, "holdings": holdings, "watch": watch, "signals": signals,
            "weekly": weekly, "fin": fin}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["begin", "input"])
    ap.add_argument("--mode", required=True, choices=["daily", "weekly"])
    ap.add_argument("--out", default="work/input.json")
    ap.add_argument("--missing", nargs="*", default=[], help="읽기 오류(시트 없음)가 난 범위")
    a = ap.parse_args()
    if a.cmd == "begin":
        print(json.dumps({"ok": True, "read_in_one_turn": begin(a.mode)}, ensure_ascii=False))
        return
    log = L.log_read()
    vals, need = {}, []
    for k, rng in reads_for(a.mode).items():
        v = {"missing": True, "raw": "읽기 오류"} if rng in a.missing else find(log, rng)
        if v is None:
            need.append(rng)
        elif isinstance(v, dict):
            if k not in OPTIONAL:
                C.fail(f"{rng} 를 읽지 못함: {v['raw']}", C.ABORT)
            vals[k] = []
        else:
            vals[k] = v
    if need:
        print(json.dumps({"ok": False, "need": need}, ensure_ascii=False))
        raise SystemExit(C.RETRY)
    today = date.fromisoformat(os.environ.get("OUTLOOK_DATE") or C.now_kst().date().isoformat())
    inp = build(a.mode, vals, today)
    if not inp["holdings"]:
        C.fail("보유 종목이 0개다. 시트 응답을 확인한다", C.ABORT)
    C.save_json(a.out, inp)
    print(json.dumps({"ok": True, "date": inp["date"], "holdings": len(inp["holdings"]), "watch": len(inp["watch"]),
                      "signals": len(inp["signals"]), "weekly": len(inp["weekly"]), "fin": len(inp["fin"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
