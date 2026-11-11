"""6단계 기록 검증 (결정적).

plan.json(plan_write.py 결과)과 훅이 남긴 커넥터 응답 원문(work/sheet_log.jsonl)을 대조한다.
세션이 만든 파일은 읽지 않는다.
- 추가: 이 plan 으로 훅이 채운 append_values 응답의 updatedRange 가 계획한 시작 행·행 수와 같아야 한다.
- 재조회: plan.json 의 readback 범위를 추가 이후에 읽은 get_values 응답이 있어야 하고, 모든 열 값이
  기록하려던 값과 같아야 한다(숫자·백분율·날짜 표시 형식 차이는 정규화).
전망은 1~2행으로 헤더와 보관 기간도 확인한다.

사용: python3 verify_written.py --plan work/plan.json
종료 코드: 0 일치, 1 불일치 또는 재조회 부족(5단계 재시도·재조회), 2 중단(파일 오류)
"""
import argparse
import json
import re

import common as C
import sheetlog as L


def norm_date(v):
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else s


def norm(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


DT = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\.?(?:\s*(오전|오후|AM|PM)?\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?)?")


def norm_cell(v, j=0):
    """시트 표시 형식 차이를 없앤 비교용 값. 앞 작은따옴표(텍스트 강제), 쉼표·%, 날짜·시각 표기."""
    s = norm(v)
    if s.startswith("'"):
        s = s[1:]
    m = DT.fullmatch(s)
    if m:
        d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if m.group(5):
            h = int(m.group(5))
            ap = m.group(4) or m.group(7)
            if ap in ("오후", "PM") and h < 12:
                h += 12
            if ap in ("오전", "AM") and h == 12:
                h = 0
            d += f" {h:02d}:{m.group(6)}"
        return d
    n = s.replace(",", "")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?%?", n):
        return f"{float(n.rstrip('%')):.6g}" + ("%" if n.endswith("%") else "")
    return s


def parse_range(r):
    """"'App_일간전망'!A297:E318" → ("App_일간전망", 297, 318). 행 번호가 없으면 None."""
    m = re.fullmatch(r"'?(.+?)'?!([A-Z]+)(\d+)?(?::([A-Z]+)(\d+)?)?", str(r).strip())
    if not m:
        raise ValueError(f"범위 형식 오류: {r}")
    t = m.group(1).replace("''", "'")
    a = int(m.group(3)) if m.group(3) else None
    b = int(m.group(5)) if m.group(5) else a
    return t, a, b


def verify(plan, append, readbacks):
    errs = []
    sheet, exp = plan["sheet"], plan["expect"]
    if exp["rows"]:
        up = (append or {}).get("updates") or {}
        if "updatedRange" not in up:
            return ["append 응답 원문이 아님 (updates.updatedRange 없음)"]
        t, a, b = parse_range(up["updatedRange"])
        if t != sheet:
            errs.append(f"추가한 시트 {t} != {sheet}")
        if (a, b) != (exp["start"], exp["end"]):
            errs.append(f"추가 위치 {a}~{b}행 != 계획 {exp['start']}~{exp['end']}행 (삭제가 안 됐거나 표 아래에 빈 행이 있음)")
        if up.get("updatedRows") not in (None, exp["rows"]):
            errs.append(f"추가 행 수 {up.get('updatedRows')} != {exp['rows']}")
    want_rb = plan["readback"]
    if len(readbacks) != len(want_rb):
        return errs + [f"재조회 응답 {len(readbacks)}개 != 계획 {len(want_rb)}개 ({', '.join(want_rb)})"]
    n = exp["cols"]
    for rng, rb in zip(want_rb, readbacks):
        if not isinstance(rb, dict) or "range" not in rb:
            errs.append(f"{rng}: 커넥터 응답 원문이 아님 (range 필드 없음)")
            continue
        try:
            got_t, got_a, _ = parse_range(rb["range"])
        except ValueError as e:
            errs.append(str(e))
            continue
        t, a, b = parse_range(rng)
        if (got_t, got_a) != (t, a):
            errs.append(f"재조회 범위 {rb['range']} != 계획 {rng}")
            continue
        vals = rb.get("values") or []
        if a == 1:  # 헤더와 첫 데이터 행
            head = [norm(x) for x in (vals[0] if vals else [])][:n]
            if head != [norm(x) for x in plan["header"][:n]]:
                errs.append(f"헤더 불일치: {head}")
            if plan.get("delete_before") and len(vals) > 1 and vals[1]:
                d = norm_date(vals[1][0])
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < plan["delete_before"]:
                    errs.append(f"보관 기간({plan['delete_before']} 이전)이 지난 행이 남아 있음: {d}")
            continue
        want = [r[:n] for r in plan["append"]["values"]]
        if len(vals) != len(want):
            errs.append(f"{rng}: 행 수 {len(vals)} != {len(want)}")
        for i, (g, e) in enumerate(zip(vals, want)):
            g = list(g) + [""] * (n - len(g))
            for j in range(n):
                if norm_cell(g[j], j) != norm_cell(e[j], j):
                    errs.append(f"{a + i}행 {plan['header'][j]}: '{norm(g[j])[:40]}' != '{norm(e[j])[:40]}'")
    return errs


def from_log(plan, log=None):
    """훅 기록에서 이 plan 의 append 응답과 그 뒤의 재조회 응답을 찾는다."""
    log = L.log_read() if log is None else log
    app, app_seq = None, -1
    if plan["expect"]["rows"]:
        for r in log:
            if r.get("phase") == "post" and r.get("tool") == "append_values" and r.get("plan_id") == plan["id"]:
                app, app_seq = r["response"], r["seq"]
        if app is None:
            return None, None, ["이 plan 의 append_values 기록이 없다(@plan 으로 추가했는지 확인)"]
    else:
        app_seq = max((r["seq"] for r in log if r.get("phase") == "post" and r.get("plan_id") == plan["id"]), default=-1)
    rbs, miss = [], []
    for rng in plan["readback"]:
        hit = None
        for r in reversed(log):
            if r["seq"] <= app_seq:
                break
            if r.get("phase") == "post" and r.get("tool") == "get_values":
                rr = (r.get("input") or {}).get("range", "")
                if L.sheet_of(rr) == L.sheet_of(rng) and L.range_spec(rr) == L.range_spec(rng):
                    hit = r["response"]
                    break
        if hit is None:
            miss.append(rng)
        rbs.append(hit)
    return app, rbs, ([f"재조회 기록이 없다: {', '.join(miss)} 를 get_values 로 읽는다"] if miss else [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    a = ap.parse_args()
    try:
        plan = C.load_json(a.plan)
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"plan 파일을 읽지 못함: {e}", C.ABORT)
    app, rbs, errs = from_log(plan)
    if not errs:
        try:
            errs = verify(plan, app, rbs)
        except (ValueError, KeyError) as e:
            errs = [f"응답 형식 오류: {e}"]
    if errs:
        print(json.dumps({"ok": False, "retry": True, "errors": errs[:30]}, ensure_ascii=False, indent=1))
        raise SystemExit(C.RETRY)
    print(json.dumps({"ok": True, "sheet": plan["sheet"], "rows": plan["expect"]["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
