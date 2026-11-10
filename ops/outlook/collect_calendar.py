"""경제 캘린더 수집 (결정적).

TradingView 공개 경제캘린더(무키)에서 미국·한국 지표 일정과 발표값을 받아, 발표된 지표를
예상치와 비교해 호재·악재·혼조·중립·예정으로 판정한다. 판정 로직은 bluedbs/invest-trading-bot
src/calendar.js(judgeBias, summarizeMacroTone)를 옮긴 것이다(매수 예산 배수는 쓰지 않는다).

사용: python3 collect_calendar.py --input work/input.json --out work/calendar.json
종료 코드: 항상 0 (실패하면 status "unavailable" 로 기록하고 진행한다, fail-open)
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone

import common as C

TV_URL = "https://economic-calendar.tradingview.com/events"

# 지표 카테고리: 정규식, 방향(높을수록 호재인가), 가중치. 금리 민감 기술·반도체 포트폴리오 기준.
CATEGORIES = [
    {"name": "rate", "re": re.compile(r"interest rate decision|fed interest rate|fed funds", re.I), "special": "rate", "weight": 1.6},
    {"name": "inflation", "re": re.compile(r"inflation|\bcpi\b|\bpce\b|\bppi\b|price index", re.I), "good_high": False, "weight": 1.4},
    {"name": "unemployment", "re": re.compile(r"unemployment rate|jobless|initial claims|continuing claims", re.I),
     "good_high": False, "weight": 0.8, "two_sided": True},
    {"name": "jobs", "re": re.compile(r"payroll|non.?farm|nonfarm|\badp\b|jolts|job offers|job openings|employment change", re.I),
     "good_high": True, "weight": 0.6, "two_sided": True},
    {"name": "growth", "re": re.compile(r"\bgdp\b|\bpmi\b|\bism\b|retail|consumer confidence|durable|industrial|manufactur|"
                                        r"production|\bsales\b|housing starts|building permits|business confidence|exports", re.I),
     "good_high": True, "weight": 1.0},
]
HIGH_IMPACT = re.compile(r"\bcpi\b|inflation rate|\bpce\b|\bppi\b|interest rate decision|fed interest rate|non.?farm|nonfarm|"
                         r"unemployment rate|\bgdp\b|ism manufacturing|ism services|retail sales|fomc", re.I)
# 고영향 표시: TradingView 중요도 1, 또는 금리 결정·FOMC(한국 금통위는 중요도 0으로 온다)
MAJOR = re.compile(r"interest rate decision|fed interest rate|fomc", re.I)
# 시트·요약에서 뺄 잡음 지표(국채 입찰 세부, 모기지 금리, 원자재 재고 등)
NOISE = re.compile(r"auction|\bmba\b|mortgage|\beia\b|\busda\b|wasde|nopa|rig count|\bapi crude|tic flows|"
                   r"budget statement|baker hughes|ethanol|bill |redbook|money supply|broad money", re.I)
# 같은 지표의 보조 계열(지수 수준, 계절조정 전 등)은 고영향 표시에서 뺀다
VARIANT = re.compile(r"seasonally|\bnsa\b|cpi-w|^cpi$|^consumer price index$|machinery|real average", re.I)
COUNTRY_KO = {"US": "미국", "KR": "한국"}
TITLE_KO = [
    (r"^Core Inflation Rate MoM", "근원 CPI 전월비"), (r"^Core Inflation Rate YoY", "근원 CPI 전년비"),
    (r"^Inflation Rate MoM$", "CPI 전월비"), (r"^Inflation Rate YoY$", "CPI 전년비"),
    (r"^Core PCE Price Index MoM", "근원 PCE 전월비"), (r"^Core PCE Price Index YoY", "근원 PCE 전년비"),
    (r"^PCE Price Index MoM", "PCE 전월비"), (r"^PCE Price Index YoY", "PCE 전년비"),
    (r"^Core PPI MoM", "근원 PPI 전월비"), (r"^PPI MoM", "PPI 전월비"), (r"^PPI YoY", "PPI 전년비"),
    (r"^Non Farm Payrolls", "비농업 고용"), (r"^Unemployment Rate", "실업률"),
    (r"^Initial Jobless Claims", "신규 실업수당 청구"), (r"^Average Hourly Earnings MoM", "평균 시간당 임금 전월비"),
    (r"^Average Hourly Earnings YoY", "평균 시간당 임금 전년비"), (r"^ADP Employment Change", "ADP 고용"),
    (r"^JOLTs Job Openings", "JOLTs 구인"), (r"^Fed Interest Rate Decision", "FOMC 금리 결정"),
    (r"^FOMC Minutes", "FOMC 의사록"), (r"^Interest Rate Decision", "기준금리 결정"),
    (r"^GDP Growth Rate QoQ", "GDP 성장률 전기비"), (r"^GDP Growth Rate YoY", "GDP 성장률 전년비"),
    (r"^Retail Sales MoM", "소매판매 전월비"), (r"^Retail Sales Ex Autos MoM", "소매판매(자동차 제외) 전월비"),
    (r"^ISM Manufacturing PMI", "ISM 제조업 PMI"), (r"^ISM Services PMI", "ISM 서비스업 PMI"),
    (r"^S&P Global Manufacturing PMI", "S&P 제조업 PMI"), (r"^S&P Global Services PMI", "S&P 서비스업 PMI"),
    (r"^Michigan Consumer Sentiment", "미시간대 소비자심리"), (r"^Industrial Production MoM", "산업생산 전월비"),
    (r"^Housing Starts$", "주택 착공"), (r"^Building Permits", "건축 허가"), (r"^Existing Home Sales$", "기존주택 판매"),
    (r"^Durable Goods Orders MoM", "내구재 주문 전월비"), (r"^Exports YoY", "수출 전년비"), (r"^Imports YoY", "수입 전년비"),
    (r"^Consumer Confidence", "소비자신뢰"), (r"^Consumer Price Index$", "소비자물가지수"),
]


def title_ko(title):
    for pat, ko in TITLE_KO:
        if re.search(pat, title or ""):
            return ko + (" (예비)" if re.search(r"prel|flash|adv", title, re.I) else "")
    return ""


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def text(v):
    return None if v is None or v == "" else str(v)


def r1(v):
    return round(float(v) * 10) / 10


def category_of(t):
    return next((c for c in CATEGORIES if c["re"].search(t)), None)


def mk(bias, score, cat, reason):
    a = abs(score)
    strength = None if bias == "중립" else ("강한" if a >= 1.8 else "보통" if a >= 0.9 else "약한")
    label = "중립" if bias == "중립" else (bias if strength == "보통" else f"{strength} {bias}")
    return {"bias": bias, "strength": strength, "score": r1(score), "label": label, "category": cat, "reason": reason}


def judge_bias(title, indicator, importance, actual, forecast, previous, actual_t=None, forecast_t=None, previous_t=None):
    """발표 전 '예정', 발표 후 서프라이즈 크기 × 지표 가중치 × 중요도로 점수를 매긴다."""
    cat = category_of(f"{title or ''} {indicator or ''}")
    cname = cat["name"] if cat else None
    if actual is None:
        return {"bias": "예정", "strength": None, "score": 0, "label": "예정", "category": cname,
                "reason": f"예상치 {forecast_t}" if forecast_t else "발표 예정"}
    imp_w = 1.3 if importance >= 1 else 1.0
    if cat and cat.get("special") == "rate" and previous is not None:
        if actual == previous:
            return {"bias": "중립", "strength": None, "score": 0, "label": "중립", "category": "rate",
                    "reason": f"금리 동결 {actual_t}"}
        cut = actual < previous
        return mk("호재" if cut else "악재", (1 if cut else -1) * 2 * cat["weight"] * imp_w, "rate",
                  f"금리 {'인하' if cut else '인상'} {previous_t}→{actual_t}")
    if forecast is None or not cat:
        return {"bias": "중립", "strength": None, "score": 0, "label": "중립", "category": cname,
                "reason": f"발표값 {actual_t}" + (f" (이전 {previous_t})" if previous_t else "")}
    surprise = actual - forecast
    if abs(surprise) < 1e-9:
        return {"bias": "중립", "strength": None, "score": 0, "label": "중립", "category": cname,
                "reason": f"예상 부합 {actual_t}"}
    rel = abs(surprise) / max(abs(forecast), abs(previous or 0), 1e-6)
    mag = 0.6 if rel < 0.05 else 1.2 if rel <= 0.2 else 2.0
    good = surprise > 0 if cat["good_high"] else surprise < 0
    beat = "예상 상회" if surprise > 0 else "예상 하회"
    if cat.get("two_sided"):
        note = "성장 호조이나 금리인하 지연 가능(매파)" if good else "성장 둔화 우려이나 금리인하 기대(비둘기)"
        sc = r1((1 if good else -1) * min(mag, 1.2) * cat["weight"] * imp_w * 0.3)
        return {"bias": "혼조", "strength": None, "score": sc, "label": "혼조", "category": cname,
                "reason": f"{beat} (발표 {actual_t} vs 예상 {forecast_t}) — {note}"}
    sc = r1((1 if good else -1) * mag * cat["weight"] * imp_w)
    bias = "호재" if sc > 0.5 else "악재" if sc < -0.5 else "중립"
    return mk(bias, sc, cname, f"{beat} (발표 {actual_t} vs 예상 {forecast_t})")


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def normalize(e):
    released = e.get("actual") not in (None, "")
    actual = num(e.get("actualRaw") if e.get("actualRaw") is not None else e.get("actual")) if released else None
    forecast = num(e.get("forecastRaw") if e.get("forecastRaw") is not None else e.get("forecast"))
    previous = num(e.get("previousRaw") if e.get("previousRaw") is not None else e.get("previous"))
    imp = int(num(e.get("importance")) if num(e.get("importance")) is not None else -1)
    j = judge_bias(e.get("title"), e.get("indicator"), imp, actual, forecast, previous,
                   text(e.get("actual")), text(e.get("forecast")), text(e.get("previous")))
    dt = parse_ts(e.get("date"))
    t = e.get("title") or e.get("indicator") or "지표"
    unit = e.get("unit") or ""
    fmt = lambda v: None if v in (None, "") else f"{v}{unit if unit in ('%',) else ''}"  # noqa: E731
    return {
        "tv_id": str(e.get("id") or f"{t}-{e.get('date')}"), "title": t, "title_ko": title_ko(t),
        "country": e.get("country") or "", "period": e.get("period") or "",
        "at": dt.astimezone(C.KST).isoformat(timespec="minutes") if dt else None,
        "importance": imp,
        "high": imp >= 1 or bool(MAJOR.search(t)) or (bool(HIGH_IMPACT.search(t)) and not VARIANT.search(t)),
        "actual": fmt(e.get("actual")), "forecast": fmt(e.get("forecast")), "previous": fmt(e.get("previous")),
        "actual_n": actual, "forecast_n": forecast, "previous_n": previous,
        "released": released, "category": j["category"], "bias": j["bias"], "strength": j["strength"],
        "score": j["score"], "label": j["label"], "reason": j["reason"],
        "comment": re.sub(r"\s+", " ", str(e.get("comment") or ""))[:160], "source_url": e.get("source_url") or "",
    }


def keep(o):
    """시트·요약 대상: 중요도 1 또는 고영향, 또는 분류되는 지표 중 잡음이 아닌 것."""
    if NOISE.search(o["title"]):
        return o["importance"] >= 1
    return o["importance"] >= 1 or o["high"] or (o["importance"] >= 0 and o["category"] is not None)


def macro_tone(items, now, days=5):
    """최근 days 일 발표분 점수 합산 → 호재 우위 / 악재 우위 / 중립 (invest-trading-bot summarizeMacroTone)."""
    lo = now - timedelta(days=days)
    rec = [o for o in items if o["released"] and o["at"] and lo <= datetime.fromisoformat(o["at"]) <= now]
    if not rec:
        return {"tone": "중립", "score": 0, "count": 0, "label": "최근 발표 없음", "drivers": []}
    sc = r1(sum(o["score"] for o in rec))
    tone = "호재 우위" if sc >= 2 else "악재 우위" if sc <= -2 else "중립"
    top = sorted(rec, key=lambda o: -abs(o["score"]))[:3]
    sign = {"호재": "+", "악재": "−"}
    return {"tone": tone, "score": sc, "count": len(rec),
            "label": f"최근 {days}일 {len(rec)}건 합산 {'+' if sc > 0 else ''}{sc}",
            "drivers": [f"{sign.get(o['bias'], '·')}{o['title_ko'] or o['title']}" for o in top]}


def collect(inp, cfg, fetch=None, now=None):
    cc = cfg.get("calendar") or {}
    now = now or C.now_kst()
    out = {"date": inp["date"], "mode": inp["mode"], "generated_at": now.isoformat(timespec="minutes"),
           "status": "ok", "items": {}, "order": [], "tone": None, "errors": [],
           "digest_max": cc.get("digest_max", 14)}
    if not cc.get("enabled", True):
        out["status"] = "disabled"
        return out
    frm = (now - timedelta(days=cc.get("days_back", 4))).astimezone(timezone.utc)
    to = (now + timedelta(days=cc.get("days_ahead", 14))).astimezone(timezone.utc)
    url = (f"{TV_URL}?from={frm.strftime('%Y-%m-%dT%H:%M:%S.000Z')}&to={to.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
           f"&countries={cc.get('countries', 'US')}")
    fetch = fetch or (lambda u: C.http_get(u, cfg, headers={"Origin": "https://www.tradingview.com",
                                                             "User-Agent": "Mozilla/5.0"}))
    try:
        data = json.loads(fetch(url))
        raw = (data.get("result") or data.get("data") or []) if isinstance(data, dict) else data
    except Exception as e:  # noqa: BLE001 — fail-open
        out["status"] = "unavailable"
        out["errors"].append(f"경제 캘린더: {e}")
        return out
    evs = [normalize(e) for e in raw if num(e.get("importance")) is not None and num(e.get("importance")) >= 0]
    evs = [o for o in evs if o["at"] and keep(o)]
    evs.sort(key=lambda o: (o["at"], -o["importance"], o["title"]))
    evs = evs[: cc.get("max", 120)]
    for i, o in enumerate(evs, 1):
        o["id"] = f"C{i}"
        out["items"][o["id"]] = o
        out["order"].append(o["id"])
    out["tone"] = macro_tone(evs, now)
    return out


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
    C.save_json(a.out, res)
    print(json.dumps({"ok": True, "status": res["status"], "items": len(res["order"]), "errors": res["errors"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
