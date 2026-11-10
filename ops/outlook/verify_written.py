"""8단계 기록 검증 (결정적).

plan.json(plan_write.py 결과)과 커넥터 응답 원문을 대조한다. 응답은 세션이 가공하지 않고 그대로 저장한다.
  --append    append_values 응답 원문: updates.updatedRange 가 계획한 시작 행·행 수와 같아야 한다.
  --readback  plan.json 의 readback 범위마다 get_values 응답 원문 파일(순서대로).
              각 응답의 range 가 계획한 범위와 같아야 하고, 값이 기록하려던 값과 같아야 한다.
rows.json·plan.json 에서 만든 파일은 range 와 updatedRange 가 없어 통과하지 못한다.

전망: 헤더·첫 데이터 행(A1:E2)으로 헤더와 보관 기간을, 오늘 범위(A~E열)로 기록 값을 확인한다.
주요일정: 추가한 범위의 A~C열(ID·버전·구분)을 확인한다. 버전은 행 전체 내용의 해시다.

사용: python3 verify_written.py --plan work/plan.json --append work/append.json \\
          --readback work/rb1.json work/rb2.json
종료 코드: 0 일치, 1 불일치(7단계 재시도), 2 중단(파일 오류)
"""
import argparse
import json
import re

import common as C


def norm_date(v):
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else s


def norm(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def norm_cell(v, j=0):
    s = norm(v)
    if j == 0:
        return norm_date(s)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return str(float(s))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--append", help="append_values 응답 원문 (추가한 행이 없으면 생략)")
    ap.add_argument("--readback", nargs="*", default=[])
    a = ap.parse_args()
    try:
        plan = C.load_json(a.plan)
        app = C.load_json(a.append) if a.append else None
        rbs = [C.load_json(x) for x in a.readback]
        errs = verify(plan, app, rbs)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
        C.fail(f"파일을 읽지 못함: {e}", C.ABORT)
    if errs:
        print(json.dumps({"ok": False, "retry": True, "errors": errs[:30]}, ensure_ascii=False, indent=1))
        raise SystemExit(C.RETRY)
    print(json.dumps({"ok": True, "sheet": plan["sheet"], "rows": plan["expect"]["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
