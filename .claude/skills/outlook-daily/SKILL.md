---
name: outlook-daily
description: 주식일지 일간 전망 리포트를 작성해 Google Sheets에 기록한다. 예약 작업(평일 아침)이 실행한다. 수치는 ops/outlook 스크립트가 수집·검증하고, Claude는 해석만 작성한다.
---

# 일간 전망 리포트

먼저 `.claude/skills/outlook-common.md`를 읽고 그 규칙을 따른다. 아래 단계를 순서대로 하고, 각 스크립트의 종료 코드를 확인한다.

예약 작업 프롬프트가 주는 값:
- `SPREADSHEET_ID`: 주식일지 스프레드시트 ID
- `TARGET_SHEET`: 기록할 시트 (`App_일간전망` 또는 병행 운영 중 `App_일간전망_v2`)

대상: 오늘 하루(국장 장중, 미장은 오늘 밤 장). 날짜는 Asia/Seoul 기준 `YYYY-MM-DD`.

## S0 입력 읽기 (Google Sheets 커넥터)

`rm -rf ops/outlook/work && mkdir -p ops/outlook/work` 후, 아래를 읽어 `ops/outlook/work/input.json`에 저장한다.

- `'국장투자'!A3:L`, `'미장투자'!A3:L`: 3행 헤더, 4행 합계, 5행부터 종목. 열은 종목 탭, 종목명, 티커(`KRX:005930`, `NASDAQ:MU` 형식), 보유 수량, … 보유 수량이 0보다 큰 종목만 `holdings`.
- `'App_관심종목'!A2:E` (있으면): 티커, 종목명, 시장(국장/미장). `watch`.
- `'App_시그널'!A2:L` (있으면, 읽기 전용): B 신호일, D 티커, G 전략, H 신호. 최근 3거래일만 `signals`.
- `'App_전망'!A2:J` (있으면, 읽기 전용): 작성일이 가장 최근인 행만 `weekly` (티커, 종목명, 단기, 중기, 장기).
- `'App_재무추이'!A2:S` (있으면, 읽기 전용): B 일자, D 티커, L 올해 회계연도 말, M 올해 추정 EPS, N 내년 회계연도 말, O 내년 추정 EPS, P 목표주가. 보유·관심 종목의 최근 45일 행만 `fin`.

```json
{"date": "2026-11-10", "mode": "daily",
 "holdings": [{"ticker": "KRX:005930", "name": "삼성전자", "market": "KR"}],
 "watch": [{"ticker": "NASDAQ:MU", "name": "마이크론", "market": "US"}],
 "signals": [{"date": "2026-11-07", "ticker": "KRX:005930", "strategy": "추세추종", "signal": "매도·경계"}],
 "weekly": [{"ticker": "", "name": "시장 전체", "s": "중립", "m": "약강세", "l": "강세"}],
 "fin": [{"date": "2026-10-08", "ticker": "KRX:005930", "f0end": "2026-12-31", "f0eps": 40000,
          "f1end": "2027-12-31", "f1eps": 52000, "target": 400000}]}
```

- 날짜는 모두 `YYYY-MM-DD` 문자열, 숫자는 숫자로 넣는다. 보유 수량·금액은 넣지 않는다.
- 시트에 접근할 수 없으면 아무것도 쓰지 말고 무엇이 막혔는지 보고하고 끝낸다.

## S1 시세 수집, S2 뉴스 수집

```bash
cd ops/outlook
python3 collect_market.py --input work/input.json --out work/market.json
python3 collect_news.py --input work/input.json --out work/news.json
```

둘 중 하나라도 종료 코드 2이면 중단한다. `not_ok` 항목은 그대로 두고 진행한다(인용 규칙은 공통 규칙 참고).

## S3 보완 조사 (웹 검색)

공통 규칙의 용도 제한 안에서 검색해 `work/extra_sources.json`을 쓴다. 일간에서 확인할 것:
- 오늘 국내외 경제지표 발표, 실적 발표, 정책 이벤트
- 코스피200 야간선물: 한국거래소 야간장(18:00~다음 날 06:00) 마감가와 직전 주간장(15:45) 종가. 월요일 아침은 토요일 06:00 마감분. `kind: night_futures`로 기록한다.
  참고: https://www.prekospi.com/kospi200-night , https://hlkr.co.kr/futures , 직전 주간 종가 https://markets.hankyung.com/indices/kospi-future , "코스피200 야간선물 마감" 뉴스.
- `market.json`·`news.json`으로 부족한 종목별 최근 24시간 공시·뉴스 맥락

## S4 해석 작성

`work/market.json`, `work/news.json`, `work/extra_sources.json`, `work/input.json`만 근거로 `work/analysis.json`을 쓴다.

```json
{"rows": [
  {"key": "market", "outlook": "약강세", "summary": "1~2문장", "checkpoint": "오늘 볼 가격대·일정",
   "nums": ["M3", "M9"], "src": ["N2", "W1"]},
  {"key": "night", "checkpoint": "야간선물이 국장 시초가에 주는 의미, 확인되면 야간장 고가·저가", "nums": [], "src": ["W2"]},
  {"key": "KRX:005930", "outlook": "중립", "summary": "...", "checkpoint": "지지 179,200 · 저항 217,655 · 앱 신호: 11/06 추세추종 매도·경계",
   "nums": ["T11"], "src": ["N11"]}
]}
```

- 행: `market` 1개, `night` 1개(체크포인트만), 그리고 `input.json`의 보유·관심 종목마다 1개(key = 티커). 빠지거나 더 있으면 검증에서 실패한다.
- 시장 전체 전망에는 야간선물과 미국 지수선물 흐름도 반영한다. 야간선물 행의 전망과 요약은 코드가 만든다.
- `summary` 1~2문장(220자 이내), `checkpoint` 200자 이내, `src` 최대 2개.
- 레버리지 상품은 `market.json`의 `under_id` 항목(기초자산)을 기준으로 쓴다.

## S5 검증·행 조립

```bash
python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet "$TARGET_SHEET"
```

종료 코드 1이면 errors를 고쳐 1회만 다시 실행한다. 다시 실패하면 쓰지 않고 중단한다.

## S6 시트 기록, S7 기록 검증

1. 공통 규칙의 "시트 쓰기" 순서대로 `TARGET_SHEET`에 기록한다.
2. `'TARGET_SHEET'!A1:H`를 다시 읽어 `{"values": [...]}` 형식으로 `work/readback.json`에 저장한다(읽은 값을 그대로).
3. 검증한다.

```bash
python3 verify_written.py --rows work/rows.json --readback work/readback.json
```

종료 코드 1이면 S6부터 다시(최대 2회). 그래도 실패하면 오늘 행을 지우고 중단한다.

## 마무리

S7이 0일 때만 성공으로 보고한다. 1~2줄로 남긴다: 오늘 시장 분위기 한 줄(코스피200 야간선물 등락률 포함, `rows.json` 2행 요약 기준), 주목할 종목 1~2개. 주식일지 앱의 분석 → 전망 → 일간에서 확인할 수 있다고 덧붙인다. 실패했으면 실패한 단계와 errors를 그대로 보고한다.
