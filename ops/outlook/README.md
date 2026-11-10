# ops/outlook — 전망 리포트 파이프라인

일간·주간 전망 리포트의 결정적 단계(수집, 계산, 검증)를 맡는 스크립트다. 예약 작업이 `.claude/skills/outlook-daily`, `outlook-weekly` 스킬을 따라 실행한다. 설계와 진행 상황은 `docs/outlook-pipeline.md`.

- Python 3.10 이상, 표준 라이브러리만 사용 (설치 없음)
- 외부 호출(모두 GET, 무키): Worker(`config.json`의 `worker_base`) `/chart`, `/fund`, Google News RSS, Yahoo Finance 종목 RSS, 연준 보도자료 RSS, TradingView 경제 캘린더, Nasdaq 실적 API. 새 출처는 실패해도 리포트를 막지 않는다(fail-open).
- 뉴스 선별·경제 캘린더 판정·실적 수집 로직의 원 출처: bluedbs/invest-trading-bot (`docs/reference-news-calendar-events.md`, `docs/outlook-refactoring-plan.md`).
- 작업 파일은 `work/`에 쓰고 커밋하지 않는다.

## 파일

| 파일 | 단계 | 입력 → 출력 |
|---|---|---|
| `collect.py` | 3단계 묶음 | `input.json` → `market.json`, `news.json`, `calendar.json`, `events.json` + 해석용 요약 출력 (스킬은 이것만 실행) |
| `collect_market.py` | 시세 | `input.json` → `market.json` |
| `collect_news.py` | 뉴스 | `input.json` → `news.json` (태그·호재/악재 후보 포함) |
| `news_score.py` | 뉴스 선별 | 렉시콘 점수, 부정어 처리, 토픽·티커 태그, 종목 관련도 |
| `collect_calendar.py` | 경제 캘린더 | `input.json` → `calendar.json` (C.., 판정·거시 기조) |
| `collect_events.py` | 보유 종목 실적 | `input.json`, `market.json` → `events.json` (E..) |
| `build_rows.py` | 6단계 검증·행 조립 | `input`, `market`, `news`, `calendar`, `events`, `extra_sources`, `analysis` → `rows.json` |
| `build_events.py` | 6단계 주요일정 행 | `calendar`, `events` → `events_rows.json` |
| `plan_write.py` | 7단계 기록 계획 | 데이터 + 시트 응답 원문 → `plan.json` (삭제 요청, 추가 값, 시작 행, 재조회 범위) |
| `verify_written.py` | 8단계 기록 검증 | `plan.json` + append·재조회 응답 원문 → 결과 |
| `config.json` | 공통 설정 | 지수 목록, 기초자산 매핑, 뉴스 쿼리, 허용값, 길이 제한, 금지 표현, 시트 헤더 |
| `common.py` | 공용 | HTTP 재시도, 심볼 변환, 입력 정리 |

종료 코드: `0` 통과, `1` 재작성·재시도 필요, `2` 중단.

## 로컬 실행

```bash
cd ops/outlook
mkdir -p work   # input.json 은 스킬의 S0 형식으로 만든다
python3 collect.py --input work/input.json --work work
# analysis.json, extra_sources.json 작성 후
python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --calendar work/calendar.json --events work/events.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet App_일간전망_v2
python3 build_events.py --calendar work/calendar.json --events work/events.json --out work/events_rows.json
python3 plan_write.py --data work/rows.json --sheet-id 123 --cola work/cola.json --head work/head.json --out work/plan.json
python3 verify_written.py --plan work/plan.json --append work/append.json --readback work/rb1.json work/rb2.json
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
