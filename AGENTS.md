# AGENTS.md — 에이전트 작업 지침

## Git 워크플로
- main에는 직접 push하지 않는다. 기능 브랜치를 만들고 PR을 연다.
- **에이전트는 PR을 연 뒤 CI(`csp` 체크, `python3 build.py --check`)가 통과하면 직접 merge까지 진행한다.** 별도 승인을 기다리지 않는다. (2026-11-02 SJ 결정)
  - CI가 실패하면 merge하지 않는다. 원인을 고쳐 다시 push한다.
  - merge 방식은 merge commit을 쓴다. 이 세션 프록시에서는 브랜치 삭제(API·git push --delete)가 막혀 있으므로 남겨 둔다.
  - 시트 데이터를 파괴하는 변경(대량 수정·삭제·구조 변경)은 merge 전에 SJ에게 확인받는다.
- Claude 세션에서는 GraphQL(`gh pr create`)이 막혀 있으므로 REST API를 쓴다.
  - PR 생성: `gh api repos/SharkyJ/stock-journal-pwa/pulls -f title=… -f head=… -f base=main -F body=@file`
  - merge: `gh api -X PUT repos/SharkyJ/stock-journal-pwa/pulls/{n}/merge -f merge_method=merge`

## 빌드·버전
- `index.html`을 수정하면 `python3 build.py`를 실행한다. 이 스크립트가 CSP 스크립트 해시와 서비스워커 캐시 버전을 다시 계산한다.
- 릴리스할 때마다 `index.html`의 `const VERSION`을 올린다. 문서만 바뀐 경우는 올리지 않는다.
- 시트 구조를 바꾸면 `index.html` 안의 데모 백엔드와 파서도 함께 고친다.

## 보안
- 증권사 앱키·시크릿·토큰·계좌번호는 레포, 시트, 앱에 저장하지 않는다. Apps Script는 Script Properties에 두고, Worker는 Secrets에 둔다.
- 증권사 동기화 코드는 조회(GET) 경로만 호출한다. 주문·환전 POST 경로는 넣지 않는다.

## 참고 문서
- 증권사 API 동기화 설계: `docs/broker-api-sync.md`
- 전망 리포트 파이프라인 전환 계획: `docs/outlook-pipeline.md`
- 외부 참고(invest-trading-bot): 시장 뉴스·경제 캘린더·종목 이벤트 출처와 로직: `docs/reference-news-calendar-events.md`
