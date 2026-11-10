#!/usr/bin/env python3
"""전망 파이프라인 시트 쓰기 보호 훅 (PreToolUse·PostToolUse, Google Sheets 커넥터).

guard(ops/outlook/work/guard.json)가 꺼져 있으면 아무것도 하지 않는다(일반 세션 영향 없음).
켜져 있으면:
- PreToolUse: 쓰기 도구는 plan.json 이 정한 요청만 통과시킨다. 세션은 값 자리에 [["@plan"]]
  (update_spreadsheet 는 [{"@plan": "delete"}])을 보내고, 훅이 plan.json 의 실제 값으로 바꾼다
  (updatedInput + allow). 다른 값·다른 시트·순서가 틀린 요청(삭제 전 추가, 두 번 실행)은 막는다.
- PostToolUse: 모든 시트 호출의 입력과 응답 원문을 work/sheet_log.jsonl 에 남긴다.
  기록 대상 시트를 읽은 큰 응답은 세션에 짧은 요약만 보여준다(토큰 절감, 응답 옮겨 적기 방지).
사용: python3 sheets_guard.py pre|post  (stdin 으로 훅 입력 JSON)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "ops" / "outlook"))
import sheetlog as L  # noqa: E402

WRITE = {"append_values", "update_values", "update_spreadsheet", "batch_clear_values", "insert_dimension",
         "update_formulas", "copy_sheet_to_another_spreadsheet", "batch_update_values"}


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def deny(reason):
    out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                "permissionDecisionReason": "[시트 보호] " + reason}})


def allow(new_input, plan_id, step, tool_use_id):
    L.log_append({"phase": "pre", "tool_use_id": tool_use_id, "plan_id": plan_id, "step": step})
    out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                "permissionDecisionReason": f"plan {plan_id} {step}",
                                "updatedInput": new_input}})


def is_ph(v):
    return v == [[L.PLACEHOLDER]] or v == L.PLACEHOLDER


def plan_for(sheet):
    for p, pl in L.plans():
        if pl.get("sheet") == sheet:
            return pl
    return None


def pre(h, g):
    kind = L.tool_kind(h.get("tool_name"))
    if kind not in WRITE:
        return
    ti = h.get("tool_input") or {}
    sheets = set(g["sheets"])
    log = L.log_read()
    tid = h.get("tool_use_id")
    if kind == "append_values":
        sh = L.sheet_of(ti.get("range"))
        if sh not in sheets:
            deny(f"{sh} 에는 쓸 수 없다. 기록 대상: {', '.join(sorted(sheets))}")
        pl = plan_for(sh)
        if not pl:
            deny(f"{sh} 의 plan.json 이 없다. plan_write.py 를 먼저 실행한다")
        if not is_ph(ti.get("values")):
            deny('append_values 의 values 는 [["@plan"]] 으로 보낸다. 값은 훅이 plan.json 에서 채운다')
        if not pl["append"]["values"]:
            deny("추가할 행이 없다. 이 append 는 건너뛴다")
        if pl.get("header_update") and not L.applied(log, pl["id"], "header"):
            deny("헤더 갱신(update_values)을 먼저 실행한다")
        if pl["requests"] and not L.applied(log, pl["id"], "delete"):
            deny("삭제(update_spreadsheet)가 먼저 끝나야 한다")
        if L.applied(log, pl["id"], "append"):
            deny("이 plan 의 추가는 이미 실행됐다. 다시 쓰려면 7단계 읽기부터 다시 하고 plan 을 새로 만든다")
        new = dict(ti, range=pl["append"]["range"], values=pl["append"]["values"])
        allow(new, pl["id"], "append", tid)
    if kind == "update_values":
        sh = L.sheet_of(ti.get("range"))
        pl = plan_for(sh) if sh in sheets else None
        if not pl or not pl.get("header_update"):
            deny(f"{sh} 에 허용된 update_values 가 없다(헤더 갱신만 허용)")
        if not is_ph(ti.get("values")):
            deny('update_values 의 values 는 [["@plan"]] 으로 보낸다')
        if L.applied(log, pl["id"], "header"):
            deny("헤더 갱신은 이미 실행됐다")
        hu = pl["header_update"]
        allow(dict(ti, range=hu["range"], values=hu["values"]), pl["id"], "header", tid)
    if kind == "update_spreadsheet":
        reqs = ti.get("requests") or []
        if reqs and all(isinstance(r, dict) and set(r) == {"addSheet"} for r in reqs):
            titles = [((r["addSheet"] or {}).get("properties") or {}).get("title") for r in reqs]
            if all(t in sheets for t in titles):
                return  # 기록 대상 시트 만들기는 허용
            deny(f"기록 대상이 아닌 시트는 만들 수 없다: {titles}")
        if not (len(reqs) == 1 and isinstance(reqs[0], dict) and set(reqs[0]) == {L.PLACEHOLDER}):
            deny('update_spreadsheet 는 requests=[{"@plan": "delete"}] 로만 보낸다(시트 만들기 addSheet 제외)')
        todo = [(pl["id"], pl) for _, pl in L.plans() if pl.get("sheet") in sheets and pl["requests"]
                and not L.applied(log, pl["id"], "delete")]
        if not todo:
            deny("실행할 삭제가 없다(이미 실행됐거나 지울 행이 없음). 이 호출은 건너뛴다")
        merged = [r for _, pl in todo for r in pl["requests"]]
        allow(dict(ti, requests=merged), ",".join(i for i, _ in todo), "delete", tid)
    deny(f"{kind} 는 전망 기록 중에 쓸 수 없다")


def summarize(kind, ti, resp):
    j = L.resp_json(resp)
    if kind == "get_values":
        vals = j.get("values") or []
        return {"range": j.get("range"), "rows": len(vals),
                "note": "원문은 sheet_log.jsonl 에 저장됨. 이 값을 옮겨 적지 않는다. plan_write.py·verify_written.py 가 읽는다."}
    if kind == "get_spreadsheet":
        return {"sheets": [((s.get("properties") or {}).get("title"), (s.get("properties") or {}).get("sheetId"))
                           for s in j.get("sheets") or []], "note": "원문은 sheet_log.jsonl 에 저장됨"}
    return None


def post(h, g):
    kind = L.tool_kind(h.get("tool_name"))
    if not kind:
        return
    ti, resp = h.get("tool_input") or {}, h.get("tool_response")
    tid = h.get("tool_use_id")
    pre_rec = next((r for r in reversed(L.log_read()) if r.get("phase") == "pre" and r.get("tool_use_id") == tid), {})
    for pid in str(pre_rec.get("plan_id") or "").split(",") or [""]:
        L.log_append({"phase": "post", "tool": kind, "tool_use_id": tid, "plan_id": pid or None,
                      "step": pre_rec.get("step"), "input": ti, "response": L.resp_json(resp)})
    big = len(json.dumps(resp, ensure_ascii=False)) > 1500
    sh = L.sheet_of(ti.get("range")) if ti.get("range") else None
    if big and (kind == "get_spreadsheet" or (kind == "get_values" and sh in set(g["sheets"]))):
        s = summarize(kind, ti, resp)
        if s is not None:
            text = json.dumps(s, ensure_ascii=False)
            new = [{"type": "text", "text": text}] if isinstance(resp, list) else \
                ({"content": [{"type": "text", "text": text}]} if isinstance(resp, dict) and "content" in resp else text)
            out({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": new}})


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        h = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    g = L.load_guard()
    if not g or not L.tool_kind(h.get("tool_name")):
        return
    if mode == "pre":
        pre(h, g)
    elif mode == "post":
        post(h, g)


if __name__ == "__main__":
    main()
