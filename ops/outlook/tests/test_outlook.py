"""전망 파이프라인 단위 테스트 (네트워크 없음).

실행: cd ops/outlook && python3 -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import build_rows as B  # noqa: E402
import collect_market as M  # noqa: E402
import collect_news as N  # noqa: E402
import common as C  # noqa: E402
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
        a["rows"][0]["summary"] = "코스피 5,123.45. 반드시 오른다. 셋째 문장."
        a["rows"][1]["outlook"] = "상승"
        a["rows"][2]["src"] = ["N9"]
        a["rows"].pop()
        a["rows"].append({"key": "night", "outlook": "중립", "summary": "x"})
        _, errs = self.build(a)
        joined = "\n".join(errs)
        for s in ("NASDAQ:MU: analysis 에 행이 없음", "'5,123.45'", "금지 표현", "3문장", "허용값", "N9", "입력에 없는 행: NIGHT"):
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
        self.assertEqual(len(res["header"]), 10)
        self.assertEqual([len(r) for r in res["rows"]], [10] * 4)
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


class TestVerify(unittest.TestCase):
    def rows(self):
        res, _ = B.build(inp_daily(), market_fixture(), news_fixture(), {"items": []}, analysis_ok(), CFG)
        return json.loads(json.dumps(res))

    def test_match(self):
        r = self.rows()
        vals = [r["header"], ["2026-10-20", "시장", "", "시장 전체"]] + [list(x) for x in r["rows"]]
        vals[2][0] = "2026. 11. 9"
        self.assertEqual(V.verify(r, {"values": vals}), [])

    def test_partial_readback_with_dates(self):
        r = self.rows()
        vals = [r["header"]] + [list(x) for x in r["rows"]]
        self.assertEqual(V.verify(r, {"values": vals, "dates": [["작성일"], ["2026-11-01"], ["2026-11-09"]]}), [])
        errs = V.verify(r, {"values": vals, "dates": [["2026-09-30"]]})
        self.assertTrue(any("보관 기간" in e for e in errs))

    def test_cols_partial(self):
        r = self.rows()
        vals = [r["header"][:5]] + [list(x[:5]) for x in r["rows"]]
        self.assertEqual(V.verify(r, {"values": vals}, cols=5), [])
        vals[1][4] = "강세" if vals[1][4] != "강세" else "약세"
        self.assertTrue(V.verify(r, {"values": vals}, cols=5))

    def test_mismatch_and_old(self):
        r = self.rows()
        vals = [r["header"], ["2026-10-01", "시장"]] + [list(x) for x in r["rows"][:-1]]
        vals[3][5] = "다른 요약"
        errs = V.verify(r, {"values": vals})
        joined = "\n".join(errs)
        self.assertIn("행 수", joined)
        self.assertIn("요약", joined)
        self.assertIn("보관 기간", joined)


if __name__ == "__main__":
    unittest.main()
