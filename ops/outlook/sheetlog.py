"""시트 커넥터 호출 기록과 쓰기 보호(guard) 공용 모듈. 표준 라이브러리만 쓴다.

.claude/hooks/sheets_guard.py(PreToolUse·PostToolUse 훅)가 시트 커넥터 호출의 입력과 응답 원문을
work/sheet_log.jsonl 에 남긴다. plan_write.py·verify_written.py 는 세션이 만든 파일이 아니라
이 기록만 읽는다. 세션은 응답을 옮겨 적지 않고, 쓰기 값은 훅이 plan.json 에서 채운다.

guard(work/guard.json)는 1단계(prepare.py begin)가 입력 수집 단계로 켜고, 4단계(build_rows.py·build_events.py)가 기록 단계로 바꾼다. 켜져 있는 동안에는
등록된 시트에만, plan.json 이 정한 요청만 쓸 수 있다. 2시간이 지나면 꺼진 것으로 본다.
"""
import json
import re
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent / "work"
GUARD = WORK / "guard.json"
LOG = WORK / "sheet_log.jsonl"
TTL = 2 * 3600
PLACEHOLDER = "@plan"
TOOL_RE = re.compile(r"^mcp__.*sheets.*__(\w+)$", re.I)


def tool_kind(name):
    m = TOOL_RE.match(str(name or ""))
    return m.group(1) if m else None


def parse_range(r):
    """"'App_일간전망'!A297:E318" → (시트, 시작 행, 끝 행). 'S'!A:A → (S, None, None)."""
    m = re.fullmatch(r"'?(.+?)'?!\$?([A-Z]*)\$?(\d*)(?::\$?([A-Z]*)\$?(\d*))?", str(r).strip())
    if not m:
        raise ValueError(f"범위 형식 오류: {r}")
    t = m.group(1).replace("''", "'")
    a = int(m.group(3)) if m.group(3) else None
    b = int(m.group(5)) if m.group(5) else a
    return t, a, b


def range_spec(r):
    """시트 이름을 뺀 범위 문자열(대문자, $ 제거). 예: 'S'!a:a → A:A."""
    return str(r).split("!", 1)[-1].replace("$", "").upper()


def sheet_of(r):
    try:
        return parse_range(r)[0]
    except ValueError:
        return None


def resp_json(resp, _depth=0):
    """MCP 응답(문자열, content 배열, {content: [...]}, {content: {...}}, dict)을 JSON 객체로.

    텍스트 안에 다시 {"content": {...}} 래퍼가 있는 경우도 벗긴다."""
    if isinstance(resp, dict) and isinstance(resp.get("content"), list):
        resp = resp["content"]
    if isinstance(resp, list):
        resp = "".join(x.get("text", "") for x in resp if isinstance(x, dict))
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError:
            return {"_text": resp}
    if (_depth < 3 and isinstance(resp, dict) and set(resp) <= {"content", "isError"}
            and isinstance(resp.get("content"), (dict, str, list))):
        return resp_json(resp["content"], _depth + 1)
    return resp if isinstance(resp, dict) else {"_raw": resp}


def load_guard():
    try:
        g = json.loads(GUARD.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - g.get("started", 0) > TTL:
        return None
    return g


def guard_add(sheet):
    """4단계에서 기록 대상 시트를 등록하고 guard 를 켠다."""
    WORK.mkdir(parents=True, exist_ok=True)
    g = load_guard() or {"started": time.time(), "sheets": []}
    g["phase"] = "write"
    if sheet not in g["sheets"]:
        g["sheets"].append(sheet)
    GUARD.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return g


def log_append(rec):
    WORK.mkdir(parents=True, exist_ok=True)
    rec["ts"] = time.time()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_read():
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for i, ln in enumerate(lines):
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        o["seq"] = i
        if "response" in o:
            o["response"] = resp_json(o["response"])
        out.append(o)
    return out


def plans():
    """work/ 의 plan*.json 들 (plan_write.py 결과)."""
    out = []
    for p in sorted(WORK.glob("*plan.json")):
        try:
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def applied(log, plan_id, step):
    return any(r.get("phase") == "post" and r.get("plan_id") == plan_id and r.get("step") == step for r in log)
