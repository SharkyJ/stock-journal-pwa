# 증권사 API 매매기록 자동 동기화 — 조사 결과·매핑표·PoC 설계안 (2026-11-02)

## 진행 결정 (2026-11-02)
- **메리츠증권(KRW·USD): API 연동을 구현한다.** 매매, 입출금, 환전을 다룬다.
- **토스증권: 설계까지만 진행한다.** 고정 IP 실행 환경이 준비되면 구현한다.
- **메리츠 RIA·토스 RIA: 수동 입력을 유지한다.**

## 0. 결론
- **메리츠증권은 거의 전부 자동화할 수 있다.** 국내·해외 체결 조회와 함께 **계좌 거래내역(입출금·매매·수수료·세금)**, **환전 내역** API가 공식으로 제공된다. 따라서 종목 탭(매매)과 현금투자 흐름표(입금·출금·달러 매수·매도)를 둘 다 채울 수 있다.
- **토스증권은 매매만 자동화할 수 있다.** 주문 목록(CLOSED)에 체결수량·평균체결가·수수료·세금이 포함된다. 다만 입출금·환전 내역 API가 없다. 또 **종합매매(BROKERAGE) 계좌만 지원**해서 **토스 RIA(RESHORING_INVESTMENT)는 현재 불가**하다.
- 앱키와 시크릿은 브라우저 PWA에 둘 수 없다. **서버 측 동기화 작업**에서 시트의 **스테이징 시트**에 쓰고, 앱에서 확인한 뒤 반영하는 구조를 제안한다.
- 관문 현황(2026-11-02): **메리츠 IP 제한 없음 확인(SJ, openapi.imeritz.com/howto-use)** → 유동 IP 환경 OK. 남은 관문: ① 토스증권 허용 IP 등록이 필수인지(빈 목록 = 전체 허용인지), ② 실행 환경에서 메리츠 9443 포트 호출 가능 여부.

## 1. 출처
- 토스: 공식 OpenAPI 스펙 v1.2.17 (`https://openapi.tossinvest.com`, developers.tossinvest.com/docs)
- 메리츠: 메리츠증권 공식 `open-api-codegen-mcp`의 catalog.json(개발자 포털 등록 명세, REST 73·실시간 14)과 corrections.json(알려진 문제)

## 2. 공통 규약 비교
| 항목 | 메리츠증권 | 토스증권 |
|---|---|---|
| 베이스 URL | `https://openapi.imeritz.com:9443` (비표준 포트) | `https://openapi.tossinvest.com` |
| 토큰 | `POST /oauth2/token` form-urlencoded, `grant_type=client_credentials&client_id=앱키&client_secret=…&scope=login`. JSON으로 보내면 EGW00116 | `POST /oauth2/token` form-urlencoded, client credentials, JWT |
| 토큰 규칙 | 앱키당 유효 토큰 1개, **유효 12시간**, `expires_in`까지 재사용. revoke 호출은 다른 세션도 끊음 | client당 1개, 재발급하면 이전 토큰 즉시 무효. refresh token 없음 |
| 필수 헤더 | `authorization: Bearer`, **`mac_address`(12자리, 없으면 IGW50024)** | `Authorization: Bearer`, `X-Tossinvest-Account: accountSeq` |
| 계좌 지정 | **앱키에 계좌가 묶임** → 계좌마다 앱키 발급 (메리츠·메리츠 RIA 각각) | 한 키로 `GET /api/v1/accounts` → accountSeq 선택 |
| 페이징 | `rsp_cd` 5762면 `tr_cont=1`+`tr_cont_key`(뒤 공백 유지, URL 인코딩). 30건 단위 | `cursor`/`limit`(최대 100), `hasNext` |
| 호출 제한 | API별 TPS 10 | 그룹별(ACCOUNT, ASSET, ORDER_HISTORY 등). 수치는 스펙에서 확인 |
| IP 제한 | **없음** (확인 2026-11-02, 공식 howto-use). 대신 앱키·시크릿(시크릿은 최초 1회만 노출) + 토큰 + mac_address | **허용 IP 목록 존재** (WTS 설정 > Open API > 허용 IP 관리). 미등록 IP는 403. 필수 여부는 미확인 |
| 지원 계좌 | 국내·해외 / RIA 여부 미확인 | BROKERAGE만. 연금·RIA·해외파생 불가 |

## 3. 엔드포인트 매핑표

### 3-1. 매매 → 종목 탭(A 거래일자, B 구분, C 수량, D 거래단가, E 수수료/세금, J 계좌구분, K 메모)

| 시트 열 | 메리츠 국내 `GET /trading/v1/orders/history` (obtrs_comn_615r) | 메리츠 해외 `GET /trading/v1/overseas/orders/history` (obtro_oder_201r) | 메리츠 거래내역 `GET /accounts/v1/me/transactions` (obrtp_tran_001r) | 토스 `GET /api/v1/orders?status=CLOSED` |
|---|---|---|---|---|
| 조회 범위 | `oder_date` **하루 단위** → 날짜 루프 | `oder_sta_date`~`oder_fin_date` (00000000~ 전체 가능, 90일 한도 주의) | `from`(00000000=처음부터)~`to` | `from`/`to` (주문일, KST), limit 100 |
| A 거래일자 | 요청한 oder_date | `cntg_ymdh` 날짜부 | `tr_date` | `execution.filledAt` 날짜부 |
| B 구분 | `deal_cls_code` 1=매도/2=매수 | `seln_byov_clcd` 1/2 | `tr_type_code`/`smcd_name` | `side` BUY/SELL |
| C 수량 | `total_cntg_qty` | `cntg_dqty` (소수 가능) | `tr_qty` | `execution.filledQuantity` |
| D 거래단가 | `cntg_unpr` (평균) | `cntg_crnc_prc` | `byng_unpr` | `execution.averageFilledPrice` |
| E 수수료/세금 | ✗ (없음 → 거래내역에서 보강하거나 앱 수수료표로 계산) | ✗ | `deal_fee`+`fee`+`trtx`+`srtx`+`intx`+`rstx`, 해외 `ovsc_deal_fee` | `execution.commission`+`execution.tax` |
| 종목 | `iscd` "A005930" | `iscd` "PLTR.OQ" → 심볼 `ovrs_stck_symb_iscd` | `iscd` | `symbol` 005930 / AAPL |
| 고유키(K 메모) | `oder_no`+날짜 | `oder_date`+`oder_no`(+`cntg_no`) | `tr_date`+`tran_srno` | `orderId` |
| 제외 대상 | 정정·취소 행(`rctf_cncl_cls_code`≠1), 체결 0 | 정정·취소, 거부 | `cncl_yn`=Y | status≠FILLED·PARTIAL 체결 0 |

**메리츠 권장 소스는 거래내역(transactions) 하나다.** 기간 조회, 실제 정산 수수료·세금, 입출금이 한 API에 다 들어 있다. 주문내역은 장중 미확정분 확인과 교차검증에만 쓴다. 단, 거래내역은 **체결 1건이 아니라 결제 단위**일 수 있으므로 행 단위 매칭을 PoC에서 검증해야 한다.

**토스 주의:** 장전·장후 시간외 종가 주문은 Open API 목록에 나오지 않는다(스펙 명시). 이런 주문은 누락되므로 보유수량 대사로 잡아야 한다. 부분 체결 후 취소된 주문은 `filledQuantity`만 반영한다.

### 3-2. 현금 흐름 → 현금투자 흐름표(A 일자, B 구분, C 계좌, D 원화, E 환율, F 달러, G 메모)
| 구분 | 메리츠 | 토스 |
|---|---|---|
| 입금·출금 | 거래내역 `io_cls_code`, `smcd_name`, `tr_amt` (적요코드 매핑 필요) | ✗ 수동 입력 유지 |
| 달러 매수·매도 | `GET /forex/v1/exchanges/history` (obtsd_fbnk_203r): `mnec_clcd` 1=외화매수/2=외화매도/3=취소, `tr_amt`(원화), `frcr_tr_amt`(달러), `aply_exrt`, 키=`aplt_date`+`aplt_srno` | ✗ 수동 입력 유지 |

### 3-3. 대사(검증) — 잔고
| 용도 | 메리츠 | 토스 |
|---|---|---|
| 국내 보유 | `GET /accounts/v1/me/holdings`: `rmnd_qty`, `byng_unpr`, `byng_amt` | `GET /api/v1/holdings`: `quantity`, `averagePurchasePrice` (KR·US 통합) |
| 해외 보유 | `GET /accounts/v1/me/overseas/holdings`: `rmnd_dqty`(소수 10자리), `avrg_unpr` | 위와 같음 |
| 예수금 | `GET /accounts/v1/me/deposit` (`tfam`), `…/overseas/currencies` | ✗ (매수가능금액 `buying-power`만 있음) |
| 수수료율 | – | `GET /api/v1/commissions` (앱 수수료표 검증용) |

시트의 보유수량(종목 탭 G 누계)과 API 보유수량이 다르면 "누락 거래 의심"으로 표시한다. 평단가는 시트가 기준(이동평균)이므로 차이는 경고만 하고 덮어쓰지 않는다.

### 3-4. 계좌·티커 매핑
| 시트 계좌(J) | 연결 |
|---|---|
| 메리츠증권 (KRW·USD) | 메리츠 앱키 #1 (종합계좌 하나에 원화·외화 공존) |
| 메리츠 RIA | 메리츠 앱키 #2 (RIA 발급 가능 여부 확인 필요) |
| 토스증권 (KRW·USD) | 토스 accountSeq (BROKERAGE) |
| 토스 RIA | **불가 → 수동** |

티커: 메리츠 `A005930` → `KRX:005930` (KOSDAQ 여부는 종목목록 B2에서 조회), `PLTR.OQ` → 종목목록에서 심볼로 매칭. 토스 `005930`/`AAPL` → 같은 방식으로 매칭한다. 매칭되는 탭이 없으면 "새 종목" 대기로 둔다.

## 4. PoC 아키텍처
```
[메리츠 / 토스 API] ←(앱키·시크릿)— [동기화 작업: 서버 측, 하루 2회]
                                         │ 정규화·중복키 필터
                                         ▼
                        주식일지 App_체결수신 (스테이징 시트)
                                         │
                       PWA "가져오기" 화면: 검토 → 승인
                                         ▼
          종목 탭 A–E,J,K (기존 날짜순 삽입 로직 재사용) / 현금투자 흐름표 A:G
```
- **스테이징을 거치는 이유:** 시트 규칙(사용자 입력열 직접 덮어쓰기 금지, 날짜순 삽입, 평단 계산)을 지키기 위해서다. 새 티커 탭 생성과 계좌·티커 매핑 오류를 사람이 확인하고, 기존에 손으로 입력한 거래와 겹치는지도 판정해야 한다.
- **App_체결수신 열(안):** A 키(증권사|계좌|원천키), B 수신시각, C 증권사, D 계좌구분, E 유형(매수/매도/입금/출금/달러 매수/달러 매도), F 거래일자, G 티커(GOOGLEFINANCE), H 종목명, I 수량, J 단가, K 수수료·세금, L 원화금액, M 달러금액, N 환율, O 통화, P 상태(대기/반영/무시/중복의심), Q 반영위치, R 원본 JSON 요약
- **중복 처리:** K 메모에 `api:증권사:원천키`를 넣는다. 수동 입력분과의 중복은 같은 날짜·구분·수량·단가(±0.5%)이면 "중복의심"으로 표시한다. 기준일(2026-10-17) 이전 거래는 가져오지 않는다.

### 실행 환경 후보 (관문 결과에 따라 선택)
| 후보 | 장점 | 제약 |
|---|---|---|
| **A. Apps Script** (`snapshot.gs`와 같은 방식, 시트에 바인딩) | 서버 불필요. Script Properties에 시크릿 저장, 시트에 직접 쓰기, 시간 트리거 | 고정 IP 없음, UrlFetchApp이 9443 포트를 호출할 수 있는지 확인 필요, 실행 6분 제한 |
| B. Cloudflare Worker cron (기존 `sj-chart` 옆에 별도 Worker) | 이미 운영 중, Secrets 지원 | 고정 IP 없음, 비표준 포트 fetch 확인 필요, Sheets 쓰기에 서비스계정 필요 |
| C. 집 PC·NAS·소형 VPS의 Python cron | **고정 IP** 확보 가능, 메리츠 공식 예제(Python) 그대로 사용, 9443 문제 없음 | 상시 구동 기기 필요, Sheets API 서비스계정 |

판단 기준: 메리츠는 IP 제한이 없으므로 A(9443 호출만 확인되면)로 확정 가능. 토스는 허용 IP가 필수면 토스만 **C**(고정 IP)로 분리.

참고: `docs.tosspayments.com`의 API 키 접근 정책은 **토스페이먼츠(결제)** 문서라 토스증권과 무관하다. 토스증권은 스펙상 WTS 설정 > Open API > 허용 IP 관리가 있고, 미등록 IP는 403이다. 목록이 비었을 때 전체 허용인지는 WTS에서 직접 확인해야 한다.

### 보안
- **조회 전용 사용**: 동기화 코드에는 주문·환전 POST 경로를 아예 넣지 않는다(허용 경로 화이트리스트). 증권사가 권한 분리를 지원하면 조회 전용 키를 발급한다.
- 시크릿은 Script Properties나 Worker Secret에만 둔다. 시트·레포·앱에는 넣지 않는다. 토큰은 캐시해서 재사용하고 revoke는 호출하지 않는다.
- mac_address 헤더는 고정값 하나를 설정으로 둔다.
- 스테이징 시트의 원본 요약에 계좌번호를 저장하지 않는다.

## 5. PoC 단계
| 단계 | 내용 | 산출물 / 완료 기준 |
|---|---|---|
| 0. 관문 확인 (SJ) | 메리츠 개발자 포털에서 앱키 발급(종합, RIA 가능 여부). 토스 WTS에서 Open API 키 발급, 허용 IP 필수 여부와 조회 전용 권한 여부 확인 | 키 2~3개, 확인 결과 메모 |
| 1. 연결 테스트 | 실행 환경에서 토큰 발급 → 메리츠 `holdings`·`transactions`(최근 30일), 토스 `accounts`·`holdings`·`orders CLOSED` 호출, 응답을 로그로 저장 (계좌번호 마스킹) | 9443·IP 문제 여부 판정, 실제 응답 샘플 |
| 2. 정규화 | 응답 → 공통 레코드(위 App_체결수신 열) 변환. 메리츠 적요코드·거래유형 매핑표 작성, 체결 단위 대 결제 단위 검증 | 지난 2주 거래가 시트 수동 기록과 1:1 일치 |
| 3. 스테이징 쓰기 | App_체결수신 생성, 중복키 필터, 하루 2회 트리거 (KR 16:30, US 07:30 KST) | 재실행해도 중복 행 0 |
| 4. 앱 가져오기 화면 | 전략·현금 탭 옆 "가져오기" 목록, 승인 시 기존 거래 입력·흐름 입력 함수 재사용, 새 티커는 탭 생성 흐름으로 연결. 데모 백엔드도 반영, v1.8.0 | 승인 → 종목 탭·흐름표 반영, 상태 '반영' |
| 5. 대사 | 보유수량·예수금 비교 카드(불일치 경고) | 전 계좌 일치 또는 원인 표시 |

## 6. 확인 결과 (2026-11-02, SJ 확인)
| 항목 | 결과 | 설계 반영 |
|---|---|---|
| 토스 허용 IP | 허용 IP 목록 기반 차단. 공식 FAQ는 공인 IP가 바뀌는 환경(클라우드·서버리스·모바일)에서는 403이 나므로 고정 IP를 확보하라고 안내한다. 데이터센터 대역 자체를 차단하지는 않는다 | **토스는 고정 IP 실행 환경(C: 집 PC·NAS 또는 고정 IP VPS)으로 분리.** Apps Script·Worker는 불가 |
| 토스 RIA | 미지원 | 토스 RIA는 수동 입력 유지 |
| 토스 시간외 종가 | 장전 08:30~08:40, 장후 15:40~16:00(접수 15:30~). Open API 목록에서 제외됨 | 대사 단계(보유수량 비교)에서 "누락 의심"으로 잡는다. 해당 거래는 수동 보완 |
| 토스 rate limit | ORDER·ORDER_HISTORY 초당 6회(09:00~09:10에는 3회), MARKET_DATA 10회, CHART 5회, ASSET·STOCK 5회, AUTH 5회. 초과 시 429 | 조회 간격 ≥ 250ms, 429 지수 백오프. 동기화는 09:00~09:10 회피 |
| 메리츠 IP | 제한 없음 | 실행 환경 A(Apps Script) 가능 |
| 메리츠 RIA | 미지원 | 메리츠 RIA도 수동 입력 유지 |
| 메리츠 해외 주문내역 | 1회 최대 90일 | 90일 창으로 나눠 반복 조회. 기준일 2026-10-17 이후만 필요하므로 초기 적재도 1~2회 호출로 끝남 |
| 메리츠 적요코드 | 매매 / 입출금 / 배당·세금·환전 등이 서로 다른 코드로 구분됨 | 1차 필터를 `tr_type_code`·`smcd`로 하고, 실제 코드표는 PoC 단계 2에서 응답 샘플로 확정 (받은 설명의 예시 코드값은 일반론이라 그대로 쓰지 않음) |
| 메리츠 포트 | 공식 카탈로그상 REST `:9443`, WebSocket `:29443` (443 아님) | 단계 1에서 Apps Script UrlFetchApp의 9443 호출을 첫 테스트로 판정. 실패하면 메리츠도 C로 이동 |
| 수수료 | API 실제값 사용 | 확인 |
| 명세 변경 | 버전 고정 + 응답 검증 | 확인 |

### 확정된 구성
- **메리츠증권(KRW·USD)**: Apps Script(9443이 되면) 또는 C. 매매·입출금·환전을 모두 자동화한다.
- **토스증권(KRW·USD)**: C(고정 IP). 매매만 자동화하고, 입출금·환전은 수동이다.
- **메리츠 RIA·토스 RIA**: 전부 수동 입력을 유지한다.
- 공통: App_체결수신 스테이징 → 앱에서 승인 → 반영.

### 남은 미확인
- Apps Script에서 9443 호출이 되는지
- 메리츠 거래내역의 체결 단위와 결제 단위 차이, 실제 적요코드표
- 토스 고정 IP 확보 수단(집 공인 IP가 사실상 고정인지, NAS·VPS 중 무엇을 쓸지)
