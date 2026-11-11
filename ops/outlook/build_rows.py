"""S5 검증·행 조립 (결정적).

LLM 출력(analysis.json)을 검사하고, 통과하면 시트 헤더 순서의 행(rows.json)을 만든다.
작성일·구분·티커·종목명·출처 URL은 코드가 채운다.

사용:
  python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
      --calendar work/calendar.json --events work/events.json \
      --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json [--sheet 시트명]
경제 캘린더(C..)·종목 이벤트(E..) ID 도 nums 로 인용할 수 있다. 마지막 열 '작성시각'(KST)은 코드가 채운다.
종료 코드: 0 통과, 1 analysis 재작성 필요(오류 목록 출력), 2 중단(입력 파일 오류)
"""
import argparse
import json
import re
from datetime import date, timedelta

import common as C
import sheetlog as L

NUM_RE = re.compile(r"[+\-−]?\d[\d,]*(?:\.\d+)?%?")
# 날짜·시각·기간처럼 수치 검증 대상이 아닌 표현은 먼저 지운다
SKIP_RES = [
    re.compile(r"\d{4}[-./]\s?\d{1,2}[-./]\s?\d{1,2}"),
    re.compile(r"\d{4}년"),
    re.compile(r"\d{1,2}월\s?\d{1,2}일"),
    re.compile(r"\d{1,2}월"),
    re.compile(r"\d{1,2}/\d{1,2}"),
    re.compile(r"\d{1,2}:\d{2}"),
    re.compile(r"\d+\s?(?:일선|일|주|개월|년|거래일|분기|시|분|배|건|명|위|회|개|bp)"),
    re.compile(r"[1-4]Q\d{0,2}|Q[1-4]"),
]


def numbers_in(text):
    """검증 대상 수치 [(값, 소수 자릿수, 원문)]. 소수점·쉼표·% 가 있거나 1000 이상인 수만 대상으로 한다."""
    t = text
    for r in SKIP_RES:
        t = r.sub(" ", t)
    out = []
    for m in NUM_RE.finditer(t):
        raw = m.group(0)
        s = raw.replace(",", "").replace("−", "-").rstrip("%")
        try:
            v = float(s)
        except ValueError:
            continue
        dec = len(s.split(".", 1)[1]) if "." in s else 0
        if "." in raw or "," in raw or raw.endswith("%") or abs(v) >= 1000:
            out.append((v, dec, raw))
    return out


DATE_MD = [re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)"), re.compile(r"(\d{1,2})월\s?(\d{1,2})일"),
           re.compile(r"\d{4}-(\d{1,2})-(\d{1,2})")]
DATE_M = re.compile(r"(\d{1,2})월\s?(초|상순|중순|중|하순|말)")


def dates_in(text):
    """본문의 날짜 언급 → ({(월, 일)}, {월만 언급})."""
    t = str(text)
    md = {(int(m.group(1)), int(m.group(2))) for r in DATE_MD for m in r.finditer(t)
          if 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31}
    mo = {int(m.group(1)) for m in DATE_M.finditer(t) if 1 <= int(m.group(1)) <= 12}
    return md, mo


def flat_strings(obj):
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in flat_strings(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in flat_strings(v)]
    return []


def flat_numbers(obj):
    """dict/list 안의 모든 숫자."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in flat_numbers(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in flat_numbers(v)]
    return []


def matches(v, dec, allowed):
    tol = 0.5 * 10 ** (-dec) + 1e-9
    return any(abs(abs(v) - abs(a)) <= tol for a in allowed)


def sentences(text):
    return len(re.findall(r"[.!?](?=\s|$)", text.strip())) or (1 if text.strip() else 0)


class Ctx:
    def __init__(self, inp, market, news, extra, cfg, cal=None, events=None):
        self.inp, self.market, self.cfg = inp, market, cfg
        self.items = dict(market["items"])
        for src in (cal, events):
            for k, v in ((src or {}).get("items") or {}).items():
                self.items[k] = {kk: vv for kk, vv in v.items() if kk not in ("status", "score", "days")}
                # 캘린더 표시값(예상·이전·실제)은 문자열이라 숫자로 풀어 인용 가능하게 한다
                txt = [str(v.get(f)) for f in ("forecast", "previous", "actual") if v.get(f) not in (None, "")]
                if txt:
                    self.items[k]["_shown"] = [x[0] for t in txt for x in numbers_in(t)] + \
                        [float(m) for t in txt for m in re.findall(r"\d+(?:\.\d+)?", t)]
        self.src = {}
        for o in news.get("items", []):
            self.src[o["id"]] = {"url": o["url"], "nums": [x[0] for x in numbers_in(o["title"])],
                                 "text": o["title"] + " " + str(o.get("published", ""))}
        self.extra = {}
        for o in extra.get("items", []):
            self.extra[o["id"]] = o
            self.src[o["id"]] = {"url": o["url"], "nums": flat_numbers(o.get("values") or {}),
                                 "text": " ".join([o.get("title", "")] + flat_strings(o.get("values") or {}))}
        self.limits = cfg["limits"][inp["mode"]]
        # 출처 없이 쓸 수 있는 날짜: 작성일, 입력의 앱 신호 날짜
        self.base_dates = " ".join([inp["date"]] + [str(x.get("date", "")) for x in inp.get("signals") or []])


def check_extra(extra):
    errs = []
    ids = set()
    for o in extra.get("items", []):
        if not re.fullmatch(r"W\d+", str(o.get("id", ""))):
            errs.append(f"extra_sources: ID 형식 오류 {o.get('id')} (W1, W2 ...)")
        if o.get("id") in ids:
            errs.append(f"extra_sources: ID 중복 {o['id']}")
        ids.add(o.get("id"))
        if not str(o.get("url", "")).startswith(("http://", "https://")):
            errs.append(f"extra_sources: {o.get('id')} URL 없음")
    return errs


def check_row(key, r, ctx, text_fields, value_fields):
    errs = []
    cfg, lim = ctx.cfg, ctx.limits
    for f in value_fields:
        if r.get(f) not in cfg["outlook_values"]:
            errs.append(f"{key}: {f} 값 '{r.get(f)}' 는 허용값({', '.join(cfg['outlook_values'])})이 아님")
    nums, src = r.get("nums") or [], r.get("src") or []
    for i in nums:
        if i not in ctx.items and i not in ctx.extra:
            errs.append(f"{key}: nums 의 ID {i} 가 없음")
    for i in src:
        if i not in ctx.src:
            errs.append(f"{key}: src 의 ID {i} 가 없음")
    if len(src) > lim["src_max"]:
        errs.append(f"{key}: 출처는 최대 {lim['src_max']}개")
    allowed = []
    for i in nums:
        allowed += flat_numbers(ctx.items.get(i) or (ctx.extra.get(i) or {}).get("values") or {})
    for i in src:
        allowed += (ctx.src.get(i) or {}).get("nums", [])
    text = " ".join(str(r.get(f, "")) for f in text_fields)
    for v, dec, raw in numbers_in(text):
        if not matches(v, dec, allowed):
            errs.append(f"{key}: 수치 '{raw}' 가 nums/src 로 인용한 값에 없음")
    ok_md, _ = dates_in(" ".join([ctx.base_dates] + [" ".join(flat_strings(ctx.items.get(i) or {})) for i in nums]
                                 + [(ctx.src.get(i) or {}).get("text", "") for i in src]
                                 + [" ".join(flat_strings((ctx.extra.get(i) or {}).get("values") or {})) for i in nums]))
    ok_mo = {m for m, _ in ok_md}
    md, mo = dates_in(text)
    for m, d in sorted(md - ok_md):
        errs.append(f"{key}: 날짜 '{m}/{d}' 가 nums/src 로 인용한 자료에 없음 (출처를 인용하거나 날짜를 빼야 함)")
    for m in sorted(mo - ok_mo):
        errs.append(f"{key}: '{m}월' 일정이 nums/src 로 인용한 자료에 없음 (근거 없는 시기 추정 금지)")
    for w in cfg["forbidden"]:
        if w in text:
            errs.append(f"{key}: 금지 표현 '{w}'")
    bad = [i for i in nums if (ctx.items.get(i) or {}).get("status", "ok") != "ok"]
    if bad and "확인 못 함" not in text and "기준" not in text:
        errs.append(f"{key}: 상태가 ok 가 아닌 값({', '.join(bad)})을 인용하면 '확인 못 함' 또는 기준 시각을 적어야 함")
    s = str(r.get("summary", ""))
    if not s.strip() and "summary" in text_fields:
        errs.append(f"{key}: summary 비어 있음")
    if len(s) > lim["summary_chars"]:
        errs.append(f"{key}: summary {len(s)}자 (최대 {lim['summary_chars']}자)")
    if sentences(s) > lim["summary_sentences"]:
        errs.append(f"{key}: summary {sentences(s)}문장 (최대 {lim['summary_sentences']}문장)")
    for f in text_fields:
        if f != "summary" and len(str(r.get(f, ""))) > lim["check_chars"]:
            errs.append(f"{key}: {f} {len(str(r.get(f, '')))}자 (최대 {lim['check_chars']}자)")
    return errs


def build(inp, market, news, extra, analysis, cfg, sheet=None, cal=None, events=None, now=None):
    errs = check_extra(extra)
    ctx = Ctx(inp, market, news, extra, cfg, cal, events)
    ts = (now or C.now_kst()).strftime("%Y-%m-%d %H:%M")
    mode = inp["mode"]
    rows_in = {}
    for r in analysis.get("rows", []):
        k = str(r.get("key", "")).upper() if r.get("key") != "market" else r["key"]
        if k in rows_in:
            errs.append(f"{k}: 행 중복")
        rows_in[k] = r
    keys = ["market"] + [h["ticker"] for h in inp["holdings"] + inp["watch"]]
    for k in keys:
        if k not in rows_in:
            errs.append(f"{k}: analysis 에 행이 없음")
    extra_keys = set(rows_in) - set(keys)
    if extra_keys:
        errs.append(f"analysis 에 입력에 없는 행: {', '.join(sorted(extra_keys))}")
    if mode == "daily":
        tf, vf = ["summary", "checkpoint"], ["outlook"]
    else:
        tf, vf = ["summary", "event"], ["s", "m", "l"]
    for k in keys:
        if k in rows_in:
            errs += check_row(k, rows_in[k], ctx, tf, vf)
    if errs:
        return None, errs

    d = inp["date"]
    sh = cfg["sheets"][mode]

    def urls(r):
        return " ".join(ctx.src[i]["url"] for i in (r.get("src") or []))

    def line(kind, ticker, name, r):
        if mode == "daily":
            return [d, kind, ticker, name, r["outlook"], r["summary"], r.get("checkpoint", ""), urls(r), ts]
        return [d, kind, ticker, name, r["s"], r["m"], r["l"], r["summary"], r.get("event", ""), urls(r), ts]

    rows = [line("시장", "", "시장 전체", rows_in["market"])]
    for kind, lst in (("보유", inp["holdings"]), ("관심", inp["watch"])):
        for h in lst:
            rows.append(line(kind, h["ticker"], h["name"], rows_in[h["ticker"]]))
    cut = (date.fromisoformat(d) - timedelta(days=sh["keep_days"])).isoformat()
    return {"sheet": sheet or sh["name"], "header": sh["header"], "date": d, "delete_before": cut, "written_at": ts,
            "rows": rows}, []


def main():
    ap = argparse.ArgumentParser()
    for f in ("input", "market", "news", "analysis", "out"):
        ap.add_argument("--" + f, required=True)
    ap.add_argument("--extra", help="S3 보완 출처 (없으면 생략)")
    ap.add_argument("--calendar", help="경제 캘린더 calendar.json (없으면 생략)")
    ap.add_argument("--events", help="종목 이벤트 events.json (없으면 생략)")
    ap.add_argument("--sheet", help="기록할 시트 이름 (병행 운영 시 _v2)")
    a = ap.parse_args()
    cfg = C.load_config()
    try:
        inp, ierr = C.normalize_input(C.load_json(a.input))
        ierr += C.date_errors(inp)
        market, news = C.load_json(a.market), C.load_json(a.news)
        extra = C.load_json(a.extra) if a.extra else {"items": []}
        analysis = C.load_json(a.analysis)
        cal = C.load_json(a.calendar) if a.calendar else None
        events = C.load_json(a.events) if a.events else None
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"입력 파일을 읽지 못함: {e}", C.ABORT)
    if ierr:
        C.fail("input.json 오류: " + "; ".join(ierr), C.ABORT)
    if market.get("date") != inp["date"] or news.get("date") != inp["date"]:
        C.fail("market/news 의 date 가 input 과 다름 (S1, S2 를 다시 실행)", C.ABORT)
    try:
        res, errs = build(inp, market, news, extra, analysis, cfg, a.sheet, cal, events)
    except (KeyError, TypeError, ValueError) as e:
        errs = [f"analysis.json 구조 오류: {e}"]
    if errs:
        print(json.dumps({"ok": False, "retry": True, "errors": errs}, ensure_ascii=False, indent=1))
        raise SystemExit(C.RETRY)
    C.save_json(a.out, res)
    L.guard_add(res["sheet"])  # 7단계 시트 쓰기 보호(훅)를 켠다
    print(json.dumps({"ok": True, "sheet": res["sheet"], "rows": len(res["rows"]),
                      "delete_before": res["delete_before"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
