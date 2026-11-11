"""시트 기록 계획 (결정적). 5단계의 삭제·추가를 코드가 계산한다.

시트 응답은 세션이 파일로 옮기지 않는다. 훅(.claude/hooks/sheets_guard.py)이 커넥터 응답 원문을
work/sheet_log.jsonl 에 남기고, 이 스크립트는 그 기록만 읽는다. 결과 plan.json 의 요청은 세션이
자리표시자(@plan)로 호출하면 훅이 그대로 채워 실행한다.

전망(rows.json): 오늘 작성일 행과 보관 기간이 지난 행을 지우고 오늘 행을 모두 추가한다.
주요일정(events_rows.json): ID 가 같은데 버전이 다른 행만 지우고 다시 추가, 새 ID 는 추가, 같으면 건너뛴다.
    기존 행은 지우지 않는다.

필요한 읽기(5-1, 이 순서와 상관없이 마지막 쓰기 이후의 것만 인정):
  get_spreadsheet, 전망 시트 'S'!A:A 와 'S'!1:1, 주요일정 시트 'S'!A:B
사용: python3 plan_write.py --data work/rows.json --out work/plan.json
종료 코드: 0 계획 생성, 1 읽기 부족(출력된 읽기를 하고 다시 실행), 2 입력 오류
"""
import argparse
import json
import re
import time

import common as C
import sheetlog as L
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
    vc = ncol  # 재조회 응답은 세션에 보이지 않으므로(훅이 요약) 모든 열을 대조한다
    res = {"id": f"{sheet}@{int(time.time() * 1000)}", "sheet": sheet, "sheet_id": sheet_id,
           "kind": "events" if events else "outlook",
           "header_update": hdr_update, "requests": deletes(sheet_id, kill), "deleted_rows": len(kill),
           "append": {"range": f"'{sheet}'!A1", "values": append},
           "expect": {"start": start, "end": end, "rows": len(append), "cols": vc},
           "readback": ([f"'{sheet}'!A1:{col_letter(vc)}2"] if not events else [])
           + ([f"'{sheet}'!A{start}:{col_letter(vc)}{end}"] if append else []),
           "header": header}
    if not events:
        res["date"], res["delete_before"] = data["date"], data["delete_before"]
    return res


def last_write_seq(log, sheet):
    """이 시트에 마지막으로 쓴 기록의 순번(없으면 -1). 그 뒤의 읽기만 인정한다."""
    s = -1
    for r in log:
        if r.get("phase") != "post" or r.get("tool") not in ("append_values", "update_values", "update_spreadsheet"):
            continue
        inp = r.get("input") or {}
        hit = L.sheet_of(inp.get("range")) == sheet if inp.get("range") else sheet in json.dumps(inp, ensure_ascii=False)
        if hit or r.get("tool") == "update_spreadsheet":
            s = r["seq"]
    return s


def find_read(log, sheet, spec, after):
    for r in reversed(log):
        if r["seq"] <= after:
            break
        if r.get("phase") == "post" and r.get("tool") == "get_values":
            rg = (r.get("input") or {}).get("range", "")
            if L.sheet_of(rg) == sheet and L.range_spec(rg) == spec:
                return r["response"]
    return None


def from_log(data, log=None):
    """훅 기록에서 sheetId·A열·1행을 찾아 계획을 만든다. 부족하면 ValueError(해야 할 읽기)."""
    log = L.log_read() if log is None else log
    sheet = data["sheet"]
    after = last_write_seq(log, sheet)
    meta = next((r["response"] for r in reversed(log) if r.get("phase") == "post" and r.get("tool") == "get_spreadsheet"
                 and r["seq"] > after), None)
    if meta is None:
        raise LookupError("get_spreadsheet(fields sheets.properties.sheetId, sheets.properties.title)를 읽는다")
    ids = {((s.get("properties") or {}).get("title")): (s.get("properties") or {}).get("sheetId")
           for s in meta.get("sheets") or []}
    if sheet not in ids:
        raise LookupError(f"시트 {sheet} 가 없다. update_spreadsheet addSheet 로 만든 뒤 get_spreadsheet 를 다시 읽는다")
    events = data.get("kind") == "events"
    spec = "A:B" if events else "A:A"
    cola = find_read(log, sheet, spec, after)
    if cola is None:
        raise LookupError(f"get_values '{sheet}'!{spec} 를 읽는다")
    head = None if events else find_read(log, sheet, "1:1", after)
    if not events and head is None:
        raise LookupError(f"get_values '{sheet}'!1:1 을 읽는다")
    cola.setdefault("values", [])
    if head is not None:
        head.setdefault("values", [])
    return plan(data, ids[sheet], cola, head)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="rows.json 또는 events_rows.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        data = C.load_json(a.data)
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"데이터 파일을 읽지 못함: {e}")
    if not L.load_guard():
        C.fail("guard 가 꺼져 있다. 4단계(draft.py merge·build_rows.py·build_events.py)를 다시 실행한다")
    try:
        res = from_log(data)
    except LookupError as e:
        print(json.dumps({"ok": False, "retry": True, "need": str(e)}, ensure_ascii=False))
        raise SystemExit(C.RETRY)
    except (ValueError, KeyError) as e:
        C.fail(f"기록 계획을 만들지 못함: {e}")
    C.save_json(a.out, res)
    print(json.dumps({"ok": True, "sheet": res["sheet"], "header_update": bool(res["header_update"]),
                      "deleted_rows": res["deleted_rows"], "append_rows": res["expect"]["rows"],
                      "start": res["expect"]["start"], "readback": res["readback"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
