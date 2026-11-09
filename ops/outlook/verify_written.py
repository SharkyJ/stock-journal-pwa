"""S7 기록 검증 (결정적).

시트를 다시 읽은 값(readback.json)과 rows.json 을 대조한다.

readback.json 형식 (커넥터가 돌려준 값을 그대로 저장한다. rows.json 에서 만들면 안 된다):
  {"values": [[헤더...], [행...], ...]}           시트 A1 부터 읽은 전체 값, 또는
  {"values": [[헤더], [오늘 행...]], "dates": [A열 전체 값]}
      시트가 길면 헤더(1행)와 오늘 기록한 행 범위만 읽고, 보관 기간 확인용으로 A열만 따로 읽는다.
  --cols N 을 주면 앞 N 열만 대조한다(토큰 절약: 긴 요약 열을 다시 읽지 않음).

사용: python3 verify_written.py --rows work/rows.json --readback work/readback.json
종료 코드: 0 일치, 1 불일치(S6 재시도), 2 중단(파일 오류)
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
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def verify(rows, readback, cols=None):
    errs = []
    vals = readback.get("values") or []
    if not vals:
        return ["시트 값이 비어 있음"]
    head = [norm(x) for x in vals[0]]
    n = cols or len(rows["header"])
    want_head = [norm(x) for x in rows["header"][:n]]
    if head[: len(want_head)] != want_head:
        errs.append(f"헤더 불일치: {head} != {want_head}")
    body = [r for r in vals[1:] if any(norm(x) for x in r)]
    today = [r for r in body if norm_date(r[0] if r else "") == rows["date"]]
    want = [r[:n] for r in rows["rows"]]
    if len(today) != len(want):
        errs.append(f"오늘({rows['date']}) 행 수 {len(today)} != {len(want)}")
    for i, (got, exp) in enumerate(zip(today, want)):
        g = [norm(x) for x in got] + [""] * (len(exp) - len(got))
        for j, e in enumerate(exp):
            gv = norm_date(g[j]) if j == 0 else g[j]
            if gv != norm(e):
                errs.append(f"{i + 1}번째 행 {rows['header'][j]}: '{gv[:40]}' != '{norm(e)[:40]}'")
    col_a = [d[0] if isinstance(d, list) else d for d in readback["dates"]] if readback.get("dates") is not None \
        else [r[0] if r else "" for r in body]
    old = sorted({norm_date(d) for d in col_a if re.fullmatch(r"\d{4}-\d{2}-\d{2}", norm_date(d))
                  and norm_date(d) < rows["delete_before"]})
    if old:
        errs.append(f"보관 기간({rows['delete_before']} 이전)이 지난 행이 남아 있음: {', '.join(old[:5])}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--readback", required=True)
    ap.add_argument("--cols", type=int, help="앞에서부터 이 열 수만 대조 (예: 5 = 작성일~전망 첫 열)")
    a = ap.parse_args()
    try:
        rows, rb = C.load_json(a.rows), C.load_json(a.readback)
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"파일을 읽지 못함: {e}", C.ABORT)
    errs = verify(rows, rb, a.cols)
    if errs:
        print(json.dumps({"ok": False, "retry": True, "errors": errs[:30]}, ensure_ascii=False, indent=1))
        raise SystemExit(C.RETRY)
    print(json.dumps({"ok": True, "sheet": rows["sheet"], "rows": len(rows["rows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
