"""주요일정 시트 행 조립 (결정적).

calendar.json(경제 지표)과 events.json(보유 종목 실적)을 App_주요일정 시트 행으로 만든다.
행마다 고정 ID 와 내용 해시(버전)를 붙여, plan_write.py 가 바뀐 행만 교체·추가하게 한다.
기존 행은 지우지 않는다(보유 종목을 전량 매도해도 과거 기록이 남는다).

ID 규칙: 경제 지표 TV-<TradingView id>, 다음 실적 ER-<티커>-NEXT, 지난 실적 결과 ER-<티커>-<발표일>.

사용: python3 build_events.py --calendar work/calendar.json --events work/events.json --out work/events_rows.json
종료 코드: 0 (쓸 행이 없어도 0), 2 입력 파일 오류
"""
import argparse
import hashlib
import json

import collect_calendar as K
import common as C
import sheetlog as L

IMP = {1: "높음", 0: "보통"}


def fnum(v):
    if v is None:
        return ""
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def txt(v):
    """시트가 '4.2%' 같은 값을 숫자(0.042)로 바꾸지 않도록 텍스트로 고정한다."""
    s = "" if v is None else str(v)
    return "'" + s if s.endswith("%") else s


def econ_row(o):
    # 지표 설명(comment)은 시트에 넣지 않는다(쓰기 토큰 절감). 앱 팝업은 출처 링크로 대신한다.
    sub = " · ".join(x for x in [o.get("period") and f"기간 {o['period']}", o.get("reason")] if x)
    return {
        "ID": f"TV-{o['tv_id']}", "구분": "경제", "일시": o["at"][:16].replace("T", " "),
        "대상": K.COUNTRY_KO.get(o["country"], o["country"]), "종목명": "",
        "제목": (f"{o['title_ko']} ({o['title']})" if o.get("title_ko") else o["title"]),
        "중요도": "높음" if o["high"] else IMP.get(o["importance"], "낮음"),
        "상태": "발표" if o["released"] else "예정", "예상": txt(o.get("forecast")), "이전": txt(o.get("previous")),
        "실제": txt(o.get("actual")), "판정": o["label"] if o["released"] else "", "점수": o["score"] if o["released"] else "",
        "세부": sub[:600], "출처": o.get("source_url") or "https://www.tradingview.com/economic-calendar/",
    }


def earn_row(o):
    res = o["status"] == "발표"
    sp = o.get("surprise_pct")
    verdict = ""
    if res and sp is not None:
        verdict = "예상 상회" if sp > 0 else "예상 하회" if sp < 0 else "예상 부합"
    sub = " · ".join(x for x in [
        o.get("fiscal") and f"{o['fiscal']} 분기", o.get("time"),
        o.get("prev_eps") is not None and f"전년 동기 EPS {fnum(o['prev_eps'])}",
        o.get("check"), f"출처 {o.get('src', '')}"] if x)
    return {
        "ID": f"ER-{o['ticker']}-{o['date'] if res else 'NEXT'}", "구분": "실적", "일시": o["date"],
        "대상": o["ticker"], "종목명": o["name"], "제목": f"{o['name']} 실적 발표",
        "중요도": "높음", "상태": "발표" if res else ("예정" if o.get("confirm") == "예정" else "예상"),
        "예상": fnum(o.get("consensus_eps")), "이전": fnum(o.get("prev_eps")) if not res else "",
        "실제": fnum(o.get("actual_eps")) if res else "", "판정": verdict,
        "점수": sp if sp is not None else "", "세부": sub, "출처": o.get("url") or "",
    }


def build(cal, events, cfg, now=None, sheet=None):
    sh = cfg["sheets"]["events"]
    head = sh["header"]
    ts = (now or C.now_kst()).strftime("%Y-%m-%d %H:%M")
    recs = []
    if cal and cal.get("status") == "ok":
        recs += [econ_row(cal["items"][i]) for i in cal["order"]]
    if events and events.get("status") == "ok":
        recs += [earn_row(events["items"][i]) for i in events["order"]]
    rows, seen = [], set()
    for r in recs:
        if r["ID"] in seen:
            continue
        seen.add(r["ID"])
        body = [r.get(h, "") for h in head if h not in ("버전", "갱신시각")]
        ver = "v" + hashlib.sha1(json.dumps(body, ensure_ascii=False).encode()).hexdigest()[:10]
        r["버전"], r["갱신시각"] = ver, ts
        rows.append([r.get(h, "") for h in head])
    return {"kind": "events", "sheet": sheet or sh["name"], "header": head, "written_at": ts, "rows": rows,
            "sources": {"calendar": (cal or {}).get("status"), "events": (events or {}).get("status")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet", help="기록할 시트 이름 (기본 config sheets.events.name)")
    a = ap.parse_args()
    try:
        cal, ev = C.load_json(a.calendar), C.load_json(a.events)
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"입력 파일을 읽지 못함: {e}")
    res = build(cal, ev, C.load_config(), sheet=a.sheet)
    C.save_json(a.out, res)
    L.guard_add(res["sheet"])  # 7단계 시트 쓰기 보호(훅)를 켠다
    print(json.dumps({"ok": True, "sheet": res["sheet"], "rows": len(res["rows"]), "sources": res["sources"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
