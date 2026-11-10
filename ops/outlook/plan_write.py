"""시트 기록 계획 (결정적). 7단계의 삭제·추가를 코드가 계산한다.

세션은 시트에서 읽은 응답을 그대로 파일로 저장해 넘기고, 이 스크립트가 만든 plan.json 의 요청을
그대로 실행한다. 지울 행, 추가할 값, 추가 후 시작 행, 8단계 재조회 범위가 모두 여기서 정해진다.

전망(rows.json): 오늘 작성일 행과 보관 기간이 지난 행을 지우고 오늘 행을 모두 추가한다.
주요일정(events_rows.json): ID 가 같은데 버전이 다른 행만 지우고 다시 추가, 새 ID 는 추가, 같으면 건너뛴다.
    기존 행은 지우지 않는다.

입력 파일은 커넥터 응답 원문이다.
  --cola  get_values('시트'!A:A) 응답 (주요일정은 '시트'!A:B). 시트가 없으면 생략하고 --new 를 준다.
  --head  get_values('시트'!1:1) 응답 (헤더 확인용, 생략 가능)
사용: python3 plan_write.py --data work/rows.json --sheet-id 123 --cola work/cola.json --head work/head.json \\
          --out work/plan.json
종료 코드: 0 계획 생성, 2 입력 오류(응답 형식이 다르거나 시트 이름 불일치)
"""
import argparse
import json
import re

import common as C
from verify_written import norm, norm_date, parse_range


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def check_resp(resp, sheet, what):
    """커넥터 응답 원문인지 확인한다: range 의 시트 이름이 대상과 같아야 한다."""
    if not isinstance(resp, dict) or "range" not in resp:
        raise ValueError(f"{what}: 커넥터 응답 원문이 아님 (range 필드 없음)")
    t, _, _ = parse_range(resp["range"])
    if t != sheet:
        raise ValueError(f"{what}: 시트 이름 {t} != {sheet}")
    return resp.get("values") or []


def deletes(sheet_id, rows):
    """1-based 행 번호들 → 아래쪽부터 지우는 deleteDimension 요청(연속 구간 묶음)."""
    out, rs = [], sorted(set(rows), reverse=True)
    i = 0
    while i < len(rs):
        hi = lo = rs[i]
        while i + 1 < len(rs) and rs[i + 1] == lo - 1:
            i += 1
            lo = rs[i]
        out.append({"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                                  "startIndex": lo - 1, "endIndex": hi}}})
        i += 1
    return out


def plan(data, sheet_id, cola=None, head=None, new=False):
    sheet, header = data["sheet"], data["header"]
    vals = [] if new else check_resp(cola, sheet, "cola")
    hv = check_resp(head, sheet, "head") if head is not None else None
    kill, append = [], list(data["rows"])
    events = data.get("kind") == "events"
    if events:
        have = {}
        for i, r in enumerate(vals[1:], start=2):
            if r and norm(r[0]):
                have.setdefault(norm(r[0]), []).append((i, norm(r[1]) if len(r) > 1 else ""))
        append = []
        vi = header.index("버전")
        for row in data["rows"]:
            got = have.get(row[0], [])
            if got and len(got) == 1 and got[0][1] == row[vi]:
                continue  # 같은 내용
            kill += [i for i, _ in got]
            append.append(row)
    else:
        for i, r in enumerate(vals[1:], start=2):
            d = norm_date(r[0]) if r else ""
            if d == data["date"] or (re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < data["delete_before"]):
                kill.append(i)
    last = max(len(vals), 1) - len(kill)
    ncol = len(header)
    hdr_update = None
    if new or not vals:
        hdr_update = {"range": f"'{sheet}'!A1:{col_letter(ncol)}1", "values": [header]}
    elif hv is not None and [norm(x) for x in (hv[0] if hv else [])][:ncol] != [norm(x) for x in header]:
        hdr_update = {"range": f"'{sheet}'!A1:{col_letter(ncol)}1", "values": [header]}
    start = last + 1
    end = start + len(append) - 1
    vc = 3 if events else 5
    res = {"sheet": sheet, "sheet_id": sheet_id, "kind": "events" if events else "outlook",
           "header_update": hdr_update, "requests": deletes(sheet_id, kill), "deleted_rows": len(kill),
           "append": {"range": f"'{sheet}'!A1", "values": append},
           "expect": {"start": start, "end": end, "rows": len(append), "cols": vc},
           "readback": ([f"'{sheet}'!A1:{col_letter(vc)}2"] if not events else [])
           + ([f"'{sheet}'!A{start}:{col_letter(vc)}{end}"] if append else []),
           "header": header}
    if not events:
        res["date"], res["delete_before"] = data["date"], data["delete_before"]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="rows.json 또는 events_rows.json")
    ap.add_argument("--sheet-id", type=int, required=True, help="get_spreadsheet 의 sheets.properties.sheetId")
    ap.add_argument("--cola", help="A열(주요일정은 A:B) get_values 응답 원문")
    ap.add_argument("--head", help="1행 get_values 응답 원문")
    ap.add_argument("--new", action="store_true", help="시트를 방금 만들었다(헤더부터 쓴다)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        data = C.load_json(a.data)
        cola = C.load_json(a.cola) if a.cola else None
        head = C.load_json(a.head) if a.head else None
        if cola is None and not a.new:
            raise ValueError("--cola 또는 --new 가 필요")
        res = plan(data, a.sheet_id, cola, head, a.new)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
        C.fail(f"기록 계획을 만들지 못함: {e}")
    C.save_json(a.out, res)
    print(json.dumps({"ok": True, "sheet": res["sheet"], "header_update": bool(res["header_update"]),
                      "delete_requests": len(res["requests"]), "deleted_rows": res["deleted_rows"],
                      "append_rows": res["expect"]["rows"], "start": res["expect"]["start"],
                      "readback": res["readback"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
