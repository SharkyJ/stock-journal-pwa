"""3~4단계 해석 초안·합치기 (결정적).

  brief : 수집 결과로 행마다 규칙 기반 전망(점수)·사실 문장·체크포인트·인용 ID 를 만들어 work/draft.json 에 저장하고,
          세션이 판단할 부분(행별 전망 점수 근거, 관련 뉴스 제목)만 짧게 출력한다.
  merge : 세션이 쓴 notes.json(행별 lead 1~2문장, 선택적 전망 조정 ±1, 뉴스 src)을 초안과 합쳐 analysis.json 을 만든다.

세션은 수치·날짜·체크포인트·출처를 쓰지 않는다. 모두 코드가 market/calendar/events/news 값으로 채운다.
따라서 모델이 달라도 사실 부분과 형식은 같고, 세션의 판단은 lead 문장과 ±1 조정으로 제한된다.

사용:
  python3 draft.py brief --work work
  python3 draft.py merge --work work [--notes work/notes.json]
notes.json 형식: {"rows": {"market": {"lead": "...", "src": ["N1"], "adjust": 0, "why": ""}, "KRX:005930": {...}}}
종료 코드: 0 통과, 1 notes 재작성 필요, 2 중단
"""
import argparse
import json
import re
from datetime import datetime, timedelta

import build_rows as B
import collect as D
from collect_calendar import COUNTRY_KO as K_COUNTRY
import common as C

LEVELS = ["약세", "약약세", "중립", "약강세", "강세"]
NEWS_PER_ROW = 4
MARKET_ORDER = {"daily": ["us", "fut", "upcoming", "recent", "rf", "kospi", "tone"],
                "weekly": ["us", "kospi", "recent", "tone", "upcoming", "rf", "fut"]}
# 요약에 남기는 서술형 사실(수치는 지표 표로 간다)
SUMMARY_KINDS = {"tone", "signal"}
# 사실 문장 순서(작성 과제·요약) (길이 한도를 넘으면 뒤에서부터 뺀다)
FACT_ORDER = {"daily": ["price", "under", "next", "signal", "cons", "last"],
              "weekly": ["price", "cons", "next", "under", "signal", "last"]}


def fmt(v):
    return D.fmt(v)


def pct(v):
    return D.pct(v)


def step(v, a, b):
    """|v| ≥ b → ±2, ≥ a → ±1."""
    if v is None:
        return 0
    s = 1 if v > 0 else -1
    return 2 * s if abs(v) >= b else s if abs(v) >= a else 0


def level(score, lo=1, hi=3):
    if score >= hi:
        return "강세"
    if score >= lo:
        return "약강세"
    if score <= -hi:
        return "약세"
    if score <= -lo:
        return "약약세"
    return "중립"


def shift(lv, d):
    i = LEVELS.index(lv) + max(-1, min(1, int(d or 0)))
    return LEVELS[max(0, min(4, i))]


def ok(o):
    return bool(o) and o.get("status") in ("ok", "stale", "mismatch") and o.get("value") is not None


def flat(o):
    """등락률이 정확히 0 이면 시세가 아직 갱신되지 않았을 가능성이 크다."""
    return bool(o) and o.get("chg_pct") is not None and abs(o["chg_pct"]) < 1e-9


def chg(o):
    return "변동 없음, 시세 미갱신 가능" if flat(o) else pct(o.get("chg_pct"))


def tag(o):
    """ok 가 아닌 값의 표시(검증 규칙: '확인 못 함' 또는 기준 시각)."""
    if not o or o.get("status") == "ok":
        return ""
    if o.get("as_of"):
        return f" ({D.hm(o['as_of'])} 기준)"
    return " (확인 못 함)"


def josa(word, a="은", b="는"):
    ch = word.strip()[-1:] if word.strip() else ""
    if "가" <= ch <= "힣":
        return a if (ord(ch) - 0xAC00) % 28 else b
    if ch.isdigit():
        return a if ch in "013678" else b
    return a if ch.upper() in "LMNR" else b


def cop(word):
    """서술격 조사: 받침 있으면 '이다', 없으면 '다'."""
    return "이다" if josa(word, "1", "") == "1" else "다"


KNOWN = {"000660.KS": "SK하이닉스", "005930.KS": "삼성전자"}


def lab(ctx, o):
    return o.get("label") or ulabel(ctx, o)


def ulabel(ctx, u):
    sym = u.get("sym")
    lab = {s: n for s, n in ctx.cfg["indices"]}.get(sym) or KNOWN.get(sym)
    if not lab:
        lab = next((t["name"] for t in ctx.market["tickers"] if (ctx.it.get(t["id"]) or {}).get("sym") == sym), None)
    return lab or sym or ""


class Ctx:
    def __init__(self, inp, market, news, cal, events, cfg, now):
        self.inp, self.market, self.news, self.cal, self.events, self.cfg, self.now = \
            inp, market, news, cal or {"items": {}, "order": []}, events or {"items": {}, "order": []}, cfg, now
        self.it = market["items"]
        self.by_sym = {o.get("sym"): k for k, o in self.it.items() if k.startswith("M")}
        self.daily = inp["mode"] == "daily"
        self.sig = {}
        for s in inp.get("signals") or []:
            self.sig.setdefault(s["ticker"], []).append(s)
        self.week = {w.get("ticker") or "": w for w in inp.get("weekly") or []}

    def m(self, sym):
        k = self.by_sym.get(sym)
        return self.it.get(k) if k else None


def news_for(ctx, scope):
    lst = [n for n in ctx.news.get("items", []) if n.get("scope") == scope]
    if scope == "market":
        lst += [n for n in ctx.news.get("items", []) if n.get("scope") == "fed"]
    return lst[:NEWS_PER_ROW if scope != "market" else NEWS_PER_ROW + 2]


def news_score(lst):
    g = sum(1 for n in lst if n.get("hint") == "호재")
    b = sum(1 for n in lst if n.get("hint") == "악재")
    return (1 if g > b else -1 if b > g else 0), g, b


def cal_today(ctx):
    """일간: 앞으로 36시간 고영향 예정, 주간: 앞으로 7일."""
    hrs = 36 if ctx.daily else 24 * 7
    out = []
    for i in ctx.cal.get("order") or []:
        o = ctx.cal["items"][i]
        at = datetime.fromisoformat(o["at"])
        if o["high"] and not o["released"] and timedelta(0) <= at - ctx.now <= timedelta(hours=hrs):
            out.append(o)
    return out


def cal_recent(ctx):
    hrs = 48 if ctx.daily else 24 * 7
    out = []
    for i in ctx.cal.get("order") or []:
        o = ctx.cal["items"][i]
        at = datetime.fromisoformat(o["at"])
        if o["high"] and o["released"] and timedelta(0) <= ctx.now - at <= timedelta(hours=hrs):
            out.append(o)
    return out


def ev_for(ctx, ticker):
    nxt = last = None
    for i in ctx.events.get("order") or []:
        o = ctx.events["items"][i]
        if o.get("ticker") != ticker:
            continue
        if o["status"] == "예정" and o.get("days", -1) >= 0 and (nxt is None or o["date"] < nxt["date"]):
            nxt = o
        if o["status"] == "발표" and (last is None or o["date"] > last["date"]):
            last = o
    return nxt, last


def md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}"


def sig_text(s):
    return f"{md(s['date'])} {s.get('strategy', '')} {s.get('signal', '')}".replace("  ", " ").strip()


def sig_dir(s):
    t = str(s.get("signal", ""))
    return 1 if "매수" in t else -1 if re.search(r"매도|경계|이탈", t) else 0


# ---------- 시장 행 ----------

def market_row(ctx):
    nums, facts, why, score = [], [], [], 0
    kinds = []

    def add(kind, text):
        kinds.append(kind)
        facts.append(text)
    es, nq = ctx.m("ES=F"), ctx.m("NQ=F")
    fut = [o for o in (es, nq) if ok(o) and o.get("chg_pct") is not None]
    if fut:
        avg = sum(o["chg_pct"] for o in fut) / len(fut)
        sc = step(avg, 0.3, 1.0)
        score += sc
        why.append(f"지수선물 평균 {avg:+.2f}% → {sc:+d}")
    sox = ctx.m("^SOX")
    if ok(sox):
        sc = step(sox.get("chg_pct"), 1.0, 3.0)
        score += sc
        why.append(f"반도체지수 {pct(sox.get('chg_pct'))} → {sc:+d}")
    vix = ctx.m("^VIX")
    if ok(vix) and vix.get("chg_pct") is not None and abs(vix["chg_pct"]) >= 8:
        sc = -1 if vix["chg_pct"] > 0 else 1
        score += sc
        why.append(f"VIX {pct(vix['chg_pct'])} → {sc:+d}")
    tone = (ctx.cal.get("tone") or {}).get("tone")
    if tone in ("호재 우위", "악재 우위"):
        sc = 1 if tone == "호재 우위" else -1
        score += sc
        why.append(f"거시 기조 {tone} → {sc:+d}")
    ns, g, b = news_score(news_for(ctx, "market"))
    if ns:
        score += ns
        why.append(f"시장 뉴스 호재후보 {g}·악재후보 {b} → {ns:+d}")

    us = [ctx.m(s) for s in ("^GSPC", "^NDX", "^SOX")]
    us = [o for o in us if ok(o)]
    if us:
        nums += [o["id"] for o in us]
        add("us", "간밤 " + ", ".join(f"{lab(ctx, o)} {chg(o)}{tag(o)}" for o in us) + "로 마감했다.")
    if fut:
        nums += [o["id"] for o in fut]
        add("fut", "미국 지수선물은 " + ", ".join(f"{lab(ctx, o)} {chg(o)}" for o in fut)
                     + f"({D.hm(fut[0].get('as_of'))} 기준)이다.")
    tnx, krw = ctx.m("^TNX"), ctx.m("KRW=X")
    rf = []
    if ok(tnx):
        nums.append(tnx["id"])
        rf.append(f"미국 10년물 금리 {fmt(tnx['value'])}%({pct(tnx.get('chg_pct'))}){tag(tnx)}")
    if ok(krw):
        nums.append(krw["id"])
        rf.append(f"원/달러 {fmt(krw['value'])}원({pct(krw.get('chg_pct'))}){tag(krw)}")
    if rf:
        add("rf", ", ".join(rf) + "이다.")
    ks = ctx.m("^KS11")
    if ok(ks):
        nums.append(ks["id"])
        s = f"코스피는 직전 거래일 {fmt(ks['value'])}({pct(ks.get('chg_pct'))}){tag(ks)}"
        if ks.get("vs_ma20_pct") is not None:
            s += f"로 20일선 대비 {pct(ks['vs_ma20_pct'])}"
        if ks.get("vs_ma200_pct") is not None:
            s += f", 200일선 대비 {pct(ks['vs_ma200_pct'])}"
        add("kospi", s + "이다.")
    rec = cal_recent(ctx)[:2]
    if rec:
        nums += [o["id"] for o in rec]
        add("recent", "최근 발표: " + ", ".join(f"{o['title_ko'] or o['title']} {o['label']}" for o in rec) + ".")
    up = cal_today(ctx)
    if up:
        nums += [o["id"] for o in up[:3]]
        when = "오늘·내일" if ctx.daily else "이번 주"
        add("upcoming", f"{when} 고영향 일정은 " + ", ".join(
            f"{md(o['at'][:10])} {o['at'][11:16]} {o['title_ko'] or o['title']}" for o in up[:3]) + "이다.")
    if tone:
        add("tone", f"거시 기조는 {tone}{cop(tone)}.")
    order = MARKET_ORDER["daily" if ctx.daily else "weekly"]
    pairs = sorted(zip(kinds, facts), key=lambda x: order.index(x[0]))
    context = [f for _, f in pairs]
    facts = [f for k, f in pairs if k in SUMMARY_KINDS]
    ck = []
    for o in up[:3]:
        s = f"{md(o['at'][:10])} {o['at'][11:16]} {o['title_ko'] or o['title']}"
        if o.get("forecast"):
            s += f" 예상 {o['forecast']}"
        ck.append(s)
    # 지표 표에 나오는 지수·선물 값은 lead 에서 인용할 수 있다
    nums += [i for i in ctx.market.get("indices", []) + ctx.market.get("futures", []) if ok(ctx.it.get(i))]
    nums += [o["id"] for o in cal_recent(ctx) + up]
    return {"key": "market", "name": "시장 전체", "score": score, "why": why, "facts": facts, "context": context,
            "table": market_table(ctx),
            "checkpoint": " · ".join(ck), "nums": list(dict.fromkeys(nums)), "news": news_for(ctx, "market"),
            "auto_src": [n["id"] for n in news_for(ctx, "market")[:2]]}


# ---------- 지표 표 (코드가 만들고 시트 '지표' 열에 JSON 으로 기록, 앱이 그대로 표로 보여준다) ----------

def _p(v):
    return "" if v is None else pct(v)


def _v(v):
    return "" if v is None else fmt(v)


def quote_cells(ctx, o, name=None):
    rng = f"{fmt(o['lo20'])}~{fmt(o['hi20'])}" if o.get("lo20") is not None and o.get("hi20") is not None else ""
    c = "미갱신" if flat(o) else _p(o.get("chg_pct"))
    if o.get("status") not in (None, "ok"):
        c += f" ({o['status']})"
    return [name or lab(ctx, o), _v(o.get("value")), c, _p(o.get("vs_ma20_pct")), _p(o.get("vs_ma60_pct")),
            _p(o.get("vs_ma200_pct")), rng]


QUOTE_COLS = ["항목", "현재가", "등락", "20일선 대비", "60일선 대비", "200일선 대비", "20일 범위"]


def market_table(ctx):
    rows = [quote_cells(ctx, ctx.it[i]) for i in ctx.market.get("indices", []) + ctx.market.get("futures", [])
            if ok(ctx.it.get(i))]
    tabs = [{"title": "지수·환율·금리·선물", "cols": QUOTE_COLS, "rows": rows}]
    ev = []
    for o in cal_recent(ctx) + cal_today(ctx):
        ev.append([f"{md(o['at'][:10])} {o['at'][11:16]}", K_COUNTRY.get(o["country"], o["country"]),
                   o["title_ko"] or o["title"], o.get("forecast") or "", o.get("previous") or "",
                   o.get("actual") or "", o["label"] if o["released"] else "예정"])
    if ev:
        tabs.append({"title": "고영향 경제 지표", "cols": ["일시", "국가", "지표", "예상", "이전", "실제", "판정"], "rows": ev})
    return tabs


def ticker_table(ctx, t, o, u, nxt, last):
    rows = []
    if ok(o):
        rows.append(quote_cells(ctx, o, t["name"]))
    if u and ok(u):
        rows.append(quote_cells(ctx, u, "기초자산 " + ulabel(ctx, u)))
    tabs = [{"title": "시세·이동평균", "cols": QUOTE_COLS, "rows": rows}] if rows else []
    f = []
    if o.get("eps_rev_pct") is not None:
        f.append([f"EPS 추정치 변화({o.get('eps_rev_days')}일)", _p(o["eps_rev_pct"])])
    if o.get("tgt_chg_pct") is not None:
        f.append([f"목표주가 변화({o.get('tgt_chg_days')}일)", _p(o["tgt_chg_pct"])])
    if o.get("target") is not None:
        f.append(["목표주가(여력)", f"{fmt(o['target'])} ({_p(o.get('tgt_upside_pct'))})"])
    for k, lb in (("per", "PER"), ("ntm_per", "12개월 선행 PER"), ("fwd_per", "포워드 PER")):
        if o.get(k) is not None:
            f.append([lb, fmt(o[k])])
    if o.get("rec") is not None:
        f.append(["투자의견(1 매수~5 매도)", fmt(o["rec"])])
    if nxt:
        s = f"{nxt['date']} {nxt.get('time') or ''} ({nxt['confirm']})".replace("  ", " ")
        if nxt.get("xstatus") == "mismatch":
            s += " · 출처 간 불일치"
        f.append(["다음 실적", s])
        if nxt.get("consensus_eps") is not None:
            f.append(["다음 실적 컨센서스 EPS", fmt(nxt["consensus_eps"])])
    elif o.get("next_earnings") and str(o["next_earnings"]) >= ctx.inp["date"]:
        f.append(["다음 실적", f"{o['next_earnings']} (예정)"])
    if last and last.get("actual_eps") is not None:
        s = f"{fmt(last['actual_eps'])}"
        if last.get("consensus_eps") is not None:
            s += f" vs 컨센서스 {fmt(last['consensus_eps'])}"
        if last.get("surprise_pct") is not None:
            s += f" ({_p(last['surprise_pct'])})"
        f.append([f"직전 실적 EPS ({last['date']})", s])
    if f:
        tabs.append({"title": "컨센서스·밸류에이션·실적", "cols": ["항목", "값"], "rows": f})
    return tabs


# ---------- 종목 행 ----------

def ticker_row(ctx, t, mkt_score):
    o = ctx.it.get(t["id"]) or {}
    u = ctx.it.get(t.get("under_id")) if t.get("under_id") else None
    base = u if ok(u) else o
    nums, facts, why, score = [], [], [], 0
    name = t["name"]
    lev = o.get("leverage")
    kinds = []

    def add(kind, text):
        kinds.append(kind)
        facts.append(text)

    sc = step(base.get("chg_pct"), 1.5, 4.0)
    if sc:
        why.append(f"{'기초자산 ' if base is u else ''}등락 {pct(base.get('chg_pct'))} → {sc:+d}")
    score += sc
    v20, v60 = base.get("vs_ma20_pct"), base.get("vs_ma60_pct")
    if v20 is not None and v60 is not None and (v20 > 0) == (v60 > 0):
        sc = 1 if v20 > 0 else -1
        score += sc
        why.append(f"20·60일선 {'위' if sc > 0 else '아래'} → {sc:+d}")
    er = o.get("eps_rev_pct")
    if er is not None and abs(er) >= ctx.cfg["consensus"]["daily_notable_pct"]:
        sc = 1 if er > 0 else -1
        score += sc
        why.append(f"EPS 추정 {pct(er)} → {sc:+d}")
    ns, g, b = news_score(news_for(ctx, t["ticker"]))
    if ns:
        score += ns
        why.append(f"뉴스 호재후보 {g}·악재후보 {b} → {ns:+d}")
    sigs = ctx.sig.get(t["ticker"]) or []
    if sigs and sig_dir(sigs[0]):
        score += sig_dir(sigs[0])
        why.append(f"앱 신호 {sig_text(sigs[0])} → {sig_dir(sigs[0]):+d}")
    ms = 1 if mkt_score >= 2 else -1 if mkt_score <= -2 else 0
    if ms:
        score += ms
        why.append(f"시장 점수 {mkt_score:+d} → {ms:+d}")

    if ok(o):
        nums.append(o["id"])
        s = f"{name}{josa(name)} {fmt(o['value'])}({chg(o)}){tag(o)}"
        ma = [f"{n}일선 대비 {pct(o.get(f'vs_ma{n}_pct'))}" for n in (20, 60, 200) if o.get(f"vs_ma{n}_pct") is not None]
        add("price", s + ("로 " + ", ".join(ma) if ma else "") + "이다.")
    if u and ok(u):
        nums.append(u["id"])
        ul = ulabel(ctx, u)
        add("under", f"기초자산 {ul}{josa(ul)} "
                     f"{chg(u)}{tag(u)}"
                     + (f", 20일선 대비 {pct(u['vs_ma20_pct'])}" if u.get("vs_ma20_pct") is not None else "") + "이다.")
    cons = []
    if er is not None and (not ctx.daily or abs(er) >= ctx.cfg["consensus"]["daily_notable_pct"]):
        cons.append(f"EPS 추정치는 {o.get('eps_rev_days')}일간 {pct(er)}")
    tc = o.get("tgt_chg_pct")
    if tc is not None and (not ctx.daily or abs(tc) >= ctx.cfg["consensus"]["daily_notable_pct"]):
        cons.append(f"목표주가는 {o.get('tgt_chg_days')}일간 {pct(tc)}")
    if o.get("target") is not None and o.get("tgt_upside_pct") is not None and not ctx.daily:
        cons.append(f"목표가 {fmt(o['target'])}는 현재가 대비 {pct(o['tgt_upside_pct'])}")
    val = next(((lab, o[k]) for k, lab in (("ntm_per", "12개월 선행 PER"), ("fwd_per", "포워드 PER")) if o.get(k) is not None), None)
    if val and (not ctx.daily or not cons):
        cons.append(f"{val[0]} {fmt(val[1])}" + (f"(PER {fmt(o['per'])})" if o.get("per") is not None else ""))
    if cons:
        add("cons", ", ".join(cons) + "이다.")
    nxt, last = ev_for(ctx, t["ticker"])
    ck = []
    if nxt:
        nums.append(nxt["id"])
        s = f"다음 실적은 {nxt['date']}({nxt['confirm']})"
        if nxt.get("consensus_eps") is not None:
            s += f", 컨센서스 EPS {fmt(nxt['consensus_eps'])}"
        s += "이다."
        if nxt.get("xstatus") == "mismatch":
            s = s[:-1] + "(출처 간 일정이 달라 확인 못 함)."
        add("next", s)
        ck.append(f"실적 {md(nxt['date'])}" + (f" {nxt['time']}" if nxt.get("time") else ""))
    elif o.get("next_earnings") and str(o["next_earnings"]) >= ctx.inp["date"]:
        add("next", f"다음 실적은 {o['next_earnings']} 예정이다.")
        ck.append(f"실적 {md(o['next_earnings'])}")
    if last and last.get("surprise_pct") is not None:
        nums.append(last["id"])
        add("last", f"직전 분기 EPS는 {fmt(last['actual_eps'])}로 컨센서스 {fmt(last['consensus_eps'])}를 "
                     f"{'상회' if last['surprise_pct'] >= 0 else '하회'}했다.")
    if sigs:
        d = sig_dir(sigs[0])
        mv = base.get("vs_ma20_pct") if base.get("vs_ma20_pct") is not None else (None if flat(base) else base.get("chg_pct"))
        if d and mv is not None:
            fit = "가격 흐름과 일치한다" if (d > 0) == (mv > 0) else "가격 흐름과 엇갈린다"
            add("signal", f"최근 앱 신호는 {fit}.")
    if ok(o) and o.get("lo20") is not None and o.get("hi20") is not None:
        ck = [f"지지 {fmt(o['lo20'])}", f"저항 {fmt(o['hi20'])}"] + \
             ([f"20일선 {fmt(o['ma20'])}"] if o.get("ma20") is not None else []) + ck
    if sigs:
        ck.append("앱 신호: " + ", ".join(sig_text(s) for s in sigs[:2]))
    if lev:
        ck.append(f"레버리지 {lev}배: 변동성 손실 위험")
    order = FACT_ORDER["daily" if ctx.daily else "weekly"]
    pairs = sorted(zip(kinds, facts), key=lambda x: order.index(x[0]))
    context = [f for _, f in pairs]
    facts = [f for k, f in pairs if k in SUMMARY_KINDS]
    nw = news_for(ctx, t["ticker"])
    return {"key": t["ticker"], "name": name, "score": score, "why": why, "facts": facts, "context": context,
            "table": ticker_table(ctx, t, o, u, nxt, last),
            "checkpoint": " · ".join(ck), "nums": list(dict.fromkeys(nums)), "news": nw,
            "auto_src": [n["id"] for n in nw[:2]], "_o": o, "_u": u}


def weekly_levels(r, ctx):
    """주간: 단기=점수, 중기=60일선·컨센서스 변화, 장기=200일선·목표가 여력·밸류에이션."""
    o, u = r.get("_o") or {}, r.get("_u")
    b = u if ok(u) else o
    s = level(r["score"])
    m = 0
    m += step(b.get("vs_ma60_pct"), 0.01, 10)
    m += 1 if (o.get("eps_rev_pct") or 0) >= 2 else -1 if (o.get("eps_rev_pct") or 0) <= -2 else 0
    m += 1 if (o.get("tgt_chg_pct") or 0) >= 2 else -1 if (o.get("tgt_chg_pct") or 0) <= -2 else 0
    lg = 0
    lg += step(b.get("vs_ma200_pct"), 0.01, 15)
    up = o.get("tgt_upside_pct")
    lg += 1 if up is not None and up >= 15 else -1 if up is not None and up <= 0 else 0
    if o.get("ntm_per") is not None and o.get("per") is not None:
        lg += 1 if o["ntm_per"] < o["per"] * 0.8 else 0
    return s, level(m, 1, 3), level(lg, 1, 3)


def brief(inp, market, news, cal, events, cfg, now=None):
    now = now or C.now_kst()
    ctx = Ctx(inp, market, news, cal, events, cfg, now)
    mr = market_row(ctx)
    rows = [mr]
    by_t = {t["ticker"]: t for t in market["tickers"]}
    for h in inp["holdings"] + inp["watch"]:
        t = by_t.get(h["ticker"]) or {"ticker": h["ticker"], "name": h["name"], "id": None}
        rows.append(ticker_row(ctx, t, mr["score"]))
    for r in rows:
        if ctx.daily:
            r["outlook"] = level(r["score"])
        else:
            if r["key"] == "market":
                ks, sp = ctx.m("^KS11") or {}, ctx.m("^GSPC") or {}
                mm = step(ks.get("vs_ma60_pct"), 0.01, 10) + step(sp.get("vs_ma60_pct"), 0.01, 10)
                ll = step(ks.get("vs_ma200_pct"), 0.01, 15) + step(sp.get("vs_ma200_pct"), 0.01, 15)
                r["s"], r["m"], r["l"] = level(r["score"]), level(mm, 1, 3), level(ll, 1, 3)
            else:
                r["s"], r["m"], r["l"] = weekly_levels(r, ctx)
        r.pop("_o", None)
        r.pop("_u", None)
        r["news"] = [{"id": n["id"], "title": n["title"], "hint": n.get("hint"), "published": n.get("published")}
                     for n in r["news"]]
    ks = D.key_signals(inp, ctx.cal, ctx.events, news, now) if cal is not None else []
    return {"date": inp["date"], "mode": inp["mode"], "key_signals": ks, "rows": rows}


def table_text(tabs):
    out = []
    for t in tabs or []:
        out.append(f"  [{t['title']}] " + " | ".join(t["cols"]))
        out += ["    " + " | ".join(str(c) for c in row) for row in t["rows"]]
    return out


def brief_text(dr):
    daily = dr["mode"] == "daily"
    out = [f"# {dr['date']} {dr['mode']} 작성 과제",
           "각 행의 lead(판단과 근거 서술)를 쓴다. 지표 표·체크포인트·출처 URL 은 코드가 채운다. lead 의 숫자는 그 행 표·뉴스에 있는 값만 쓴다.",
           "src 에는 그 행의 뉴스나 시장 뉴스(market 행) ID 를 쓴다. 전망은 규칙 점수로 정해져 있다. 뉴스 제목을 읽고 달리 볼 근거가 분명할 때만 adjust ±1 과 why 를 쓴다."]
    if dr["key_signals"]:
        out += ["## 핵심 신호"] + [f"- {x}" for x in dr["key_signals"]]
    for r in dr["rows"]:
        lv = r["outlook"] if daily else f"{r['s']}/{r['m']}/{r['l']}"
        out.append(f"## {r['key']} {r['name']} — 초안 {lv} (점수 {r['score']:+d}: {'; '.join(r['why']) or '신호 없음'})")
        out += table_text(r.get("table"))
        if r["facts"]:
            out.append("요약에 코드가 붙이는 문장: " + " ".join(r["facts"]))
        if r.get("checkpoint"):
            out.append("체크포인트: " + r["checkpoint"])
        for n in r["news"]:
            h = f" [{n['hint']}후보]" if n.get("hint") and n["hint"] != "중립" else ""
            out.append(f"  {n['id']}{h} {(n.get('published') or '')[5:16].replace('T', ' ')} {n['title']}")
        if not r["news"]:
            out.append("  관련 뉴스 없음")
    return "\n".join(out)


def merge(dr, notes, cfg):
    """초안 + notes → analysis.json. notes 오류는 errors 로 돌려준다."""
    daily = dr["mode"] == "daily"
    lim = cfg["limits"][dr["mode"]]
    nr = (notes or {}).get("rows") or {}
    errs, rows = [], []
    keys = {r["key"] for r in dr["rows"]}
    for k in nr:
        if k not in keys:
            errs.append(f"notes: 없는 행 {k}")
    market_news = {x["id"] for x in dr["rows"][0]["news"]} if dr["rows"] and dr["rows"][0]["key"] == "market" else set()
    for r in dr["rows"]:
        n = nr.get(r["key"]) or {}
        lead = str(n.get("lead") or "").strip()
        if not lead:
            errs.append(f"{r['key']}: lead 가 비어 있음")
        if lead and not re.search(r"[.!?]$", lead):
            lead += "."
        mx = lim.get("lead_sentences_max")
        if mx and B.sentences(lead) > mx:
            errs.append(f"{r['key']}: lead {B.sentences(lead)}문장 (최대 {mx}문장, 권장 {lim.get('lead_sentences_hint', '')})")
        adj = n.get("adjust") or 0
        if adj not in (-1, 0, 1):
            errs.append(f"{r['key']}: adjust 는 -1, 0, 1 중 하나")
            adj = 0
        if adj and not str(n.get("why") or "").strip():
            errs.append(f"{r['key']}: adjust 를 쓰면 why 가 필요")
        own = [i for i in (n.get("src") or []) if i]
        okids = {x["id"] for x in r["news"]} | market_news  # 시장 뉴스는 모든 행에서 인용할 수 있다
        bad = [i for i in own if i not in okids and not re.fullmatch(r"W\d+", str(i))]
        if bad:
            errs.append(f"{r['key']}: src {', '.join(bad)} 는 이 행·시장 뉴스나 보완 출처가 아님")
        src = list(dict.fromkeys(own + r["auto_src"]))[:lim["src_max"]]
        summary = (lead + " " + " ".join(r["facts"])).strip()
        ck = r["checkpoint"]
        row = {"key": r["key"], "summary": summary, "nums": r["nums"], "src": src,
               "table": json.dumps(r.get("table") or [], ensure_ascii=False, separators=(",", ":"))}
        if daily:
            row["outlook"] = shift(r["outlook"], adj)
            row["checkpoint"] = ck
        else:
            row.update(s=shift(r["s"], adj), m=r["m"], l=r["l"], event=ck)
        rows.append(row)
    return {"rows": rows}, errs


def load(work, name, required=True):
    try:
        return C.load_json(f"{work}/{name}")
    except (OSError, json.JSONDecodeError) as e:
        if required:
            C.fail(f"{name} 를 읽지 못함: {e}", C.ABORT)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["brief", "merge"])
    ap.add_argument("--work", default="work")
    ap.add_argument("--notes")
    a = ap.parse_args()
    cfg = C.load_config()
    if a.cmd == "brief":
        inp, errs = C.normalize_input(load(a.work, "input.json"))
        if errs:
            C.fail("input.json 오류: " + "; ".join(errs))
        dr = brief(inp, load(a.work, "market.json"), load(a.work, "news.json"), load(a.work, "calendar.json", False),
                   load(a.work, "events.json", False), cfg)
        C.save_json(f"{a.work}/draft.json", dr)
        print(brief_text(dr))
        return
    dr = load(a.work, "draft.json")
    from pathlib import Path
    if not Path(f"{a.work}/extra_sources.json").exists():
        C.save_json(f"{a.work}/extra_sources.json", {"items": []})
    notes = C.load_json(a.notes or f"{a.work}/notes.json")
    an, errs = merge(dr, notes, cfg)
    if errs:
        print(json.dumps({"ok": False, "retry": True, "errors": errs}, ensure_ascii=False, indent=1))
        raise SystemExit(C.RETRY)
    C.save_json(f"{a.work}/analysis.json", an)
    print(json.dumps({"ok": True, "rows": len(an["rows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
