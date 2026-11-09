---
name: outlook-weekly
description: 주식일지 주간 전망 리포트를 작성해 Google Sheets에 기록한다. 예약 작업(월요일 아침)이 실행한다. 수치는 ops/outlook 스크립트가 수집·검증하고, Claude는 해석만 작성한다.
---

# 주간 전망 리포트

먼저 `.claude/skills/outlook-common.md`를 읽고 그 규칙을 따른다. 아래 단계를 순서대로 하고, 각 스크립트의 종료 코드를 확인한다.

예약 작업 프롬프트가 주는 값:
- `SPREADSHEET_ID`: 주식일지 스프레드시트 ID
- `TARGET_SHEET`: 기록할 시트 (`App_전망` 또는 병행 운영 중 `App_전망_v2`)

대상: 이번 주. 단기(1~2주), 중기(1~3개월), 장기(6개월 이상). 날짜는 Asia/Seoul 기준 `YYYY-MM-DD`.

## S0 입력 읽기 (Google Sheets 커넥터)

`rm -rf ops/outlook/work && mkdir -p ops/outlook/work` 후 `ops/outlook/work/input.json`을 만든다. 형식과 읽는 범위는 `outlook-daily` 스킬의 S0과 같고, 다음만 다르다.

- `"mode": "weekly"`
- `signals`: 최근 14일
- `weekly`: 넣지 않는다(빈 배열)
- `fin`: 최근 45일 (같음)

시트에 접근할 수 없으면 아무것도 쓰지 말고 무엇이 막혔는지 보고하고 끝낸다.

## S1 시세 수집, S2 뉴스 수집

```bash
cd ops/outlook
python3 collect_market.py --input work/input.json --out work/market.json
python3 collect_news.py --input work/input.json --out work/news.json
```

둘 중 하나라도 종료 코드 2이면 중단한다. 주간 뉴스는 최근 14일 범위로 수집된다.

## S3 보완 조사 (웹 검색)

공통 규칙의 용도 제한 안에서 검색해 `work/extra_sources.json`을 쓴다. 주간에서 확인할 것:
- 이번 주 주요 일정: 물가·고용 지표, FOMC 등 통화정책, 주요 실적 발표
- 종목별 다음 실적 발표일(`market.json`의 `next_earnings`가 없을 때), 최근 1~2주 목표주가·투자의견 변화 뉴스
- 야간선물은 확인하지 않는다.

## S4 해석 작성

`work/market.json`, `work/news.json`, `work/extra_sources.json`, `work/input.json`만 근거로 `work/analysis.json`을 쓴다.

```json
{"rows": [
  {"key": "market", "s": "중립", "m": "약강세", "l": "강세", "summary": "2~3문장",
   "event": "이번 주 일정·리스크(날짜 포함)", "nums": ["M1", "M5"], "src": ["N3", "W1"]},
  {"key": "NASDAQ:MU", "s": "약강세", "m": "강세", "l": "강세", "summary": "...",
   "event": "다음 실적 2026-12-17 · 앱 신호: 11/03 돌파 매수 신호", "nums": ["T12"], "src": ["N14"]}
]}
```

- 행: `market` 1개, 그리고 `input.json`의 보유·관심 종목마다 1개(key = 티커). `night` 행은 없다.
- `summary` 2~3문장(420자 이내), `event` 300자 이내, `src` 최대 3개.
- 공통 규칙의 해석 체크리스트 중 주간 필수 항목(컨센서스 변화, 밸류에이션 포함)을 확인한다.
- 최근 14일 앱 신호가 있는 종목은 그 신호가 뉴스·실적과 맞는지 확인하고 `event`에 "앱 신호: 날짜 전략 신호" 형식으로 적는다.
- 레버리지·인버스 ETF는 기초자산 기준으로 전망하고, 요약에 변동성 손실 위험을 짧게 언급한다. 일반 ETF는 구성 업종·지수 흐름 기준이다.

## S5 검증·행 조립

```bash
python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet "$TARGET_SHEET"
```

종료 코드 1이면 errors를 고쳐 1회만 다시 실행한다. 다시 실패하면 쓰지 않고 중단한다.

## S6 시트 기록, S7 기록 검증

1. 공통 규칙의 "시트 쓰기" 순서대로 `TARGET_SHEET`에 기록한다.
2. 공통 규칙의 "기록 검증용 재조회"대로 다시 읽어 `work/readback.json`에 저장한다. 행이 적으면 `'TARGET_SHEET'!A1:J` 전체, 많으면 1행과 오늘 행 범위(A:J) 그리고 A열 전체. 읽은 값을 그대로 저장한다.
3. 검증한다.

```bash
python3 verify_written.py --rows work/rows.json --readback work/readback.json
```

종료 코드 1이면 S6부터 다시(최대 2회). 그래도 실패하면 오늘 행을 지우고 중단한다.

## 마무리

S7이 0일 때만 성공으로 보고한다. 2~3줄로 남긴다: 시장 국면 한 줄, 눈에 띄는 종목 2~3개와 이유. 주식일지 앱의 분석 → 전망 탭에서 확인할 수 있다고 덧붙인다. 실패했으면 실패한 단계와 errors를 그대로 보고한다.
