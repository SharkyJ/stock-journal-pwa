"""뉴스 제목 선별용 점수·태그 (결정적, 순수 함수).

bluedbs/invest-trading-bot 의 src/news.js(가중 렉시콘, 부정어 처리, 토픽 태그)와 src/tickers.js
(티커 프로필, aliasMatcher, relevanceMatcher)를 옮겼다. 점수는 기사 정렬·선별과 사전 태그에만 쓴다.
LLM 이 본문에 점수를 수치로 쓰지 않으며, 호재·악재 최종 판단은 LLM 이 기사 제목을 보고 한다.
"""
import re

POSITIVE = [
    ("rate cut", "금리 인하 기대", 2), ("rate cuts", "금리 인하 기대", 2), ("dovish", "완화적 연준", 1.5),
    ("cooling inflation", "인플레이션 둔화", 2), ("inflation cools", "인플레이션 둔화", 2),
    ("inflation eases", "인플레이션 완화", 2), ("disinflation", "디스인플레이션", 1.5), ("inflation falls", "인플레이션 하락", 2),
    ("soft landing", "경기 연착륙", 1), ("ai demand", "AI 수요", 2), ("hbm", "HBM 강세", 2),
    ("data center", "데이터센터 수요", 1.5), ("strong demand", "수요 강세", 1.5), ("memory shortage", "메모리 공급부족", 2.5),
    ("undersupply", "공급 부족", 2), ("price increase", "가격 인상", 2.5), ("price hike", "가격 인상", 2),
    ("earnings beat", "실적 호조", 2), ("beats estimates", "실적 호조", 2), ("raises guidance", "가이던스 상향", 2.5),
    ("raises outlook", "전망 상향", 2), ("upgrade", "투자의견 상향", 1), ("rally", "상승 흐름", 1), ("surge", "급등", 1.5),
    ("record high", "사상 최고", 1.5), ("jumps", "급등", 1), ("soars", "급등", 1.5),
    ("급등", "급등", 1.5), ("상승", "상승", 1), ("순매수", "순매수", 1), ("호실적", "실적 호조", 2), ("상향", "상향", 1),
    ("최고치", "최고치", 1.5), ("반등", "반등", 1),
]
NEGATIVE = [
    ("rate hike", "금리 인상 우려", 2), ("higher for longer", "고금리 장기화", 1.5), ("hawkish", "매파적 연준", 1.5),
    ("rising inflation", "인플레이션 상승", 2), ("hot inflation", "인플레이션 과열", 2), ("sticky inflation", "끈적한 인플레이션", 2),
    ("inflation surge", "인플레이션 급등", 2), ("high inflation", "고인플레이션", 1.5), ("inflation", "인플레이션 부담", 1),
    ("recession", "침체 우려", 2.5), ("slowdown", "둔화 우려", 1.5), ("memory glut", "메모리 공급과잉", 2.5),
    ("oversupply", "공급 과잉", 2.5), ("inventory correction", "재고 조정", 2), ("price decline", "가격 하락", 2.5),
    ("price drop", "가격 하락", 2), ("demand weakness", "수요 약세", 2), ("weak demand", "수요 약세", 2),
    ("capex cut", "투자 축소", 2), ("guidance cut", "가이던스 하향", 2.5), ("cuts outlook", "전망 하향", 2.5),
    ("cuts guidance", "가이던스 하향", 2.5), ("downturn", "업황 둔화", 2), ("export restriction", "수출 규제", 2),
    ("export curb", "수출 규제", 2), ("tariff", "관세 부담", 1.5), ("misses", "실적 부진", 2),
    ("selloff", "매도세", 1.5), ("plunge", "급락", 2), ("crash", "폭락", 2.5), ("glut", "공급과잉", 2),
    ("collapse", "폭락", 2.5), ("rout", "투매", 2), ("meltdown", "붕괴", 2.5), ("crater", "폭락", 2),
    ("tumble", "급락", 1.5), ("slump", "급락", 1.5), ("bear market", "약세장", 2), ("sell-off", "매도세", 1.5),
    ("sinks", "급락", 1.5), ("wipeout", "손실 경고", 2), ("downgrade", "투자의견 하향", 1.5), ("lowered", "하향", 1),
    ("probe", "조사 리스크", 1.5), ("lawsuit", "소송 리스크", 1.5),
    ("급락", "급락", 1.5), ("하락", "하락", 1), ("순매도", "순매도", 1), ("하향", "하향", 1), ("폭락", "폭락", 2.5),
    ("반토막", "급락", 2), ("우려", "우려", 1),
]
# 부정어가 키워드 바로 앞(16자 안)에 있으면 그 신호는 무시한다(예: "no recession", "not a rate hike")
NEGATION = re.compile(r"\b(no|not|without|avoid|unlikely|despite|less|lower|eas\w*)\s+\S{0,14}$")

TOPIC_TAGS = [
    (re.compile(r"\bfed\b|fomc|federal reserve|연준|powell|warsh"), "연준"),
    (re.compile(r"rate (cut|hike|decision)|interest rate|monetary|금리"), "금리"),
    (re.compile(r"inflation|\bcpi\b|\bpce\b|\bppi\b|인플레|물가"), "인플레이션"),
    (re.compile(r"recession|\bgdp\b|jobs|payroll|unemployment|경기|침체|고용"), "거시"),
    (re.compile(r"semiconductor|\bchips?\b|반도체"), "반도체"),
    (re.compile(r"\bai\b|artificial intelligence"), "AI"),
    (re.compile(r"\bmemory\b|\bdram\b|\bnand\b|\bhbm\b|메모리"), "메모리"),
    (re.compile(r"tariff|export (restriction|curb|control)|trade war|관세|수출 규제"), "무역"),
    (re.compile(r"leverage|leveraged etf|\b[23]x\b|레버리지"), "레버리지"),
    (re.compile(r"earnings|guidance|quarter|실적|분기"), "실적"),
]

# 티커 프로필: 검색어·태깅·관련도 판정이 공유한다. 키는 거래소 접두어 없는 심볼.
PROFILES = {
    "MU": {"aliases": ["micron"], "sector": "memory"},
    "TSM": {"aliases": ["tsmc", "taiwan semiconductor"], "sector": "semi"},
    "GOOG": {"aliases": ["alphabet", "google", "googl", "waymo", "gemini"], "sector": "ai"},
    "GOOGL": {"aliases": ["alphabet", "google", "goog"], "sector": "ai"},
    "SNDK": {"aliases": ["sandisk"], "sector": "memory"},
    "NVDA": {"aliases": ["nvidia", "jensen huang"], "sector": "semi"},
    "AVGO": {"aliases": ["broadcom"], "sector": "semi"},
    "INTC": {"aliases": ["intel"], "sector": "semi"},
    "AMD": {"aliases": ["advanced micro devices", "lisa su"], "sector": "semi"},
    "MRVL": {"aliases": ["marvell"], "sector": "semi"},
    "IREN": {"aliases": ["iris energy"], "sector": "aiinfra"},
    "SPCX": {"aliases": ["spacex", "starlink", "musk"], "sector": "space"},
    "AAPL": {"aliases": ["apple"], "sector": "tech"},
    "MSFT": {"aliases": ["microsoft"], "sector": "tech"},
    "AMZN": {"aliases": ["amazon"], "sector": "tech"},
    "META": {"aliases": ["meta platforms", "facebook"], "sector": "ai"},
    "TSLA": {"aliases": ["tesla"], "sector": "tech"},
    "005930": {"aliases": ["삼성전자", "삼전", "samsung electronics"], "sector": "memory"},
    "000660": {"aliases": ["sk하이닉스", "하이닉스", "sk hynix"], "sector": "memory"},
    "012450": {"aliases": ["한화에어로스페이스", "한화에어로"], "sector": "defense"},
}
SECTOR = {
    "semi": re.compile(r"semiconductor|chips?|\bai\b|nvidia|tsmc|반도체", re.I),
    "memory": re.compile(r"memory|dram|nand|hbm|storage|반도체|메모리", re.I),
    "ai": re.compile(r"\bai\b|artificial intelligence|cloud|data center|advertising|search|클라우드|데이터센터", re.I),
    "aiinfra": re.compile(r"\bai\b|data center|gpu|neocloud|coreweave|bitcoin min", re.I),
    "space": re.compile(r"space|rocket|satellite|launch|우주", re.I),
    "defense": re.compile(r"방산|국방|defen[cs]e|drone|드론|미사일", re.I),
    "tech": re.compile(r"technology|tech stocks|big tech|빅테크|기술주", re.I),
}
STOP = {"inc", "corp", "corporation", "ltd", "co", "company", "technologies", "technology", "holdings", "group",
        "plc", "the", "class", "adr", "etf", "trust", "fund", "stock", "common"}


def code_of(ticker):
    return str(ticker).split(":", 1)[-1].upper()


def alias_re(ticker, name=""):
    code = code_of(ticker)
    p = PROFILES.get(code) or {}
    toks = {code.lower(), *p.get("aliases", [])}
    toks |= {w for w in re.split(r"[^a-z]+", str(name).lower()) if len(w) >= 3 and w not in STOP}
    if re.search(r"[가-힣]", str(name)):
        toks.add(str(name).lower())
    toks = [re.escape(t) for t in toks if t]
    if not toks:
        return re.compile(r"a^")
    return re.compile("|".join(f"(?:^|[^a-z0-9가-힣]){t}(?:[^a-z0-9가-힣]|$)" for t in toks), re.I)


def relevant(text, ticker, name=""):
    """기사가 종목과 관련 있는지: 별칭·회사명 매칭 또는 섹터 키워드 매칭."""
    if alias_re(ticker, name).search(text):
        return "alias"
    sec = SECTOR.get((PROFILES.get(code_of(ticker)) or {}).get("sector"))
    return "sector" if sec and sec.search(text) else ""


def hits(text, terms):
    labels, w = [], 0.0
    for needle, label, val in terms:
        i = text.find(needle)
        if i < 0:
            continue
        if NEGATION.search(text[max(0, i - 16):i]):
            continue
        labels.append(label)
        w += val
    return labels, w


def score(title, tracked=()):
    """제목 → {score, hint(호재/악재/중립 후보), reasons, tags}."""
    t = str(title).lower()
    pl, pw = hits(t, POSITIVE)
    nl, nw = hits(t, NEGATIVE)
    sc = round((pw - nw) * 10) / 10
    hint = "호재" if sc >= 1.5 else "악재" if sc <= -1.5 else "중립"
    tags = [lab for r, lab in TOPIC_TAGS if r.search(t)]
    for tk, nm in tracked:
        if alias_re(tk, nm).search(t):
            tags.insert(0, code_of(tk))
    seen, uniq = set(), []
    for x in tags:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return {"score": sc, "hint": hint, "reasons": (nl + pl)[:3], "tags": uniq[:6]}
