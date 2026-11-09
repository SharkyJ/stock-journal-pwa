# ops/outlook — 전망 리포트 파이프라인

일간·주간 전망 리포트의 결정적 단계(수집, 계산, 검증)를 맡는 스크립트다. 예약 작업이 `.claude/skills/outlook-daily`, `outlook-weekly` 스킬을 따라 실행한다. 설계와 진행 상황은 `docs/outlook-pipeline.md`.

- Python 3.10 이상, 표준 라이브러리만 사용 (설치 없음)
- 외부 호출: Worker(`config.json`의 `worker_base`) `/chart`, `/fund`와 Google News RSS. 모두 GET.
- 작업 파일은 `work/`에 쓰고 커밋하지 않는다.

## 파일

| 파일 | 단계 | 입력 → 출력 |
|---|---|---|
| `collect_market.py` | S1 시세 | `input.json` → `market.json` |
| `collect_news.py` | S2 뉴스 | `input.json` → `news.json` |
| `build_rows.py` | S5 검증·행 조립 | `input`, `market`, `news`, `extra_sources`, `analysis` → `rows.json` |
| `verify_written.py` | S7 기록 검증 | `rows.json`, `readback.json` → 결과 |
| `config.json` | 공통 설정 | 지수 목록, 기초자산 매핑, 뉴스 쿼리, 허용값, 길이 제한, 금지 표현, 시트 헤더 |
| `common.py` | 공용 | HTTP 재시도, 심볼 변환, 입력 정리 |

종료 코드: `0` 통과, `1` 재작성·재시도 필요, `2` 중단.

## 로컬 실행

```bash
cd ops/outlook
mkdir -p work   # input.json 은 스킬의 S0 형식으로 만든다
python3 collect_market.py --input work/input.json --out work/market.json
python3 collect_news.py --input work/input.json --out work/news.json
# analysis.json, extra_sources.json 작성 후
python3 build_rows.py --input work/input.json --market work/market.json --news work/news.json \
  --extra work/extra_sources.json --analysis work/analysis.json --out work/rows.json --sheet App_일간전망_v2
python3 verify_written.py --rows work/rows.json --readback work/readback.json
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
