"""전망 파이프라인 단위 테스트 (네트워크 없음).

실행: cd ops/outlook && python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import build_events as BE  # noqa: E402
import build_rows as B  # noqa: E402
import collect_calendar as K  # noqa: E402
import collect_events as E  # noqa: E402
import collect_market as M  # noqa: E402
import collect_news as N  # noqa: E402
import common as C  # noqa: E402
import news_score as S  # noqa: E402
import plan_write as W  # noqa: E402
import verify_written as V  # noqa: E402

CFG = C.load_config()


def inp_daily():
    i, e = C.normalize_input({
        "date": "2026-11-09", "mode": "daily",
        "holdings": [{"ticker": "KRX:005930", "name": "삼성전자"}, {"ticker": "NYSEARCA:SOXL", "name": "SOXL"}],
        "watch": [{"ticker": "krx:005930", "name": "삼성전자"}, {"ticker": "NASDAQ:MU", "name": "마이크론"}],
    })
    assert not e, e
    return i


def market_fixture():
    items = {
        "M1": {"id": "M1", "sym": "^KS11", "status": "ok", "value": 5018.85, "prev": 5059.69, "chg_pct": -0.81, "lo20": 4911.28},
        "M2": {"id": "M2", "sym": "ES=F", "status": "ok", "value": 7389.0, "prev": 7402.0, "chg_pct": -0.18, "as_of": "2026-11-09T16:46+09:00"},
        "M3": {"id": "M3", "sym": "NQ=F", "status": "stale", "value": 25000.0, "prev": 25280.0, "chg_pct": -1.11, "as_of": "2026-11-09T16:46+09:00"},
        "T4": {"id": "T4", "sym": "005930.KS", "status": "ok", "value": 189900.0, "chg_pct": 0.64, "lo20": 179200.0, "vs_ma20_pct": -12.75},
        "T5": {"id": "T5", "sym": "SOXL", "status": "ok", "value": 45.97, "leverage": 3},
        "U6": {"id": "U6", "sym": "^SOX", "status": "ok", "value": 9390.25, "chg_pct": 4.21},
        "T7": {"id": "T7", "sym": "MU", "status": "ok", "value": 690.0, "chg_pct": 5.12},
    }
    return {"date": "2026-11-09", "mode": "daily", "indices": ["M1"], "futures": ["M2", "M3"],
            "tickers": [], "items": items}


def news_fixture():
    return {"date": "2026-11-09", "items": [
        {"id": "N1", "title": "코스피 0.81% 하락 5,018.85 마감", "url": "https://n.example/1"},
        {"id": "N2", "title": "삼성전자 신제품", "url": "https://n.example/2"},
    ]}


def analysis_ok():
    return {"rows": [
        {"key": "market", "outlook": "중립", "summary": "코스피는 5,018.85로 0.81% 내렸다.", "checkpoint": "20일 저점 4,911.28",
         "nums": ["M1"], "src": ["N1"]},
        {"key": "KRX:005930", "outlook": "약약세", "summary": "20일선 대비 -12.75% 아래다.", "checkpoint": "지지 179,200",
         "nums": ["T4"], "src": ["N2"]},
        {"key": "NYSEARCA:SOXL", "outlook": "중립", "summary": "기초자산이 4.21% 올랐다.", "checkpoint": "", "nums": ["U6"], "src": []},
        {"key": "NASDAQ:MU", "outlook": "강세", "summary": "특별한 뉴스가 없다.", "checkpoint": "", "nums": [], "src": []},
    ]}


class TestCommon(unittest.TestCase):
    def test_ysym(self):
        self.assertEqual(C.ysym("KRX:005930", "KR"), "005930.KS")
        self.assertEqual(C.ysym("KOSDAQ:091990", "KR"), "091990.KQ")
        self.assertEqual(C.ysym("NYSE:BRK.B", "US"), "BRK-B")
        self.assertEqual(C.ysym("KRX:12", "KR"), "")

    def test_normalize_dedup_holding_first(self):
        i = inp_daily()
        self.assertEqual([h["ticker"] for h in i["holdings"]], ["KRX:005930", "NYSEARCA:SOXL"])
        self.assertEqual([h["ticker"] for h in i["watch"]], ["NASDAQ:MU"])
        self.assertEqual(i["holdings"][0]["market"], "KR")

    def test_date_must_be_today(self):
        today = C.now_kst().date().isoformat()
        os.environ.pop("OUTLOOK_DATE", None)
        self.assertEqual(C.date_errors({"date": today}), [])
        self.assertTrue(C.date_errors({"date": "2000-01-01"}))
        os.environ["OUTLOOK_DATE"] = "2000-01-01"
        try:
            self.assertEqual(C.date_errors({"date": "2000-01-01"}), [])
        finally:
            del os.environ["OUTLOOK_DATE"]

    def test_normalize_errors(self):
        _, e = C.normalize_input({"date": "11/09", "mode": "x", "holdings": [{"ticker": "KRX:1"}]})
        self.assertEqual(len(e), 3)


class TestMarket(unittest.TestCase):
    def test_quote_metrics(self):
        q = M.quote_metrics({"price": 110, "prev": 100, "at": 1000, "ccy": "KRW"}, CFG, 1000 + 3600)
        self.assertEqual((q["chg_pct"], q["status"]), (10.0, "ok"))
        q = M.quote_metrics({"price": 110, "prev": 100, "at": 0}, CFG, 97 * 3600)
        self.assertEqual(q["status"], "stale")
        self.assertEqual(M.quote_metrics({"price": 1}, CFG, 0)["status"], "unavailable")

    def test_daily_metrics(self):
        c = list(range(1, 251))
        m, ch = M.daily_metrics({"c": c, "h": c, "l": c}, 250, CFG)
        self.assertEqual(m["ma20"], sum(range(231, 251)) / 20)
        self.assertEqual((m["hi20"], m["lo20"], m["lo52"]), (250, 231, 1))
        self.assertEqual(ch, [])
        _, ch = M.daily_metrics({"c": c, "h": c, "l": c}, 260, CFG)
        self.assertTrue(ch and "차이" in ch[0])

    def test_kr_revision(self):
        rows = [{"date": "2026-10-08", "ticker": "KRX:005930", "f0end": "2026-12-31", "f0eps": 40000, "target": 400000},
                {"date": "2026-11-01", "ticker": "KRX:005930", "f0end": "2026-12-31", "f0eps": 1}]
        f = {"fy": [{"p": "0y", "end": "2026-12-31", "eps": 45655}], "tgt": 472381}
        r = M.kr_revision(rows, f, "2026-11-09")
        self.assertEqual(r["eps_rev_pct"], 14.14)
        self.assertEqual((r["eps_rev_days"], r["tgt_chg_pct"]), (32, 18.1))
        self.assertEqual(M.kr_revision(rows[1:], f, "2026-11-09"), {})

    def test_fund_metrics_us(self):
        m, ch = M.fund_metrics({"per": 9.28, "ntmEps": 182.4254, "tgt": 1561.9, "rev": {"r30": 0.004}, "stale": True}, 690)
        self.assertEqual((m["ntm_per"], m["eps_rev_pct"]), (3.78, 0.4))
        self.assertTrue(ch)


class TestNews(unittest.TestCase):
    def test_parse_pick(self):
        items = N.parse_rss((HERE / "fixtures" / "rss_sample.xml").read_bytes())
        self.assertEqual(items[0]["title"], "삼성전자, 신제품 출시")
        now = datetime(2026, 11, 9, 18, 0, tzinfo=C.KST)
        got = N.pick(items, now, 24, 5, set(), set())
        self.assertEqual([g["url"][-7:] for g in got], ["A1?oc=5", "A3?oc=5"])  # 같은 제목 중복·기간 밖 제외

    def test_queries_skip_leverage(self):
        q = N.queries(inp_daily(), CFG)
        scopes = [x[0] for x in q]
        self.assertNotIn("NYSEARCA:SOXL", scopes)
        self.assertIn(("NASDAQ:MU", "MU stock", "en", 3), q)

    def test_yahoo_queries(self):
        y = N.yahoo_queries(inp_daily(), CFG)
        self.assertIn(("NASDAQ:MU", "MU", 2), y)
        self.assertTrue(all(":" not in s and not s.endswith((".KS", ".KQ")) for _, s, _ in y))
        self.assertNotIn("NYSEARCA:SOXL", [x[0] for x in y])
        off = json.loads(json.dumps(CFG)); off["news"]["yahoo_rss"] = False
        self.assertEqual(N.yahoo_queries(inp_daily(), off), [])

    def test_yahoo_parse_default_source(self):
        xml = (b'<rss><channel><item><title>Micron rises</title>'
               b'<link>https://finance.yahoo.com/a.html?.tsrc=rss</link>'
               b'<pubDate>Tue, 10 Nov 2026 02:45:36 +0000</pubDate></item></channel></rss>')
        it = N.parse_rss(xml, "Yahoo Finance")[0]
        self.assertEqual((it["source"], it["title"]), ("Yahoo Finance", "Micron rises"))

    def test_collect_yahoo_fail_open(self):
        def fetch(url):
            if "yahoo" in url:
                raise OSError("HTTP 403")
            return (HERE / "fixtures" / "rss_sample.xml").read_bytes()
        res = N.collect(inp_daily(), CFG, fetch=fetch, now=datetime(2026, 11, 9, 18, 0, tzinfo=C.KST))
        self.assertTrue(any("yahoo" in e for e in res["errors"]))
        self.assertTrue(res["items"])


class TestBuild(unittest.TestCase):
    def build(self, a, extra=None):
        return B.build(inp_daily(), market_fixture(), news_fixture(), extra or {"items": []}, a, CFG, "X_v2")

    def test_numbers_in(self):
        got = [x[2] for x in B.numbers_in("2026-11-10 07:05 20일선 3거래일 5,018.85 -0.81% 690 1200원 11월 10일 2026년")]
        self.assertEqual(got, ["5,018.85", "-0.81%", "1200"])

    def test_ok(self):
        res, errs = self.build(analysis_ok())
        self.assertEqual(errs, [])
        self.assertEqual(res["sheet"], "X_v2")
        self.assertEqual([r[3] for r in res["rows"]], ["시장 전체", "삼성전자", "SOXL", "마이크론"])
        self.assertEqual([r[1] for r in res["rows"]], ["시장", "보유", "보유", "관심"])
        self.assertEqual(res["rows"][0][7], "https://n.example/1")
        self.assertEqual(res["delete_before"], "2026-10-10")

    def test_errors(self):
        a = analysis_ok()
        a["rows"][0]["summary"] = "코스피 5,123.45. 반드시 오른다. 셋째 문장. 넷째. 다섯째. 6. 7. 8. 9."
        a["rows"][1]["outlook"] = "상승"
        a["rows"][2]["src"] = ["N9"]
        a["rows"].pop()
        a["rows"].append({"key": "night", "outlook": "중립", "summary": "x"})
        _, errs = self.build(a)
        joined = "\n".join(errs)
        for s in ("NASDAQ:MU: analysis 에 행이 없음", "'5,123.45'", "금지 표현", "9문장", "허용값", "N9", "입력에 없는 행: NIGHT"):
            self.assertIn(s, joined)

    def test_stale_needs_note(self):
        a = analysis_ok()
        a["rows"][0].update({"summary": "나스닥 선물이 1.11% 내렸다.", "nums": ["M1", "M3"]})
        _, errs = self.build(a)
        self.assertTrue(any("ok 가 아닌" in e for e in errs))
        a["rows"][0]["summary"] = "나스닥 선물이 1.11% 내렸다(16:46 기준)."
        _, errs = self.build(a)
        self.assertEqual(errs, [])

    def test_dates_need_source(self):
        a = analysis_ok()
        a["rows"][1]["checkpoint"] = "1월 말 실적 발표 예상 · 11/20 행사"
        _, errs = self.build(a)
        joined = "\n".join(errs)
        self.assertIn("'1월'", joined)
        self.assertIn("'11/20'", joined)
        a["rows"][1]["checkpoint"] = "앱 신호: 11/6 매도·경계 · 11/9 기준"
        i = inp_daily()
        i["signals"] = [{"date": "2026-11-06", "ticker": "KRX:005930"}]
        _, errs = B.build(i, market_fixture(), news_fixture(), {"items": []}, a, CFG)
        self.assertEqual(errs, [])

    def test_extra_id_format(self):
        _, errs = self.build(analysis_ok(), {"items": [{"id": "X1", "url": "ftp://a"}]})
        self.assertEqual(len(errs), 2)


class TestWeekly(unittest.TestCase):
    def test_weekly_rows(self):
        i = inp_daily()
        i["mode"] = "weekly"
        a = {"rows": [
            {"key": "market", "s": "중립", "m": "약강세", "l": "강세", "summary": "코스피는 0.81% 내렸다. 둘째. 셋째.",
             "event": "11/12 CPI", "nums": ["M1"], "src": ["N1", "W1"]},
            {"key": "KRX:005930", "s": "약약세", "m": "중립", "l": "강세", "summary": "x", "event": "", "nums": [], "src": []},
            {"key": "NYSEARCA:SOXL", "s": "중립", "m": "중립", "l": "중립", "summary": "x", "event": "", "nums": [], "src": []},
            {"key": "NASDAQ:MU", "s": "강세", "m": "강세", "l": "강세", "summary": "x", "event": "", "nums": [], "src": []},
        ]}
        ex = {"items": [{"id": "W1", "url": "https://w.example/cal", "title": "주간 일정", "kind": "event",
                         "values": {"cpi_date": "2026-11-12"}}]}
        res, errs = B.build(i, market_fixture(), news_fixture(), ex, a, CFG)
        self.assertEqual(errs, [])
        self.assertEqual(res["sheet"], "App_전망")
        self.assertEqual(len(res["header"]), 11)
        self.assertEqual([len(r) for r in res["rows"]], [11] * 4)
        self.assertEqual(res["rows"][0][4:7], ["중립", "약강세", "강세"])
        self.assertEqual(res["delete_before"], "2026-08-17")


class TestDigest(unittest.TestCase):
    def test_digest(self):
        import collect as K
        m = market_fixture()
        m["tickers"] = [{"ticker": "KRX:005930", "name": "삼성전자", "kind": "보유", "id": "T4"},
                        {"ticker": "NYSEARCA:SOXL", "name": "SOXL", "kind": "보유", "id": "T5", "under_id": "U6"}]
        n = {"items": [{"id": "N1", "scope": "market", "published": "2026-11-09T16:49+09:00", "source": "S", "title": "t"}]}
        d = K.digest(inp_daily(), m, n)
        self.assertIn("T4 KRX:005930 삼성전자 [보유] 189,900 +0.64%", d)
        self.assertIn("20일선 -12.75%", d)
        self.assertIn("M3 ", d)
        self.assertIn("[stale]", d)
        self.assertIn("기초자산 U6", d)
        self.assertIn("U6 ^SOX", d)
        self.assertIn("N1 [market] 11-09 16:49 S | t", d)
        # 요약의 숫자는 검증기가 인용 값으로 인정하는 형식이어야 한다
        for v, dec, raw in B.numbers_in("189,900 +0.64% -12.75% 9,390.25"):
            self.assertTrue(B.matches(v, dec, B.flat_numbers(m["items"])), raw)


class TestPlanVerify(unittest.TestCase):
    def rows(self):
        res, _ = B.build(inp_daily(), market_fixture(), news_fixture(), {"items": []}, analysis_ok(), CFG,
                         now=datetime(2026, 11, 9, 7, 25, tzinfo=C.KST))
        return json.loads(json.dumps(res))

    def cola(self, dates, sheet="App_일간전망"):
        return {"range": f"'{sheet}'!A1:A{len(dates) + 1}", "values": [["작성일"]] + [[d] for d in dates]}

    def test_written_at_column(self):
        r = self.rows()
        self.assertEqual(r["header"][-1], "작성시각")
        self.assertEqual(r["rows"][0][-1], "2026-11-09 07:25")

    def test_plan_deletes_today_and_old(self):
        r = self.rows()
        dates = ["2026-10-01", "2026-10-01", "2026-10-20", "2026-11-09", "2026-11-09"]
        p = W.plan(r, 77, self.cola(dates))
        # 아래쪽(오늘 5~6행)부터, 그다음 보관 기간 지난 2~3행
        self.assertEqual([x["deleteDimension"]["range"]["startIndex"] for x in p["requests"]], [4, 1])
        self.assertEqual([x["deleteDimension"]["range"]["endIndex"] for x in p["requests"]], [6, 3])
        self.assertEqual(p["expect"]["start"], 3)  # 헤더 + 10-20 한 행 남음
        self.assertEqual(p["readback"], ["'App_일간전망'!A1:I2", f"'App_일간전망'!A3:I{2 + len(r['rows'])}"])
        self.assertIsNone(p["header_update"])
        head = {"range": "'App_일간전망'!A1:Z1", "values": [r["header"][:8]]}
        self.assertTrue(W.plan(r, 77, self.cola(dates), head)["header_update"])  # 작성시각 열 추가

    def test_plan_rejects_wrong_sheet_or_raw(self):
        r = self.rows()
        with self.assertRaises(ValueError):
            W.plan(r, 1, self.cola([], "다른시트"))
        with self.assertRaises(ValueError):
            W.plan(r, 1, {"values": [["작성일"]]})

    def responses(self, p, mutate=None):
        vals = [list(x) for x in p["append"]["values"]]
        app = {"updates": {"updatedRange": f"'{p['sheet']}'!A{p['expect']['start']}:I{p['expect']['end']}",
                           "updatedRows": p["expect"]["rows"]}}
        rb1 = {"range": p["readback"][0], "values": [p["header"], ["2026-10-20", "시장", "", "시장 전체", "중립"]]}
        body = [list(x) for x in vals]
        body[0][0] = "2026. 11. 9"
        body[0][-1] = "2026. 11. 9 오전 7:25:00"  # 시트가 날짜·시각으로 바꿔 보여준 경우
        if mutate:
            mutate(body)
        rb2 = {"range": p["readback"][1], "values": body}
        return app, [rb1, rb2]

    def test_verify_ok(self):
        p = W.plan(self.rows(), 77, self.cola(["2026-10-20"]))
        app, rbs = self.responses(p)
        self.assertEqual(V.verify(p, app, rbs), [])

    def test_verify_rejects_fabricated_readback(self):
        p = W.plan(self.rows(), 77, self.cola(["2026-10-20"]))
        app, rbs = self.responses(p)
        fake = [{"values": rbs[0]["values"]}, {"values": rbs[1]["values"]}]  # rows.json 에서 만든 형태
        self.assertTrue(any("원문이 아님" in e for e in V.verify(p, app, fake)))
        self.assertTrue(any("원문이 아님" in e for e in V.verify(p, {"rows": 1}, rbs)))

    def test_verify_position_and_value(self):
        p = W.plan(self.rows(), 77, self.cola(["2026-10-20"]))
        app, rbs = self.responses(p, lambda b: b[1].__setitem__(4, "강세" if b[1][4] != "강세" else "약세"))
        app["updates"]["updatedRange"] = "'App_일간전망'!A9:I12"
        errs = "\n".join(V.verify(p, app, rbs))
        self.assertIn("추가 위치", errs)
        self.assertIn("오늘 전망", errs)

    def test_verify_old_rows_left(self):
        p = W.plan(self.rows(), 77, self.cola(["2026-10-20"]))
        app, rbs = self.responses(p)
        rbs[0]["values"][1][0] = "2026-09-01"
        self.assertTrue(any("보관 기간" in e for e in V.verify(p, app, rbs)))


def cal_raw():
    return {"status": "ok", "result": [
        {"id": "1", "title": "Inflation Rate YoY", "country": "US", "date": "2026-11-10T13:30:00.000Z", "importance": 1,
         "actual": "4.1", "actualRaw": 4.1, "forecast": "3.9", "forecastRaw": 3.9, "previous": "3.7", "previousRaw": 3.7,
         "unit": "%", "period": "Oct. 2026"},
        {"id": "2", "title": "Non Farm Payrolls", "country": "US", "date": "2026-11-06T13:30:00.000Z", "importance": 1,
         "actual": "165", "actualRaw": 165000, "forecast": "65", "forecastRaw": 65000, "previous": "60", "previousRaw": 60000},
        {"id": "3", "title": "Interest Rate Decision", "country": "KR", "date": "2026-11-26T01:00:00.000Z", "importance": 0,
         "actual": None, "actualRaw": 0, "forecast": None, "previous": "3", "previousRaw": 3},
        {"id": "4", "title": "3-Month Bill Auction Bid-to-Cover", "country": "US", "date": "2026-11-09T16:30:00.000Z",
         "importance": 0, "actual": "2.67", "actualRaw": 2.67},
        {"id": "5", "title": "Something minor", "country": "US", "date": "2026-11-09T16:30:00.000Z", "importance": -1},
    ]}


class TestCalendar(unittest.TestCase):
    def test_judge_bias(self):
        j = K.judge_bias("Inflation Rate YoY", "", 1, 4.1, 3.9, 3.7, "4.1", "3.9", "3.7")
        self.assertEqual((j["bias"], j["category"]), ("악재", "inflation"))  # 물가 예상 상회
        self.assertLess(j["score"], -0.5)
        self.assertEqual(K.judge_bias("Fed Interest Rate Decision", "", 1, 3.5, None, 3.75, "3.5", None, "3.75")["bias"], "호재")
        self.assertEqual(K.judge_bias("Fed Interest Rate Decision", "", 1, 3.75, None, 3.75, "3.75", None, "3.75")["bias"], "중립")
        nfp = K.judge_bias("Non Farm Payrolls", "", 1, 165000, 65000, 60000, "165", "65", "60")
        self.assertEqual(nfp["bias"], "혼조")
        self.assertEqual(K.judge_bias("CPI", "", 1, None, 3.0, 2.9)["bias"], "예정")

    def test_collect(self):
        now = datetime(2026, 11, 11, 7, 0, tzinfo=C.KST)
        res = K.collect({"date": "2026-11-11", "mode": "daily"}, CFG, fetch=lambda u: json.dumps(cal_raw()), now=now)
        titles = [res["items"][i]["title"] for i in res["order"]]
        self.assertNotIn("3-Month Bill Auction Bid-to-Cover", titles)
        self.assertNotIn("Something minor", titles)
        kr = [res["items"][i] for i in res["order"] if res["items"][i]["country"] == "KR"][0]
        self.assertTrue(kr["high"])  # 금통위는 중요도 0 이어도 고영향
        self.assertEqual(kr["bias"], "예정")
        cpi = res["items"]["C2"]
        self.assertEqual((cpi["title_ko"], cpi["at"]), ("CPI 전년비", "2026-11-10T22:30+09:00"))
        self.assertEqual(res["tone"]["count"], 2)

    def test_fail_open(self):
        def boom(u):
            raise OSError("403")
        res = K.collect({"date": "2026-11-11", "mode": "daily"}, CFG, fetch=boom)
        self.assertEqual(res["status"], "unavailable")


class TestNewsScore(unittest.TestCase):
    def test_negation_and_tags(self):
        self.assertEqual(S.score("Fed signals no recession ahead")["hint"], "중립")
        s = S.score("Micron plunges as memory glut worsens", [("NASDAQ:MU", "MU")])
        self.assertEqual(s["hint"], "악재")
        self.assertEqual(s["tags"][0], "MU")
        self.assertIn("메모리", s["tags"])
        self.assertEqual(S.score("삼성전자 급등, 외국인 순매수")["hint"], "호재")

    def test_relevant(self):
        self.assertEqual(S.relevant("Nvidia shares drop", "NASDAQ:NVDA"), "alias")
        self.assertEqual(S.relevant("Warby Parker prospects brighter", "NASDAQ:GOOG"), "")
        self.assertEqual(S.relevant("Chip stocks rebound", "NASDAQ:INTC"), "sector")

    def test_collect_drops_irrelevant_yahoo(self):
        xml = (b'<rss><channel><item><title>Warby Parker prospects brighter</title><link>https://y/1</link>'
               b'<pubDate>Mon, 09 Nov 2026 08:00:00 +0000</pubDate></item>'
               b'<item><title>Micron rises on HBM demand</title><link>https://y/2</link>'
               b'<pubDate>Mon, 09 Nov 2026 08:00:00 +0000</pubDate></item></channel></rss>')
        res = N.collect(inp_daily(), CFG, fetch=lambda u: xml if "yahoo" in u else b"<rss><channel></channel></rss>",
                        now=datetime(2026, 11, 9, 18, 0, tzinfo=C.KST))
        titles = [x["title"] for x in res["items"]]
        self.assertIn("Micron rises on HBM demand", titles)
        self.assertNotIn("Warby Parker prospects brighter", titles)
        it = [x for x in res["items"] if x["title"].startswith("Micron")][0]
        self.assertEqual(it["hint"], "호재")


class TestNormCell(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(V.norm_cell("4.20%"), V.norm_cell("'4.2%"))
        self.assertEqual(V.norm_cell("1,234.50"), V.norm_cell("1234.5"))
        self.assertEqual(V.norm_cell("2026. 11. 10 오후 5:27:00"), V.norm_cell("2026-11-10 17:27"))
        self.assertNotEqual(V.norm_cell("4.2%"), V.norm_cell("4.2"))


class TestGuardHook(unittest.TestCase):
    """훅(.claude/hooks/sheets_guard.py)과 기록 기반 plan_write·verify_written 흐름."""

    def setUp(self):
        import importlib.util
        import tempfile
        import sheetlog as L
        self.L = L
        self.tmp = tempfile.TemporaryDirectory()
        w = Path(self.tmp.name)
        self.saved = (L.WORK, L.GUARD, L.LOG)
        L.WORK, L.GUARD, L.LOG = w, w / "guard.json", w / "sheet_log.jsonl"
        spec = importlib.util.spec_from_file_location("sheets_guard", HERE.parents[2] / ".claude" / "hooks" / "sheets_guard.py")
        self.H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.H)
        self.H.L = L

    def tearDown(self):
        self.L.WORK, self.L.GUARD, self.L.LOG = self.saved
        self.tmp.cleanup()

    def call(self, mode, h):
        import contextlib
        import io
        buf = io.StringIO()
        if not self.L.load_guard():
            return None
        with contextlib.redirect_stdout(buf):
            try:
                (self.H.pre if mode == "pre" else self.H.post)(h, self.L.load_guard())
            except SystemExit:
                pass
        s = buf.getvalue().strip()
        return json.loads(s)["hookSpecificOutput"] if s else None

    def post(self, tool, ti, resp, tid="t"):
        return self.call("post", {"tool_name": f"mcp__Google-Sheets__{tool}", "tool_input": ti, "tool_use_id": tid,
                                  "tool_response": [{"type": "text", "text": json.dumps(resp, ensure_ascii=False)}]})

    def pre(self, tool, ti, tid="t"):
        return self.call("pre", {"tool_name": f"mcp__Google-Sheets__{tool}", "tool_input": ti, "tool_use_id": tid})

    def test_content_wrapper(self):
        L = self.L
        meta = {"sheets": [{"properties": {"title": "S", "sheetId": 5}}]}
        self.assertEqual(L.resp_json({"content": meta}), meta)
        self.assertEqual(L.resp_json({"content": json.dumps(meta)}), meta)
        self.assertEqual(L.resp_json({"content": [{"type": "text", "text": json.dumps({"content": meta})}]}), meta)
        L.log_append({"phase": "post", "tool": "get_spreadsheet", "response": {"content": meta}})
        self.assertEqual(L.log_read()[-1]["response"], meta)

    def test_flow(self):
        L = self.L
        self.assertIsNone(self.pre("append_values", {"range": "'X'!A1", "values": [[1]]}))  # guard 꺼짐: 관여 안 함
        res, _ = B.build(inp_daily(), market_fixture(), news_fixture(), {"items": []}, analysis_ok(), CFG,
                         now=datetime(2026, 11, 9, 7, 25, tzinfo=C.KST))
        res = json.loads(json.dumps(res))
        sh = res["sheet"]
        L.guard_add(sh)
        self.post("get_spreadsheet", {}, {"sheets": [{"properties": {"title": sh, "sheetId": 77}}]}, "r1")
        dates = ["2026-10-01", "2026-10-20", "2026-11-09"]
        big = self.post("get_values", {"range": f"'{sh}'!A:A"},
                        {"range": f"'{sh}'!A1:A4", "values": [["작성일"]] + [[d] for d in dates] + [["x" * 2000]][:0]}, "r2")
        self.post("get_values", {"range": f"'{sh}'!1:1"}, {"range": f"'{sh}'!A1:Z1", "values": [res["header"][:8]]}, "r3")
        p = W.from_log(res)
        self.assertEqual((p["deleted_rows"], p["expect"]["start"]), (2, 3))
        self.assertTrue(p["header_update"])
        (L.WORK / "plan.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        # 세션이 값을 직접 보내면 막는다
        d = self.pre("append_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["직접"]]})
        self.assertEqual(d["permissionDecision"], "deny")
        # 다른 시트 쓰기 금지
        self.assertEqual(self.pre("update_values", {"range": "'총자산'!A1", "values": [["@plan"]]})["permissionDecision"], "deny")
        # 헤더·삭제 전 추가 금지
        self.assertIn("헤더", self.pre("append_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["@plan"]]})["permissionDecisionReason"])
        hd = self.pre("update_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["@plan"]]}, "h1")
        self.assertEqual((hd["permissionDecision"], hd["updatedInput"]["values"]), ("allow", [res["header"]]))
        self.post("update_values", hd["updatedInput"], {"updatedRange": f"'{sh}'!A1:I1"}, "h1")
        self.assertIn("삭제", self.pre("append_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["@plan"]]})["permissionDecisionReason"])
        dl = self.pre("update_spreadsheet", {"spreadsheetId": "S", "requests": [{"@plan": "delete"}]}, "d1")
        self.assertEqual(dl["updatedInput"]["requests"], p["requests"])
        self.post("update_spreadsheet", dl["updatedInput"], {"replies": [{}]}, "d1")
        self.assertEqual(self.pre("update_spreadsheet", {"spreadsheetId": "S", "requests": [{"@plan": "delete"}]})["permissionDecision"], "deny")  # 두 번 삭제 금지
        ap = self.pre("append_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["@plan"]]}, "a1")
        self.assertEqual(ap["updatedInput"]["values"], p["append"]["values"])
        s, e = p["expect"]["start"], p["expect"]["end"]
        self.post("append_values", ap["updatedInput"],
                  {"updates": {"updatedRange": f"'{sh}'!A{s}:I{e}", "updatedRows": p["expect"]["rows"]}}, "a1")
        self.assertEqual(self.pre("append_values", {"spreadsheetId": "S", "range": f"'{sh}'!A1", "values": [["@plan"]]})["permissionDecision"], "deny")
        _, _, miss = V.from_log(p)
        self.assertTrue(miss)  # 재조회 전
        self.post("get_values", {"range": p["readback"][0]}, {"range": p["readback"][0], "values": [res["header"], ["2026-10-20"]]}, "v1")
        rb = [list(x) for x in p["append"]["values"]]
        out = self.post("get_values", {"range": p["readback"][1]}, {"range": p["readback"][1], "values": rb, "pad": "y" * 2000}, "v2")
        self.assertIn("rows", out["updatedMCPToolOutput"][0]["text"])  # 큰 응답은 요약만 보인다
        app, rbs, errs = V.from_log(p)
        self.assertEqual(errs, [])
        self.assertEqual(V.verify(p, app, rbs), [])


NVDA_DATE = {"data": {"reportText": "NVIDIA Corporation Common Stock is expected* to report earnings on  11/17/2026 after "
                      "market close.  The report will be for the fiscal Quarter ending Oct 2026.  According to Zacks Investment "
                      "Research, based on  13 analysts'  forecasts, the consensus EPS forecast for the quarter is $2.49.  The "
                      "reported EPS for the same quarter last year was $1.24.",
                      "announcement": "Earnings announcement* for NVDA: Nov 17, 2026"}}
NVDA_SURP = {"data": {"earningsSurpriseTable": {"rows": [
    {"fiscalQtrEnd": "Jul 2026", "dateReported": "8/26/2026", "eps": 2.22, "consensusForecast": "2.09", "percentageSurprise": "6.22"}]}}}


class TestEvents(unittest.TestCase):
    def test_parse(self):
        p = E.parse_earnings_date(NVDA_DATE)
        self.assertEqual((p["date"], p["time"], p["confirm"], p["fiscal"]), ("2026-11-17", "장 마감 후", "예정", "Oct 2026"))
        self.assertEqual((p["consensus_eps"], p["prev_eps"]), (2.49, 1.24))
        est = {"data": {"reportText": "Micron is estimated to report earnings on  12/16/2026. The upcoming earnings date is "
                        "derived from an algorithm", "announcement": ""}}
        self.assertEqual(E.parse_earnings_date(est)["confirm"], "예상")
        self.assertIsNone(E.parse_earnings_date({"data": {}}))
        self.assertEqual(E.parse_surprise(NVDA_SURP)[0]["surprise_pct"], 6.22)

    def collect(self):
        inp, _ = C.normalize_input({"date": "2026-11-10", "mode": "daily", "holdings": [
            {"ticker": "NASDAQ:NVDA", "name": "NVDA"}, {"ticker": "NYSEARCA:SOXL", "name": "SOXL"},
            {"ticker": "KRX:069500", "name": "KODEX 200"}, {"ticker": "KRX:005930", "name": "삼성전자"}]})
        market = {"tickers": [{"ticker": "NASDAQ:NVDA", "id": "T1"}, {"ticker": "NYSEARCA:SOXL", "id": "T2"},
                              {"ticker": "KRX:069500", "id": "T3"}, {"ticker": "KRX:005930", "id": "T4"}],
                  "items": {"T1": {"next_earnings": "2026-11-18"}, "T2": {}, "T3": {"qtype": "ETF"},
                            "T4": {"next_earnings": "2027-01-29"}}}
        cfg = json.loads(json.dumps(CFG)); cfg["http"]["delay_sec"] = 0
        return E.collect(inp, market, cfg, fetch=lambda u: json.dumps(NVDA_SURP if "surprise" in u else NVDA_DATE))

    def test_collect(self):
        res = self.collect()
        self.assertEqual(set(res["skipped"]), {"NYSEARCA:SOXL", "KRX:069500"})
        its = [res["items"][i] for i in res["order"]]
        nx = [o for o in its if o["ticker"] == "NASDAQ:NVDA" and o["status"] == "예정"][0]
        self.assertEqual((nx["days"], nx["xstatus"]), (7, "ok"))
        self.assertIn("차이 1일", nx["check"])
        self.assertTrue(any(o["ticker"] == "KRX:005930" and o["date"] == "2027-01-29" for o in its))
        self.assertTrue(any(o["status"] == "발표" for o in its))

    def test_events_rows_and_upsert(self):
        res = self.collect()
        now = datetime(2026, 11, 11, 7, 0, tzinfo=C.KST)
        cal = K.collect({"date": "2026-11-11", "mode": "daily"}, CFG, fetch=lambda u: json.dumps(cal_raw()), now=now)
        er = BE.build(cal, res, CFG, now=now)
        ids = [r[0] for r in er["rows"]]
        self.assertIn("ER-NASDAQ:NVDA-NEXT", ids)
        self.assertIn("ER-NASDAQ:NVDA-2026-08-26", ids)
        self.assertIn("TV-3", ids)
        h = er["header"]
        vi = h.index("버전")
        # 시트에 같은 버전 1행, 다른 버전 1행이 있다
        same, diff = er["rows"][0], er["rows"][1]
        cola = {"range": "'App_주요일정'!A1:B4", "values": [h[:2], [same[0], same[vi]], [diff[0], "v0"], ["TV-old", "v1"]]}
        p = W.plan(er, 9, cola)
        self.assertEqual(p["deleted_rows"], 1)
        self.assertEqual(p["expect"]["rows"], len(er["rows"]) - 1)
        self.assertEqual(p["expect"]["start"], 4)  # 헤더 + 남은 2행 다음
        self.assertNotIn(same, p["append"]["values"])
        self.assertEqual(p["readback"], [f"'App_주요일정'!A4:Q{3 + p['expect']['rows']}"])


if __name__ == "__main__":
    unittest.main()
