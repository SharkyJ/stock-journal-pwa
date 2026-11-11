# ops/outlook — 전망 리포트 파이프라인

일간·주간 전망 리포트의 결정적 단계(수집, 계산, 검증)를 맡는 스크립트다. 예약 작업이 `.claude/skills/outlook-daily`, `outlook-weekly` 스킬을 따라 실행한다. 설계와 진행 상황은 `docs/outlook-pipeline.md`.

- Python 3.10 이상, 표준 라이브러리만 사용 (설치 없음)
- 외부 호출(모두 GET, 무키): Worker(`config.json`의 `worker_base`) `/chart`, `/fund`, Google News RSS, Yahoo Finance 종목 RSS, 연준 보도자료 RSS, TradingView 경제 캘린더, Nasdaq 실적 API. 새 출처는 실패해도 리포트를 막지 않는다(fail-open).
- 뉴스 선별·경제 캘린더 판정·실적 수집 로직의 원 출처: bluedbs/invest-trading-bot (`docs/reference-news-calendar-events.md`, `docs/outlook-refactoring-plan.md`).
- 작업 파일은 `work/`에 쓰고 커밋하지 않는다.

## 파일

| 파일 | 단계 | 입력 → 출력 |
|---|---|---|
| `prepare.py` | 1단계 입력 | `begin`: 이전 기록 정리·입력 수집 보호 켜기. `input`: 훅 기록의 시트 읽기 응답 → `input.json` (보유 필터, 신호 기간, 최신 주간 전망, 재무추이 45일, 오늘 날짜) |
| `collect.py` | 1단계 수집 묶음 | `input.json` → `market.json`, `news.json`, `calendar.json`, `events.json` (`--digest`로 전체 요약 출력) |
| `draft.py` | 1·4단계 초안·합치기 | `brief`: 수집 결과 → `draft.json`(규칙 점수 전망, 지표 표, 서술형 사실, 체크포인트, 인용 ID) + 작성 과제 출력. `merge`: `draft.json` + 세션의 `notes.json`(행별 lead) → `analysis.json` |
| `collect_market.py` | 시세 | `input.json` → `market.json` |
| `collect_news.py` | 뉴스 | `input.json` → `news.json` (태그·호재/악재 후보 포함) |
| `news_score.py` | 뉴스 선별 | 렉시콘 점수, 부정어 처리, 토픽·티커 태그, 종목 관련도 |
| `collect_calendar.py` | 경제 캘린더 | `input.json` → `calendar.json` (C.., 판정·거시 기조) |
| `collect_events.py` | 보유 종목 실적 | `input.json`, `market.json` → `events.json` (E..) |
| `build_rows.py` | 4단계 검증·행 조립 | `input`, `market`, `news`, `calendar`, `events`, `extra_sources`, `analysis` → `rows.json` |
| `build_events.py` | 4단계 주요일정 행 | `calendar`, `events` → `events_rows.json` |
| `plan_write.py` | 5단계 기록 계획 | 데이터 + 훅 기록(`work/sheet_log.jsonl`) → `plan.json` (삭제 요청, 추가 값, 시작 행, 재조회 범위) |
| `verify_written.py` | 6단계 기록 검증 | `plan.json` + 훅 기록의 append·재조회 응답 → 결과 |
| `sheetlog.py` | 공용 | 훅 기록·guard 읽기쓰기. 훅은 `.claude/hooks/sheets_guard.py`(설정 `.claude/settings.json`) |
| `config.json` | 공통 설정 | 지수 목록, 기초자산 매핑, 뉴스 쿼리, 허용값, 길이 제한, 금지 표현, 시트 헤더 |
| `common.py` | 공용 | HTTP 재시도, 심볼 변환, 입력 정리 |

종료 코드: `0` 통과, `1` 재작성·재시도 필요, `2` 중단.

## 로컬 실행

```bash
cd ops/outlook
mkdir -p work   # input.json 은 prepare.py input 이 만든다(시트 읽기 기록 필요). 직접 만들어도 된다
python3 collect.py --input work/input.json --work work
python3 draft.py brief --work work        # 작성 과제 출력
# notes.json(행별 lead) 작성 후
python3 draft.py merge --work work
python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --calendar work/calendar.json --events work/events.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet App_일간전망_v2
python3 build_events.py --calendar work/calendar.json --events work/events.json --out work/events_rows.json
# 5·6단계는 시트 커넥터 호출이 필요하다(훅이 기록). 기록이 있으면:
python3 plan_write.py --data work/rows.json --out work/plan.json
python3 verify_written.py --plan work/plan.json
```

## 테스트

```bash
cd ops/outlook && python3 -m unittest discover -s tests -v
```

네트워크 없이 실행된다. CI에는 넣지 않았다(CI는 `csp` 체크만 유지).

## 주의

- Worker는 `Origin` 헤더가 허용 목록에 없으면 403을 준다. 스크립트는 `Origin`을 보내지 않는다.
- `r=1y` 응답의 `prev`는 전일 종가가 아니다. 등락률은 `r=1d` 응답으로 계산한다.
- Google News RSS는 개인·비상업 용도로만 쓸 수 있다. 링크는 Google News 리다이렉트 주소 그대로 쓴다.
- 레버리지 상품(`config.json`의 `underlying`)은 기초자산 시세를 함께 수집하고, 종목 뉴스는 수집하지 않는다.
