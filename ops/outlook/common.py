"""전망 파이프라인 공용 유틸. 표준 라이브러리만 사용한다."""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))

# 종료 코드: 0 통과, 1 재작성·재시도 필요, 2 중단
OK, RETRY, ABORT = 0, 1, 2


def load_config():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def now_kst():
    return datetime.now(KST)


def iso_kst(ts):
    """유닉스 초 → KST ISO 문자열."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, KST).isoformat(timespec="minutes")


def http_get(url, cfg, accept="application/json", headers=None):
    """GET 요청. 재시도·지수 백오프. 실패하면 예외."""
    h = cfg["http"]
    last = None
    for i in range(h["retries"]):
        try:
            hd = {"User-Agent": "stock-journal-outlook/1.0", "Accept": accept}
            hd.update(headers or {})
            req = urllib.request.Request(url, headers=hd)
            with urllib.request.urlopen(req, timeout=h["timeout_sec"]) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                break
            time.sleep(h["backoff_sec"] * (2 ** i))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def market_of(ticker, market=None):
    if market in ("KR", "US"):
        return market
    ex = ticker.split(":", 1)[0].upper() if ":" in ticker else ""
    return "KR" if ex in ("KRX", "KOSDAQ", "KOSPI") else "US"


def ysym(ticker, market):
    """시트 티커(KRX:005930, NASDAQ:MU) → Yahoo 심볼. 앱의 ySym 과 같은 규칙."""
    t = str(ticker).strip().upper()
    ex, code = (t.split(":", 1) if ":" in t else ("", t))
    if market == "KR":
        return code + (".KQ" if ex == "KOSDAQ" else ".KS") if re.fullmatch(r"[0-9A-Z]{6}", code) else ""
    return code.replace(".", "-") if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", code) else ""


def normalize_input(inp):
    """input.json 검증·정리. 문제 목록을 함께 돌려준다."""
    errs = []
    if inp.get("mode") not in ("daily", "weekly"):
        errs.append("mode 는 daily 또는 weekly")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(inp.get("date", ""))):
        errs.append("date 형식은 YYYY-MM-DD")
    seen = set()
    out = {"holdings": [], "watch": []}
    for kind in ("holdings", "watch"):
        for o in inp.get(kind) or []:
            t = str(o.get("ticker", "")).strip().upper()
            if not t:
                errs.append(f"{kind}: 티커 없음 {o}")
                continue
            if t in seen:
                continue  # 보유와 관심이 겹치면 보유로 한 번만
            seen.add(t)
            m = market_of(t, o.get("market"))
            if not ysym(t, m):
                errs.append(f"{kind}: 티커 형식 오류 {t}")
            out[kind].append({"ticker": t, "name": str(o.get("name", "")).strip() or t, "market": m})
    res = dict(inp)
    res.update(out)
    res.setdefault("signals", [])
    res.setdefault("weekly", [])
    res.setdefault("fin", [])
    return res, errs


def fail(msg, code=ABORT):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)
