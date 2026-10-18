// 주식일지 주가 차트 프록시 (Cloudflare Worker)
// 브라우저는 Yahoo Finance 차트 API를 직접 부를 수 없어(CORS) 이 Worker가 대신 받아 필요한 값만 돌려줍니다.
// 받는 것: 심볼과 기간뿐. 시트 데이터·토큰·계정 정보는 전달되지 않습니다.
//
// GET /chart?s=<Yahoo 심볼>&r=<1d|5d|1mo|3mo|1y|5y>
//   국장: 005930.KS (코스피) / 035720.KQ (코스닥) — .KS로 못 찾으면 .KQ로 한 번 더 찾음
//   미장: MU, AAPL, BRK-B …
// 환경 변수(선택) ALLOWED_ORIGINS: 허용할 앱 주소, 쉼표 구분. 기본값은 아래 ALLOW_DEFAULT.

const ALLOW_DEFAULT = 'https://sharkyj.github.io,http://localhost:8000';
const RANGES = { '1d': ['1d', '5m'], '5d': ['5d', '30m'], '1mo': ['1mo', '1d'], '3mo': ['3mo', '1d'], '1y': ['1y', '1d'], '5y': ['5y', '1wk'] };
const MEM = new Map(); // 같은 인스턴스 안에서의 짧은 캐시 (workers.dev에서는 Cache API가 동작하지 않음)

export default {
  async fetch(req, env) {
    const allow = String(env.ALLOWED_ORIGINS || ALLOW_DEFAULT).split(',').map(s => s.trim()).filter(Boolean);
    const origin = req.headers.get('Origin') || '';
    const cors = {
      'Access-Control-Allow-Origin': allow.includes(origin) ? origin : allow[0],
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Max-Age': '86400',
      'Vary': 'Origin',
    };
    const json = (o, status = 200) => new Response(JSON.stringify(o), {
      status,
      headers: { ...cors, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
    });
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    // 다른 웹사이트가 이 Worker를 가져다 쓰지 못하게 브라우저 출처를 제한 (Origin 없는 직접 호출은 테스트용으로 허용)
    if (origin && !allow.includes(origin)) return json({ error: '허용되지 않은 출처입니다' }, 403);

    const url = new URL(req.url);
    if (req.method !== 'GET' || url.pathname !== '/chart') return json({ error: 'not found' }, 404);
    const s = (url.searchParams.get('s') || '').toUpperCase();
    const r = url.searchParams.get('r') || '3mo';
    if (!/^[A-Z0-9][A-Z0-9.\-^=]{0,19}$/.test(s) || !RANGES[r]) return json({ error: '잘못된 요청입니다' }, 400);

    const key = s + '|' + r;
    const hit = MEM.get(key);
    if (hit && hit.exp > Date.now()) return json(hit.data);

    let out = await yahoo(s, r);
    if (!out && s.endsWith('.KS')) out = await yahoo(s.slice(0, -3) + '.KQ', r);
    if (!out) return json({ error: '시세를 찾을 수 없습니다: ' + s }, 404);
    if (out.error) return json(out, 502);

    const intraday = RANGES[r][1].endsWith('m');
    const ttl = intraday ? (out.open ? 30 : 300) : (out.open ? 120 : 1800);
    if (MEM.size > 200) MEM.clear();
    MEM.set(key, { exp: Date.now() + ttl * 1000, data: out });
    return json(out);
  },
};

async function yahoo(s, r) {
  const [range, interval] = RANGES[r];
  let lastErr = 'Yahoo 연결 실패';
  for (const host of ['query1', 'query2']) {
    let res;
    try {
      res = await fetch(`https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?range=${range}&interval=${interval}&includePrePost=false`, {
        headers: { 'User-Agent': 'Mozilla/5.0 (compatible; stock-journal-chart/1.0)', 'Accept': 'application/json' },
      });
    } catch (e) { continue; }
    if (res.status === 404) return null;
    if (!res.ok) { lastErr = 'Yahoo 응답 오류 ' + res.status; continue; }
    const j = await res.json().catch(() => null);
    const R = j && j.chart && j.chart.result && j.chart.result[0];
    if (!R || !R.timestamp || !R.timestamp.length) return null;
    const m = R.meta || {};
    const q = (R.indicators && R.indicators.quote && R.indicators.quote[0]) || {};
    const out = {
      s: m.symbol || s, ccy: m.currency || '', tz: m.exchangeTimezoneName || '', gmt: m.gmtoffset || 0,
      prev: m.chartPreviousClose ?? m.previousClose ?? null, price: m.regularMarketPrice ?? null, at: m.regularMarketTime || 0,
      t: [], o: [], h: [], l: [], c: [], v: [],
    };
    const rd = x => Math.round(x * 1e4) / 1e4;
    R.timestamp.forEach((t, i) => {
      const c = q.close && q.close[i];
      if (c == null) return;
      const pick = a => (a && a[i] != null ? rd(a[i]) : rd(c));
      out.t.push(t); out.o.push(pick(q.open)); out.h.push(pick(q.high)); out.l.push(pick(q.low)); out.c.push(rd(c));
      out.v.push((q.volume && q.volume[i]) || 0);
    });
    if (!out.t.length) return null;
    const p = m.currentTradingPeriod && m.currentTradingPeriod.regular;
    const now = Date.now() / 1000;
    out.open = !!(p && now >= p.start && now < p.end);
    return out;
  }
  return { error: lastErr };
}
