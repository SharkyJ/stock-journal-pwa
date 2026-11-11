---
name: outlook-daily
description: 주식일지 일간 전망 리포트를 작성해 Google Sheets에 기록한다. 예약 작업(평일 아침)이 실행한다. 입력·수치·전망 초안·사실 문장은 ops/outlook 스크립트가 만들고, Claude는 행별 lead(해석 서술)를 쓴다. 수치는 코드가 지표 표로 기록한다.
---

# 일간 전망 리포트

`.claude/skills/outlook-common.md`의 규칙을 따른다. 아래 순서를 그대로 실행하고, 표시된 대로 **한 턴에 병렬로** 호출한다. 파일을 둘러보거나 JSON 원본을 열지 않는다.

예약 작업 프롬프트가 주는 값: `SPREADSHEET_ID`, `TARGET_SHEET` (`App_일간전망` 또는 `App_일간전망_v2`), `EVENTS_SHEET` (없으면 `App_주요일정`). 날짜는 Asia/Seoul `YYYY-MM-DD`.

## 1. 입력 읽기 — 3턴. 값은 보지 않는다

1. 한 턴: `cd ops/outlook && python3 prepare.py begin --mode daily` (이전 기록을 지우고 입력 수집 보호를 켠다).
2. 한 턴, 병렬 get_values: 출력의 `read_in_one_turn` 범위 전부(`SPREADSHEET_ID`). 응답은 훅이 저장하고 행 수만 보여준다. 값을 옮겨 적지 않는다.
3. 한 턴:

```bash
cd ops/outlook && python3 prepare.py input --mode daily --out work/input.json \
  && python3 collect.py --input work/input.json --work work && python3 draft.py brief --work work
```

- `prepare.py`가 보유(수량 > 0)·관심·최근 3거래일 신호·최신 주간 전망·재무추이 45일을 골라 `input.json`을 만든다. 날짜는 코드가 오늘(KST)로 정한다.
- 읽기 오류(시트 없음)가 난 범위는 `--missing '범위' ...`로 넘긴다. 종료 코드 1이면 `need`의 범위를 읽고 3만 다시 한다. 종료 코드 2면 쓰지 않고 중단·보고한다.
- `collect.py`(시세·뉴스·경제 캘린더·실적 일정)가 종료 코드 2면 중단·보고한다.
- `draft.py brief`가 **작성 과제**를 출력한다: 행마다 규칙 점수로 정한 초안 전망과 그 근거, 코드가 쓴 사실 문장, 관련 뉴스 제목(ID). 이 출력만 읽는다.

## 2. 보완 조사 — 선택, 한 턴, 병렬 웹 검색 최대 2개

작성 과제만으로 lead를 쓸 수 없는 행(관련 뉴스 없음·급변 원인 불명)이 있거나 경제 캘린더가 `ok`가 아닐 때만 한다. 결과는 공통 규칙 형식으로 `ops/outlook/work/extra_sources.json`에 쓰고 그 행의 `src`에 W.. ID로 넣는다. 하지 않으면 파일을 쓰지 않는다(4단계가 빈 파일을 만든다).

## 3. 해석 작성 — 한 턴, Write 한 번

`ops/outlook/work/notes.json`:

```json
{"rows": {
  "market": {"lead": "간밤 반도체 반등이 위험 선호를 되살렸지만 금리 부담이 남아 상승 폭은 제한될 수 있다.", "src": ["N2"]},
  "KRX:005930": {"lead": "...", "src": ["N16"], "adjust": -1, "why": "N17 공시로 단기 부담"}
}}
```

- 작성 과제의 모든 행(`market`과 각 티커)에 `lead`를 쓴다. **지표 표·체크포인트·출처 URL·전망 값은 코드가 채운다.** 표(시세·등락, 20·60·200일선 대비, 20일 범위, EPS 추정치·목표주가 변화, PER, 실적 일정·직전 결과, 고영향 지표)는 시트 `지표` 열에 그대로 기록되고 앱이 표로 보여준다.
- `lead`: **3~6문장 권장, 최대 8문장**(넘으면 4단계에서 거부). 표를 반복해 나열하지 말고 해석한다: (1) 오늘 판단과 핵심 이유(간밤 흐름·뉴스), (2) 표의 가격 위치·추세가 뜻하는 것, (3) 컨센서스·실적 일정 등 이벤트의 의미, (4) 앱 신호와의 정합성, (5) 주의할 위험. 근거 없는 항목은 건너뛴다. 숫자·날짜는 그 행의 표·뉴스 제목에 있는 값만 쓴다(다른 값은 검증에서 거부). 레버리지 상품은 기초자산 기준으로 쓴다. 뉴스 태그의 호재·악재 후보는 키워드 기반이므로 제목을 읽고 판단한다. 관련 뉴스가 없으면 "특별한 뉴스는 없다"고 쓴다.
- `src`: lead의 근거 뉴스(그 행이나 `market` 행에 나온 N..) 또는 보완 출처(W..) ID, 최대 4개. 비우면 코드가 관련 뉴스 상위 2개를 넣는다.
- `adjust`: 초안 전망을 바꿀 분명한 근거가 뉴스에 있을 때만 `-1` 또는 `1`, 그리고 `why`에 근거 ID. 그 외에는 생략한다.
- "핵심 신호"의 항목은 해당 행의 lead에서 다룬다.

## 4. 합치기·검증·행 조립 — 한 턴

```bash
cd ops/outlook && python3 draft.py merge --work work \
  && python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --calendar work/calendar.json --events work/events.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet "$TARGET_SHEET" \
  && python3 build_events.py --calendar work/calendar.json --events work/events.json --out work/events_rows.json \
  --sheet "${EVENTS_SHEET:-App_주요일정}"
```

종료 코드 1이면 errors에 나온 행의 `notes.json`만 Edit로 고치고 1회만 다시 실행한다. 다시 실패하면 쓰지 않고 중단한다. 통과하면 시트 쓰기 보호(훅)가 기록 단계로 바뀐다. 생성된 JSON은 열어 보거나 출력하지 않는다.

## 5. 기록 — 4턴. 값은 훅이 채운다

공통 규칙 "시트 쓰기"를 따른다. 쓰기 호출의 값 자리에는 **자리표시자만** 넣는다. 실제 값은 훅이 `plan.json`에서 채우고, 다른 값·다른 시트·잘못된 순서는 훅이 막는다. 시트 응답은 훅이 원문을 저장하므로 파일로 옮기지 않는다.

1. 한 턴, 병렬 호출: `get_spreadsheet`(fields `sheets.properties.sheetId`, `sheets.properties.title`), `get_values` `'TARGET_SHEET'!A:A`, `'TARGET_SHEET'!1:1`, `'EVENTS_SHEET'!A:B`.
2. 한 턴: `cd ops/outlook && python3 plan_write.py --data work/rows.json --out work/plan.json && python3 plan_write.py --data work/events_rows.json --out work/ev_plan.json`
   - 종료 코드 1이면 `need`에 적힌 읽기를 하고 다시 실행한다. 시트가 없다고 나오면 `update_spreadsheet`에 `addSheet`(그 시트 이름만)로 만들고 5-1부터 다시 한다.
3. 한 턴, 병렬 호출(해당하는 것만):
   - 두 plan 중 `header_update`가 true인 시트: `update_values`(range `'시트'!A1`, values `[["@plan"]]`)
   - `deleted_rows`가 0보다 큰 plan이 있으면: `update_spreadsheet`(requests `[{"@plan": "delete"}]`) **한 번**. 두 plan의 삭제를 훅이 합쳐 보낸다.
4. 한 턴, 병렬 호출: `append_rows`가 0보다 큰 시트마다 `append_values`(range `'시트'!A1`, values `[["@plan"]]`).

훅이 거부하면 거부 사유대로 한다. 값을 직접 넣어 다시 시도하지 않는다.

## 6. 기록 검증 — 2턴

1. 한 턴, 병렬 호출: 두 plan 출력의 `readback` 범위를 모두 `get_values`로 읽는다(응답은 훅이 요약해 보여준다).
2. 한 턴: `cd ops/outlook && python3 verify_written.py --plan work/plan.json && python3 verify_written.py --plan work/ev_plan.json`

검증기는 훅이 저장한 응답 원문만 읽는다(세션이 만든 파일은 쓰지 않는다). 종료 코드 1이면 errors대로 한다: 재조회가 없다는 오류면 6-1만 다시, 값·위치 불일치면 5-1부터 다시(최대 2회). 그래도 실패하면 중단하고 errors를 보고한다.

## 마무리

6단계가 0일 때만 성공으로 보고한다. 3줄: 오늘 시장 분위기, 주목할 종목 1~2개, 주요일정 반영 행 수(추가·교체). "주식일지 앱의 분석 → 전망 → 일간에서 확인할 수 있습니다." 실패했으면 실패한 단계와 errors를 그대로 보고한다.
