"""S1+S2 수집과 요약 출력 (결정적).

collect_market, collect_news 를 한 번에 실행해 market.json, news.json 을 만들고,
해석(S4)에 필요한 값만 한 줄씩 요약해 출력한다. LLM 은 이 요약만 읽고 JSON 원본은 열지 않는다.

사용: python3 collect.py --input work/input.json --work work
종료 코드: 0 통과, 2 중단(입력 오류, 지수 전부 실패, 뉴스 전부 실패)
"""
import argparse
import json

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


def digest(inp, market, news):
    it = market["items"]
    out = [f"# {inp['date']} {inp['mode']} 요약 (ID로 인용)", "## 지수·환율·금리"]
    out += [quote_line(it[i]) for i in market["indices"]]
    out += ["## 미국 지수선물"] + [quote_line(it[i]) for i in market["futures"]]
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
    out.append("## 뉴스")
    for n in news["items"]:
        out.append(f"{n['id']} [{n['scope']}] {n['published'][5:16].replace('T', ' ')} {n['source']} | {n['title']}")
    if news.get("errors"):
        out.append("뉴스 오류: " + "; ".join(news["errors"]))
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
    print(digest(inp, market, news))
    bad = {k: v["status"] for k, v in market["items"].items() if v["status"] != "ok"}
    print(json.dumps({"ok": True, "items": len(market["items"]), "news": len(news["items"]), "not_ok": bad},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
