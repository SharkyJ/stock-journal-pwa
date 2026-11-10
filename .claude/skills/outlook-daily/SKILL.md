---
name: outlook-daily
description: 주식일지 일간 전망 리포트를 작성해 Google Sheets에 기록한다. 예약 작업(평일 아침)이 실행한다. 수치는 ops/outlook 스크립트가 수집·검증하고, Claude는 해석만 작성한다.
---

# 일간 전망 리포트

`.claude/skills/outlook-common.md`의 규칙을 따른다. 아래 순서를 그대로 실행하고, 표시된 대로 **한 턴에 병렬로** 호출한다. 파일을 둘러보거나 JSON 원본을 열지 않는다.

예약 작업 프롬프트가 주는 값: `SPREADSHEET_ID`, `TARGET_SHEET` (`App_일간전망` 또는 `App_일간전망_v2`), `EVENTS_SHEET` (없으면 `App_주요일정`). 날짜는 Asia/Seoul `YYYY-MM-DD`.

## 1. 입력 읽기 — 한 턴, 병렬 get_values

| 범위 | 쓰는 값 |
|---|---|
| `'국장투자'!A5:D`, `'미장투자'!A5:D` | B 종목명, C 티커, D 보유 수량. 수량 > 0 만 `holdings` |
| `'App_관심종목'!A2:C` | A 티커, B 종목명 → `watch` |
| `'App_시그널'!B2:H` | B 신호일, D 티커, G 전략, H 신호. 최근 3거래일만 `signals` |
| `'App_전망'!A2:G` | 작성일이 가장 최근인 행만 `weekly` (티커, 종목명, 단기, 중기, 장기) |
| `'App_재무추이'!B2:D`, `'App_재무추이'!L2:P` | 같은 행끼리 합쳐 B 일자, D 티커, L 올해 회계연도 말, M 올해 추정 EPS, N 내년 회계연도 말, O 내년 추정 EPS, P 목표주가. 국장 보유·관심 종목의 최근 45일 행을 **모두** `fin`에 넣는다(최신 행만 넣으면 28~45일 전 기준이 없어 컨센서스 변화가 계산되지 않는다) |

없는 시트는 빈 값으로 둔다. 시트에 접근할 수 없으면 아무것도 쓰지 말고 막힌 내용을 보고하고 끝낸다.

## 2. input.json 쓰기 — 한 턴

`ops/outlook/work/input.json` (폴더가 없으면 Write가 만든다):

```json
{"date": "2026-11-10", "mode": "daily",
 "holdings": [{"ticker": "KRX:005930", "name": "삼성전자"}], "watch": [{"ticker": "NASDAQ:MU", "name": "마이크론"}],
 "signals": [{"date": "2026-11-07", "ticker": "KRX:005930", "strategy": "추세추종", "signal": "매도·경계"}],
 "weekly": [{"ticker": "", "name": "시장 전체", "s": "중립", "m": "약강세", "l": "강세"}],
 "fin": [{"date": "2026-10-08", "ticker": "KRX:005930", "f0end": "2026-12-31", "f0eps": 40000, "f1end": "2027-12-31", "f1eps": 52000, "target": 400000}]}
```

보유 수량·금액은 넣지 않는다. 날짜는 `YYYY-MM-DD` 문자열, 숫자는 숫자.

## 3. 수집 — 한 턴

```bash
cd ops/outlook && python3 collect.py --input work/input.json --work work
```

시세·뉴스·경제 캘린더(TradingView, 미국·한국)·보유 종목 실적 일정(Nasdaq, Worker)을 모으고 요약을 출력한다. 출력되는 요약(ID 포함)만 근거로 쓴다. 종료 코드 2면 중단하고 보고한다. 경제 캘린더·실적 수집이 실패해도 요약에 상태가 나오고 계속 진행한다.

## 4. 보완 조사 — 한 턴, 병렬 웹 검색 최대 3개

요약의 "경제 캘린더"가 `ok`면 미국·한국 지표 일정은 검색하지 않는다. 검색 대상: 요약에 없는 정책 일정(연준·한은 위원 발언, 정부 발표), 오늘 실적 발표 중 요약에 없는 것, 요약의 뉴스로 부족한 핵심 종목 맥락. 경제 캘린더가 `ok`가 아니면 오늘 경제지표 일정을 먼저 찾는다. 페이지 열람(WebFetch)은 날짜·수치 확인이 꼭 필요할 때만 2회 이내.

## 5. 해석 작성 — 한 턴, 두 파일 병렬 Write

- `ops/outlook/work/extra_sources.json`: 공통 규칙 형식. 쓸 것이 없으면 `{"items": []}`.
- `ops/outlook/work/analysis.json`:

```json
{"rows": [
  {"key": "market", "outlook": "약강세", "summary": "4~8문장", "checkpoint": "오늘 볼 가격대·일정", "nums": ["M3", "M9"], "src": ["N2", "W1"]},
  {"key": "KRX:005930", "outlook": "중립", "summary": "...", "checkpoint": "지지 179,200 · 저항 217,655 · 앱 신호: 11/06 추세추종 매도·경계", "nums": ["T11"], "src": ["N11"]}
]}
```

- 행: `market` 1개 + 요약의 종목(T..)마다 1개(key = 티커). 기초자산(U..)은 행이 아니다.
- 시장 전체 전망에 미국 지수선물 흐름, 거시 기조, 오늘 고영향 지표(C..)와 최근 발표 결과를 반영한다. 일정·발표값은 `nums`에 C.. ID를 넣고 인용한다.
- 종목 행은 요약의 "보유 종목 실적 일정"(E..)을 확인한다. 실적이 가까우면 날짜·컨센서스 EPS를 `nums`에 E.. ID로 인용하고, 직전 분기 결과(예상 상회·하회)도 맥락으로 쓴다. `[mismatch]`면 일정이 불확실하다고 쓴다.
- "오늘의 핵심 신호"의 항목은 해당 행에서 빠짐없이 다룬다. 뉴스 태그의 호재·악재 후보는 키워드 기반이므로 제목을 읽고 판단한다.
- `summary` 4~8문장(1000자 이내, 마침표 8개 이하), `checkpoint` 300자 이내, `src` 최대 3개.
- `summary` 구성: (1) 오늘 판단과 핵심 근거(간밤 흐름·뉴스), (2) 가격 위치(이동평균 대비, 20일 범위), (3) 컨센서스·밸류에이션 또는 이벤트, (4) 앱 신호와의 정합성. 근거가 없는 항목은 건너뛰되 가능한 정보를 최대한 담는다(3문장 이하로 끝내지 않는다).
- 문장 수는 마침표로 센다. `앱 신호: …` 같은 꼬리표는 마침표 없이 `checkpoint`에 둔다.
- 레버리지 상품은 기초자산 기준. 변동성 손실 위험은 `checkpoint`에 짧게 쓴다(요약 문장 수 절약).

## 6. 검증·행 조립 — 한 턴

```bash
cd ops/outlook && python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --calendar work/calendar.json --events work/events.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet "$TARGET_SHEET" \
  && python3 build_events.py --calendar work/calendar.json --events work/events.json --out work/events_rows.json
```

종료 코드 1이면 errors에 나온 행만 Edit로 고치고 1회만 다시 실행한다. 다시 실패하면 쓰지 않고 중단한다.

## 7. 기록 — 4턴. 지울 행과 추가 위치는 `plan_write.py`가 정한다

공통 규칙 "시트 쓰기"를 따른다. 커넥터 응답은 **받은 그대로** 파일에 저장한다(가공·요약·재구성 금지).

1. 한 턴, 병렬 호출: `get_spreadsheet`(fields `sheets.properties.sheetId`, `sheets.properties.title`), `get_values` `'TARGET_SHEET'!A:A`, `'TARGET_SHEET'!1:1`, `'EVENTS_SHEET'!A:B`. 없는 시트는 `update_spreadsheet`의 `addSheet`로 만들고 아래에서 `--new`를 쓴다.
2. 한 턴: 응답을 그대로 `work/cola.json`, `work/head.json`, `work/ev_cola.json`에 Write(병렬)하고 계획을 만든다.

```bash
cd ops/outlook && python3 plan_write.py --data work/rows.json --sheet-id <TARGET sheetId> --cola work/cola.json \
  --head work/head.json --out work/plan.json \
  && python3 plan_write.py --data work/events_rows.json --sheet-id <EVENTS sheetId> --cola work/ev_cola.json \
  --out work/ev_plan.json
```

3. 한 턴, 병렬 호출: 두 plan의 `header_update`가 있으면 `update_values`로 쓴다. 두 plan의 `requests`를 합쳐(전망 먼저) **한 번의** `update_spreadsheet`로 보낸다(비어 있으면 건너뛴다).
4. 한 턴, 병렬 호출: `plan.json`의 `append.values`를 `append_values`(range = `append.range`)로 추가한다. `ev_plan.json`도 `append.values`가 비어 있지 않으면 같은 방식으로 추가한다. 값은 파일에 있는 그대로 쓴다.

## 8. 기록 검증 — 2턴

1. 한 턴, 병렬 호출: 두 plan의 `readback` 범위를 순서대로 `get_values`로 읽는다.
2. 한 턴: 4번의 append 응답과 재조회 응답을 **그대로** `work/append.json`, `work/rb1.json`, `work/rb2.json`, `work/ev_append.json`, `work/ev_rb1.json`에 Write(병렬)하고 검증한다.

```bash
cd ops/outlook && python3 verify_written.py --plan work/plan.json --append work/append.json \
  --readback work/rb1.json work/rb2.json \
  && python3 verify_written.py --plan work/ev_plan.json --append work/ev_append.json --readback work/ev_rb1.json
```

`ev_plan.json`에 추가한 행이 없으면 두 번째 명령은 `--append`·`--readback` 없이 실행한다. 응답 파일을 `rows.json`·`plan.json`에서 만들면 `range`·`updatedRange`가 없어 검증이 실패한다. 종료 코드 1이면 7-1부터 다시(최대 2회). 그래도 실패하면 오늘 전망 행을 지우고 중단한다(주요일정은 지우지 않는다).

## 마무리

8단계가 0일 때만 성공으로 보고한다. 3줄: 오늘 시장 분위기, 주목할 종목 1~2개, 주요일정 반영 행 수(추가·교체). "주식일지 앱의 분석 → 전망 → 일간에서 확인할 수 있습니다." 실패했으면 실패한 단계와 errors를 그대로 보고한다.
