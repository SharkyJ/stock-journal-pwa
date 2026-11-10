# 시장 뉴스 / 경제 캘린더 / 종목 이벤트: 출처와 동작 로직

출처 repo: bluedbs/invest-trading-bot (main, 316ddca). 모든 외부 호출은 API 키 없는 공개 엔드포인트이고, Node 내장 `fetch`만 사용한다(외부 의존성 없음).

## 공통 구조
- 수집: `server.js`의 타이머가 주기적으로 외부 데이터를 가져와 `state`에 저장한다(프로세스 시작 시 1회 실행 후 setInterval).
- 소비: 매매 틱(`src/trader.js`)이 state의 값을 읽어 리스크 객체 `{ blocked, budgetMultiplier, label, reason }`를 만든다.
- 결합: `src/strategy.js`의 `evaluateTarget`가 신규 매수 예산 배수 = `min(event, news, calendar, volatility)`로 적용한다. `blocked`가 true면 신규 매수를 차단한다. 매도와 청산 로직에는 영향을 주지 않는다(신규 매수 축소 전용).
- 실패 시: 예외를 `state.health.lastError`에 기록하고 이전 값을 유지한다. 실적 일정은 실패하면 빈 배열이 되므로 차단을 만들지 않는다(fail-open).

| 항목 | 모듈 | 주기 | state 키 | 리스크 함수 |
|---|---|---|---|---|
| 시장 뉴스 | src/news.js | NEWS_REFRESH_MINUTES (기본 30분, 최소 5분) | state.news | getNewsRisk (src/eventRisk.js) |
| 경제 캘린더 | src/calendar.js | 60분 | state.economicCalendar | getCalendarRisk (src/calendar.js) |
| 종목 이벤트 | src/earnings.js + src/eventRisk.js | 12시간 (+ 종목별 6시간 캐시) | state.earningsEvents, state.eventCalendar | getEventRisk |

## 1. 시장 뉴스 (src/news.js, `fetchNews(targets)`)

### 출처 (RSS 3개, 병렬 `Promise.allSettled`, 피드당 최대 12건)
1. Federal Reserve 보도자료: `https://www.federalreserve.gov/feeds/press_monetary.xml` (category Fed)
2. Google News 검색 RSS: `https://news.google.com/rss/search?q=<query>&hl=ko&gl=KR&ceid=KR:ko` (category Macro)
   - query = `(<terms OR ...>) -Bitcoin -crypto when:7d`
   - terms = 거시 기본어(`NEWS_MACRO_QUERY_TERMS`, 기본 Federal Reserve, FOMC, inflation, semiconductor, AI) + 활성 종목의 회사명 키워드(`src/tickers.js`의 queryKeywords)
3. Yahoo Finance 종목 헤드라인 RSS: `https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYM,...>&region=US&lang=en-US` (category Tickers)
   - 심볼 = `NEWS_BASE_SYMBOLS`(기본 QQQ) + 활성 종목
- User-Agent: `toss-invest-terminal/0.1`. XML은 정규식으로 파싱한다(`<item>`의 title, link, pubDate|dc:date, description 220자, CDATA 처리와 기본 엔티티 디코드).
- 합친 뒤 publishedAt 내림차순으로 정렬하고 상위 24건만 남긴다(중복 제거는 하지 않는다).

### 감성 판정 (enrichNews)
- 대상 텍스트: title + summary 소문자.
- 가중 렉시콘으로 판정한다. POSITIVE_TERMS, NEGATIVE_TERMS 각 항목은 `[영문 키워드, 한글 라벨, 가중치 1~2.5]` 형식이다. 예: rate cut +2, memory shortage +2.5, raises guidance +2.5 / recession -2.5, guidance cut -2.5, collapse -2.5.
- 키워드는 `indexOf`로 찾고, 키워드당 1회만 센다.
- 부정어 처리: 키워드 앞 16자 안에 `no|not|without|avoid|unlikely|despite|less|lower|eas*`가 있으면 그 키워드를 무시한다.
- 점수와 분류:
  - score = 양수 가중합 - 음수 가중합.
  - score >= 1.5이면 positive(호재 가능성), score <= -1.5이면 negative(악재 가능성), 그 외는 neutral(중립).
- 태그:
  - 토픽 정규식 태그: 연준, 금리, 인플레이션, 거시, 반도체, AI, 메모리, 무역, 레버리지.
  - 티커 태그: 추적 종목과 CONTEXT_TICKERS(NVDA, AVGO, INTC, AMD)를 aliasMatcher로 매칭한다.
  - 태그는 최대 6개다.
- topic 우선순위: 연준/금리 > 인플레이션 > 매칭 종목(최대 2개) > 반도체/AI/메모리 > "종목 뉴스" 또는 "거시 뉴스".
- 한국어화: 번역 API는 쓰지 않는다. 제목은 단어 치환(Fed→연준 등)만 하고, koreanSummary는 템플릿 문장으로 만든다.
- 출력 필드: title, url, publishedAt, summary, source, category, translatedTitle, topic, koreanSummary, tags, sentiment, signalLabel, signalScore, signalReasons(최대 3).

### 매매 반영 (getNewsRisk(symbol, news, now, name))
- 최근 36시간 뉴스 중 관련 뉴스만 최대 8건 대상으로 한다.
- 관련 뉴스 판정:
  - 거시 정규식(fed, fomc, rate, cpi, pce, ppi, inflation, gdp, jobs, unemployment, 한글 동의어)에 걸리면 전 종목에 관련된 것으로 본다.
  - 그 외에는 `relevanceMatcher(symbol, name)`(별칭과 섹터 키워드)로 판정한다.
- 신규 매수 예산 0.5배 조건(하나라도 해당): 부정 뉴스 2건 이상, (긍정 건수 - 부정 건수) <= -2, signalScore 합 <= -3.
- 그 외에는 1배다(호재여도 예산을 늘리지 않는다).

### 티커 프로필 (src/tickers.js)
- 검색어, 태깅, 관련성 판정이 공유하는 단일 출처다.
- 구성: `TICKER_PROFILES[SYM] = { keywords, aliases, sector, leverage? }`, 섹터 정규식 `SECTOR_KEYWORDS`.
- 프로필이 없는 티커는 name에서 접미사(inc, corp 등)를 제외한 3글자 이상 토큰과 심볼 토큰으로 매칭한다.

## 2. 경제 캘린더 (src/calendar.js)

### 출처
- TradingView 공개 경제캘린더 JSON: `GET https://economic-calendar.tradingview.com/events?from=<ISO>&to=<ISO>&countries=US`
  - 헤더: `Origin: https://www.tradingview.com`, `User-Agent: Mozilla/5.0`, `Accept: application/json`. 타임아웃 15초.
  - 기간: 과거 4일 ~ 미래 14일. 국가는 `CALENDAR_COUNTRIES`(기본 US).
  - 응답의 `result`, `data` 또는 배열을 사용한다. importance -1(저중요도)은 제외하고, 날짜 오름차순으로 최대 60건만 남긴다.
- 비공식 엔드포인트라 스펙이 바뀔 수 있다.

### 정규화와 판정 (normalizeEvent, judgeBias)
- released: 표시용 `actual`이 비어 있지 않으면 발표된 것으로 본다. `actualRaw`는 미발표여도 0으로 올 수 있어 판단에 쓰지 않는다.
- 수치는 `actualRaw`, `forecastRaw`, `previousRaw`를 우선 사용한다.
- highImpact 정규식: CPI, inflation rate, PCE, PPI, interest rate decision, nonfarm, unemployment rate, GDP, ISM manufacturing/services, retail sales, FOMC.
- 카테고리(정규식, 방향, 가중치):
  - rate (special) 1.6
  - inflation: 낮을수록 호재, 1.4
  - unemployment: 낮을수록 호재, 0.8, 양면(twoSided)
  - jobs: 높을수록 호재, 0.6, 양면
  - growth(GDP, PMI, ISM, retail 등): 높을수록 호재, 1.0
- 판정 규칙:
  - 미발표: bias "예정", score 0.
  - 금리 결정: actual과 previous를 비교한다. 같으면 중립, 인하면 호재, 인상이면 악재다. score = ±2 × 1.6 × impWeight. impWeight는 importance >= 1이면 1.3, 아니면 1.0이다.
  - forecast가 없거나 카테고리가 미분류면 중립이다.
  - 그 외 지표:
    - 서프라이즈 = actual - forecast.
    - 상대 크기 rel = |서프라이즈| / max(|forecast|, |previous|).
    - magWeight는 rel < 0.05이면 0.6, rel <= 0.2이면 1.2, 그보다 크면 2.0이다.
    - score = 방향(±1) × magWeight × 카테고리 가중치 × impWeight.
    - score > 0.5이면 호재, score < -0.5이면 악재, 그 사이는 중립이다.
    - 강도: |score|가 1.8 이상이면 "강한", 0.9 이상이면 "보통", 그 미만이면 "약한"이다.
  - 양면 지표(고용, 실업): bias를 "혼조"로 두고, score는 부호만 유지한 채 크게 줄인다(× 0.3, magWeight 상한 1.2). 매수 축소 조건은 "악재"만 보므로 혼조는 매매에 영향이 없다.

### 매매 반영 (getCalendarRisk(calendar, now), 전 종목 공통)
- 거시 기조 `summarizeMacroTone`: 최근 5일 발표분의 biasScore를 합산한다.
  - 합 >= 2이면 호재 우위, 합 <= -2이면 악재 우위다.
  - toneMultiplier는 합 <= -5이면 0.6, 합 <= -3이면 0.8, 그 외는 1이다.
- 아래 순서로 평가하고, 앞 조건에 해당하면 거기서 끝난다.
  1. 고영향 지표가 4시간 안에 발표 예정이면 blocked(예산 0).
  2. 최근 8시간 안에 발표된 고영향 "악재"가 있으면 가장 강한 것 기준으로 배수를 정한다. biasScore <= -1.8이면 0.4, <= -0.9이면 0.6, 그 외는 0.75다. 최종 배수는 이 값과 toneMultiplier 중 작은 값이다.
  3. 24시간 안에 고영향 지표 발표가 예정돼 있으면 0.7이다. 최종 배수는 이 값과 toneMultiplier 중 작은 값이다.
  4. 해당 없으면 1(호재로 예산을 늘리지 않는다).

## 3. 종목 이벤트 (src/earnings.js + src/eventRisk.js)

### 출처
- Nasdaq 공개 API: `GET https://api.nasdaq.com/api/analyst/<SYMBOL>/earnings-date`
  - 헤더: `Origin`, `Referer`에 `https://www.nasdaq.com`, `User-Agent: Mozilla/5.0`. 타임아웃 12초.
  - 대상: 활성 종목 심볼(중복 제거). 순차 호출하고 종목별 결과(실패, null 포함)를 6시간 메모리 캐시에 둔다.
  - `data.data.announcement` 문자열(예: "Earnings announcement* for TSM: Jul 16, 2026")에서 `:\s*(Mon DD, YYYY)$`로 날짜만 추출한다.
  - 시각은 제공되지 않아 20:00 UTC(미국 장 마감 후)로 가정한다(estimated: true).
- 이벤트 객체: `{ id: earnings-SYM-YYYY-MM-DD, title: "SYM 실적 발표", type: "earnings", symbols: [SYM], startsAt, preBlockHours: 18, postBlockHours: 18, severity: "high", estimated: true, source: "Nasdaq" }`
- ETF(QQQM, XLE 등)는 실적일이 없어 이벤트가 생기지 않는다.
- 거시 이벤트는 여기서 다루지 않는다(경제 캘린더가 담당). 하드코딩 이벤트(BUILTIN_EVENTS)는 비어 있다.
- 화면용 `state.eventCalendar = listEventCalendar(events)`: 종료 시각(startsAt + postBlockHours)이 2일 넘게 지난 이벤트는 숨기고 날짜순으로 정렬한다.

### 매매 반영 (getEventRisk(symbol, now, events))
- 적용 대상: 이벤트의 symbols에 해당 종목이나 "*"가 있거나, type이 "macro"인 이벤트.
- 주의 구간(start - 18h ~ start + 18h): 신규 매수 예산을 high면 0.35배, 그 외면 0.55배로 줄인다.
- 하드 차단 구간(blocked, 예산 0): 실적 이벤트는 high면 -8h ~ +4h, 그 외면 -4h ~ +2h다. 다른 타입은 high면 -4h ~ +2h, 그 외면 -2h ~ +1h다.
- 구간 밖이면 1배이고, 가장 가까운 예정 이벤트를 upcomingEvent로 노출한다.

## 이식 시 주의
- 세 출처 모두 비공식, 무키 엔드포인트라 차단되거나 스펙이 바뀔 수 있다. 실패해도 매매가 멈추지 않도록 리스크 기본값을 1배로 두는 fail-open 설계를 유지하는 것이 좋다.
- 뉴스 감성은 키워드 기반이라 오탐이 있다. 그래서 매수 예산 축소(0.5배)에만 쓰고 매도 트리거로는 쓰지 않는다.
- 실적 시각은 추정값이다. 장전 발표 종목은 -18h 창 덕분에 대체로 커버되지만, 하드 차단 구간은 어긋날 수 있다.
- 렉시콘과 CONTEXT_TICKERS는 반도체, 메모리 중심 포트폴리오에 맞춰져 있으니 대상 포트폴리오에 맞게 조정해야 한다.
