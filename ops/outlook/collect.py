"""S1+S2 수집과 요약 출력 (결정적).

collect_market, collect_news, collect_calendar, collect_events 를 한 번에 실행해
market.json, news.json, calendar.json, events.json 을 만들고,
해석(S4)에 필요한 값만 한 줄씩 요약해 출력한다. LLM 은 이 요약만 읽고 JSON 원본은 열지 않는다.

사용: python3 collect.py --input work/input.json --work work
종료 코드: 0 통과, 2 중단(입력 오류, 지수 전부 실패, 뉴스 전부 실패)
"""
import argparse
import json
from datetime import datetime, timedelta

import collect_calendar as K
import collect_events as E
import collect_market as M
import collect_news as N
import common as C


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    return f"{int(v):,}"


def pct(v):
    return "-" if v is None else f"{v:+.2f}%"


def quote_line(o):
    s = f"{o['id']} {o.get('label', '')} {fmt(o.get('value'))} {pct(o.get('chg_pct'))}"
    if o.get("as_of"):
        s += f" ({o['as_of'][5:16].replace('T', ' ')})"
    if o.get("status") != "ok":
        s += f" [{o.get('status')}]"
    return s


def ticker_line(t, o):
    parts = [f"{o['id']} {t['ticker']} {t['name']} [{t['kind']}] {fmt(o.get('value'))} {pct(o.get('chg_pct'))}"]
    if o.get("status") != "ok":
        parts.append(f"[{o.get('status')}]")
    ma = " ".join(f"{n}일선 {pct(o.get(f'vs_ma{n}_pct'))}" for n in (20, 60, 200) if o.get(f"vs_ma{n}_pct") is not None)
    if ma:
        parts.append(ma)
    if o.get("lo20") is not None:
        parts.append(f"20일 {fmt(o.get('lo20'))}~{fmt(o.get('hi20'))}")
    f = []
    for k, lab in (("per", "PER"), ("ntm_per", "선행PER"), ("fwd_per", "포워드PER")):
        if o.get(k) is not None:
            f.append(f"{lab} {fmt(o[k])}")
    if o.get("target") is not None:
        f.append(f"목표가 {fmt(o['target'])}({pct(o.get('tgt_upside_pct'))})")
    if o.get("eps_rev_pct") is not None:
        f.append(f"EPS추정 {o.get('eps_rev_days')}일 {pct(o['eps_rev_pct'])}")
    if o.get("tgt_chg_pct") is not None:
        f.append(f"목표가 {o.get('tgt_chg_days')}일 {pct(o['tgt_chg_pct'])}")
    if o.get("rec") is not None:
        f.append(f"의견 {fmt(o['rec'])}")
    if o.get("next_earnings"):
        f.append(f"다음실적 {o['next_earnings']}")
    if f:
        parts.append(" · ".join(f))
    if o.get("leverage"):
        parts.append(f"레버리지 {o['leverage']}배 → 기초자산 {t.get('under_id')}")
    if o.get("checks"):
        parts.append("주의: " + "; ".join(o["checks"]))
    return " | ".join(parts)


def hm(at):
    return at[5:16].replace("T", " ") if at else "-"


def cal_line(o):
    s = f"{o['id']} {hm(o['at'])} {K.COUNTRY_KO.get(o['country'], o['country'])} {o['title_ko'] or o['title']}"
    if o["title_ko"]:
        s += f" ({o['title']})"
    if o["high"]:
        s += " [고영향]"
    if o["released"]:
        s += f" | 발표 {o['actual']}" + (f" vs 예상 {o['forecast']}" if o["forecast"] else "") + \
             (f" (이전 {o['previous']})" if o["previous"] else "") + f" → {o['label']}"
    else:
        s += " | 예정" + (f" · 예상 {o['forecast']}" if o["forecast"] else "") + (f" · 이전 {o['previous']}" if o["previous"] else "")
    return s


def ev_line(o):
    d = f"D{o['days']:+d}" if o["days"] else "D-day"
    if o["status"] == "발표":
        s = f"{o['id']} {o['ticker']} {o['name']} 실적 발표 {o['date']} ({o.get('fiscal', '')})"
        if o.get("actual_eps") is not None:
            s += f" EPS {fmt(o['actual_eps'])}"
        if o.get("consensus_eps") is not None:
            s += f" vs 컨센서스 {fmt(o['consensus_eps'])}"
        if o.get("surprise_pct") is not None:
            s += f" ({pct(o['surprise_pct'])})"
        return s
    s = f"{o['id']} {o['ticker']} {o['name']} 다음 실적 {o['date']} {o.get('time', '')} ({d}, {o['confirm']})".replace("  ", " ")
    if o.get("fiscal"):
        s += f" · {o['fiscal']} 분기"
    if o.get("consensus_eps") is not None:
        s += f" · 컨센서스 EPS {fmt(o['consensus_eps'])}"
    if o.get("prev_eps") is not None:
        s += f" · 전년 동기 {fmt(o['prev_eps'])}"
    if o.get("check"):
        s += f" · {o['check']}"
    if o.get("xstatus") == "mismatch":
        s += " [mismatch]"
    return s


def cal_pick(inp, cal, now):
    """요약에 보일 경제 일정: 일간은 지난 2일 발표·앞으로 2일 예정, 주간은 지난 7일·앞으로 7일."""
    back, ahead = (2, 2) if inp["mode"] == "daily" else (7, 7)
    lo, hi = now - timedelta(days=back), now + timedelta(days=ahead)
    got = []
    for i in cal["order"]:
        o = cal["items"][i]
        at = datetime.fromisoformat(o["at"])
        if lo <= at <= hi and (o["released"] or at >= now):
            got.append(o)
    got.sort(key=lambda o: (not o["high"], o["at"]))
    lim = cal.get("digest_max", 14)
    return sorted(got[:lim], key=lambda o: o["at"]), lim


def key_signals(inp, cal, events, news, now):
    """규칙으로 뽑은 핵심 신호(3~6줄). 해석 우선순위를 잡는 용도."""
    out = []
    win = 36 if inp["mode"] == "daily" else 24 * 7
    slots = {}
    for i in cal["order"]:
        o = cal["items"][i]
        at = datetime.fromisoformat(o["at"])
        if o["released"] and o["strength"] in ("강한", "보통") and now - at <= timedelta(hours=win):
            out.append(f"발표 {o['label']}: {o['title_ko'] or o['title']} ({o['id']})")
        elif not o["released"] and o["high"] and timedelta(0) <= at - now <= timedelta(hours=win):
            slots.setdefault((o["at"], o["country"]), []).append(o)
    for (at, ctry), lst in slots.items():  # 같은 시각 같은 나라 지표는 한 줄로 묶는다
        names = ", ".join(x["title_ko"] or x["title"] for x in lst[:4])
        out.append(f"예정 고영향: {hm(at)} {K.COUNTRY_KO.get(ctry, ctry)} {names} ({', '.join(x['id'] for x in lst[:4])})")
    near = (events.get("near_days") or 3) if inp["mode"] == "daily" else 7
    for i in events["order"]:
        o = events["items"][i]
        if o["status"] == "예정" and 0 <= o["days"] <= near:
            out.append(f"실적 임박: {o['name']} {o['date']} {o.get('time', '')} ({o['id']})".replace("  ", " "))
    bad = {}
    for nn in news["items"]:
        if nn.get("hint") == "악재" and nn["scope"] not in ("market", "fed"):
            bad.setdefault(nn["scope"], []).append(nn["id"])
    for k, v in bad.items():
        if len(v) >= 2:
            out.append(f"악재 후보 기사 {len(v)}건: {k} ({', '.join(v[:3])})")
    fed = [nn["id"] for nn in news["items"] if nn["scope"] == "fed"]
    if fed:
        out.append(f"연준 보도자료 {len(fed)}건 ({', '.join(fed)})")
    return out[:8]


def digest(inp, market, news, cal=None, events=None, now=None):
    now = now or C.now_kst()
    cal = cal or {"order": [], "items": {}, "tone": None, "status": "disabled"}
    events = events or {"order": [], "items": {}, "status": "disabled"}
    it = market["items"]
    out = [f"# {inp['date']} {inp['mode']} 요약 (ID로 인용)"]
    ks = key_signals(inp, cal, events, news, now)
    if ks:
        out += ["## 오늘의 핵심 신호 (규칙 추출)"] + [f"- {x}" for x in ks]
    out.append("## 지수·환율·금리")
    out += [quote_line(it[i]) for i in market["indices"]]
    out += ["## 미국 지수선물"] + [quote_line(it[i]) for i in market["futures"]]
    out.append("## 경제 캘린더 (TradingView, 미국·한국, KST)")
    if cal.get("status") != "ok":
        out.append(f"경제 캘린더 {cal.get('status')}: 일정은 4단계 웹 검색으로 보완")
    else:
        if cal.get("tone"):
            tn = cal["tone"]
            out.append(f"거시 기조: {tn['tone']} ({tn['label']})" + (f" · {', '.join(tn['drivers'])}" if tn.get("drivers") else ""))
        picked, _ = cal_pick(inp, cal, now)
        out += [cal_line(o) for o in picked] or ["해당 기간 주요 지표 없음"]
    out.append("## 보유 종목 실적 일정 (ETF 제외)")
    if events.get("status") != "ok":
        out.append(f"종목 이벤트 {events.get('status')}")
    else:
        last = {}
        for i in events["order"]:
            o = events["items"][i]
            if o["status"] == "발표":
                last.setdefault(o["ticker"], o)
        lines = [ev_line(events["items"][i]) for i in events["order"] if events["items"][i]["status"] == "예정"]
        lines += [ev_line(o) for o in last.values()]
        out += lines or ["수집된 실적 일정 없음"]
    out.append("## 종목")
    shown_u = set()
    for t in market["tickers"]:
        out.append(ticker_line(t, it[t["id"]]))
        u = t.get("under_id")
        if u and u not in shown_u:
            shown_u.add(u)
            out.append("  " + ticker_line({"ticker": it[u]["sym"], "name": it[u].get("label", ""), "kind": "기초자산"}, it[u]))
    if inp.get("signals"):
        out.append("## 앱 신호 (input)")
        out += [f"{s.get('date')} {s.get('ticker')} {s.get('strategy', '')} {s.get('signal', '')}" for s in inp["signals"]]
    out.append("## 뉴스 (태그: 키워드 기반 후보, 최종 판단은 제목으로)")
    for n in news["items"]:
        tag = " · ".join(([n["hint"] + "후보"] if n.get("hint") and n["hint"] != "중립" else []) + (n.get("tags") or []))
        out.append(f"{n['id']} [{n['scope']}] {n['published'][5:16].replace('T', ' ')} {n['source']} | {n['title']}"
                   + (f" {{{tag}}}" if tag else ""))
    if news.get("errors"):
        out.append("뉴스 오류: " + "; ".join(news["errors"]))
    for x in (cal.get("errors") or []) + (events.get("errors") or []):
        out.append("수집 오류: " + x)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True, help="market.json, news.json 을 쓸 폴더")
    ap.add_argument("--base", help="Worker 주소 (기본값 config.json)")
    a = ap.parse_args()
    cfg = C.load_config()
    inp, errs = C.normalize_input(C.load_json(a.input))
    if errs:
        C.fail("input.json 오류: " + "; ".join(errs))
    market = M.collect(inp, cfg, a.base)
    C.save_json(f"{a.work}/market.json", market)
    news = N.collect(inp, cfg)
    C.save_json(f"{a.work}/news.json", news)
    if not news["items"] and news["errors"]:
        C.fail("뉴스 RSS 를 하나도 받지 못함: " + news["errors"][0])
    cal = K.collect(inp, cfg)
    C.save_json(f"{a.work}/calendar.json", cal)
    events = E.collect(inp, market, cfg)
    events["near_days"] = (cfg.get("events") or {}).get("near_days", 3)
    C.save_json(f"{a.work}/events.json", events)
    print(digest(inp, market, news, cal, events))
    bad = {k: v["status"] for k, v in market["items"].items() if v["status"] != "ok"}
    print(json.dumps({"ok": True, "items": len(market["items"]), "news": len(news["items"]), "calendar": cal["status"],
                      "events": len(events["order"]), "not_ok": bad},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
