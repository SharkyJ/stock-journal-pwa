// 주식일지 시세·재무 프록시 (Cloudflare Worker)
// 브라우저는 Yahoo Finance·네이버 증권 API를 직접 부를 수 없어(CORS) 이 Worker가 대신 받아 필요한 값만 돌려줍니다.
// 받는 것: 심볼과 기간뿐. 시트 데이터·토큰·계정 정보는 전달되지 않습니다.
//
// GET /chart?s=<Yahoo 심볼>&r=<1d|5d|1mo|3mo|1y|5y|2y|5yd>
//   국장: 005930.KS (코스피) / 035720.KQ (코스닥) — .KS로 못 찾으면 .KQ로 한 번 더 찾음
//   미장: MU, AAPL, BRK-B …  지수: ^KS11, ^GSPC, ^SOX …  환율: KRW=X
//   2y = 2년 일봉(지표·신호 계산용), 5yd = 5년 일봉(과거 적중률 계산용)
// GET /fund?s=<Yahoo 심볼>
//   재무·컨센서스. 국장은 네이버 증권(추정 PER·EPS, 목표주가), 미장은 Yahoo quoteSummary(포워드 PER 등)
//   둘 다 비공식 API라 응답 형식이 바뀌면 이 파일만 고치면 됩니다.
// 환경 변수(선택) ALLOWED_ORIGINS: 허용할 앱 주소, 쉼표 구분. 기본값은 아래 ALLOW_DEFAULT.

const ALLOW_DEFAULT = 'https://sharkyj.github.io,http://localhost:8000';
const RANGES = {
  '1d': ['1d', '5m'], '5d': ['5d', '30m'], '1mo': ['1mo', '1d'], '3mo': ['3mo', '1d'], '1y': ['1y', '1d'], '5y': ['5y', '1wk'],
  '2y': ['2y', '1d'], '5yd': ['5y', '1d'],
};
const UA = { 'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36', 'Accept': 'application/json' };
const MEM = new Map(); // 같은 인스턴스 안에서의 짧은 캐시 (workers.dev에서는 Cache API가 동작하지 않음)
let YC = null;         // Yahoo 쿠키·crumb (quoteSummary용)

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
    if (req.method !== 'GET' || (url.pathname !== '/chart' && url.pathname !== '/fund')) return json({ error: 'not found' }, 404);
    const s = (url.searchParams.get('s') || '').toUpperCase();
    if (!/^[A-Z0-9^][A-Z0-9.\-^=]{0,19}$/.test(s)) return json({ error: '잘못된 요청입니다' }, 400);

    if (url.pathname === '/fund') {
      const key = 'F|' + s;
      const hit = MEM.get(key);
      if (hit && hit.exp > Date.now()) return json(hit.data);
      const out = await fundamentals(s);
      if (out.error) return json(out, 502);
      remember(key, out, 6 * 3600);
      return json(out);
    }

    const r = url.searchParams.get('r') || '3mo';
    if (!RANGES[r]) return json({ error: '잘못된 요청입니다' }, 400);
    const key = s + '|' + r;
    const hit = MEM.get(key);
    if (hit && hit.exp > Date.now()) return json(hit.data);

    let out = await chart(s, r);
    if (!out && s.endsWith('.KS')) out = await chart(s.slice(0, -3) + '.KQ', r);
    if (!out) return json({ error: '시세를 찾을 수 없습니다: ' + s }, 404);
    if (out.error) return json(out, 502);

    const intraday = RANGES[r][1].endsWith('m');
    remember(key, out, intraday ? (out.open ? 30 : 300) : (out.open ? 120 : 1800));
    return json(out);
  },
};

function remember(key, data, ttlSec) {
  if (MEM.size > 300) MEM.clear();
  MEM.set(key, { exp: Date.now() + ttlSec * 1000, data });
}

async function chart(s, r) {
  const [range, interval] = RANGES[r];
  let lastErr = 'Yahoo 연결 실패';
  for (const host of ['query1', 'query2']) {
    let res;
    try {
      res = await fetch(`https://${host}.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?range=${range}&interval=${interval}&includePrePost=false`, { headers: UA });
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

/* ---------- 재무·컨센서스 ---------- */
// 공통 형식 (없는 값은 null)
//   per 실적 PER, fper 추정(포워드) PER, eps, feps 추정 EPS, pbr, peg, roe(비율), divy 배당수익률(비율), beta,
//   tgt/tgtHi/tgtLo 목표주가, nAn 애널리스트 수, rec 투자의견(5=강력매수 … 1=매도), revG/epsG 성장률(비율),
//   earn 다음 실적 발표(epoch 초), type EQUITY|ETF|…, name, ccy, src
const blank = s => ({ s, src: '', type: '', name: '', ccy: '', per: null, fper: null, eps: null, feps: null, pbr: null, peg: null, roe: null, divy: null, beta: null, tgt: null, tgtHi: null, tgtLo: null, nAn: null, rec: null, revG: null, epsG: null, earn: null, at: Math.floor(Date.now() / 1000) });

async function fundamentals(s) {
  const kr = /^[0-9A-Z]{6}\.(KS|KQ)$/.test(s);
  if (kr) {
    const n = await naver(s).catch(() => null);
    if (n) return n;
  }
  const y = await yahooSummary(s).catch(e => ({ error: '재무 정보를 가져오지 못했습니다: ' + e.message }));
  if (y && !y.error) return y;
  if (kr) return { ...blank(s), src: 'none' }; // 국장 ETF 등 재무가 없는 종목
  return y;
}

// 네이버 증권 모바일 API: totalInfos[{code,key,value}] + consensusInfo
async function naver(s) {
  const code = s.slice(0, 6);
  const res = await fetch(`https://m.stock.naver.com/api/stock/${code}/integration`, { headers: { ...UA, 'Referer': 'https://m.stock.naver.com/' } });
  if (!res.ok) return null;
  const j = await res.json().catch(() => null);
  if (!j || !Array.isArray(j.totalInfos)) return null;
  const num = v => {
    if (v == null) return null;
    const m = String(v).replace(/,/g, '').match(/-?\d+(\.\d+)?/);
    return m ? +m[0] : null;
  };
  const ti = {};
  j.totalInfos.forEach(x => { if (x && x.code) ti[x.code] = x.value; });
  const ci = j.consensusInfo || {};
  const out = blank(s);
  Object.assign(out, {
    src: 'naver', type: 'EQUITY', name: j.stockName || '', ccy: 'KRW',
    per: num(ti.per), fper: num(ti.cnsPer), eps: num(ti.eps), feps: num(ti.cnsEps), pbr: num(ti.pbr),
    divy: ti.dividendYieldRatio != null && num(ti.dividendYieldRatio) != null ? Math.round(num(ti.dividendYieldRatio) * 100) / 1e4 : null,
    tgt: num(ci.priceTargetMean), rec: num(ci.recommMean),
  });
  if (out.per == null && out.fper == null && out.pbr == null && out.tgt == null) return null; // ETF 등 → Yahoo로
  return out;
}

async function yCrumb(force) {
  if (!force && YC && YC.exp > Date.now()) return YC;
  const r1 = await fetch('https://fc.yahoo.com/', { headers: UA, redirect: 'manual' });
  const sc = typeof r1.headers.getSetCookie === 'function' ? r1.headers.getSetCookie() : String(r1.headers.get('set-cookie') || '').split(/,(?=\s*[A-Za-z0-9_]+=)/);
  const cookie = sc.map(c => c.split(';')[0].trim()).filter(Boolean).join('; ');
  const r2 = await fetch('https://query2.finance.yahoo.com/v1/test/getcrumb', { headers: { ...UA, Cookie: cookie } });
  const crumb = (await r2.text()).trim();
  if (!r2.ok || !crumb || crumb.includes('<') || crumb.length > 40) throw new Error('crumb ' + r2.status);
  YC = { cookie, crumb, exp: Date.now() + 6 * 3600e3 };
  return YC;
}

async function yahooSummary(s) {
  const mods = 'price,summaryDetail,defaultKeyStatistics,financialData,calendarEvents';
  let j = null;
  for (let attempt = 0; attempt < 2 && !j; attempt++) {
    const c = await yCrumb(attempt > 0);
    const res = await fetch(`https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(s)}?modules=${mods}&crumb=${encodeURIComponent(c.crumb)}`, { headers: { ...UA, Cookie: c.cookie } });
    if (res.status === 401 || res.status === 403) { YC = null; continue; }
    if (res.status === 404) return { error: '재무 정보가 없는 종목입니다' };
    if (!res.ok) throw new Error('HTTP ' + res.status);
    j = await res.json().catch(() => null);
  }
  const R = j && j.quoteSummary && j.quoteSummary.result && j.quoteSummary.result[0];
  if (!R) throw new Error('응답 형식');
  const v = x => (x && typeof x === 'object' ? (x.raw ?? null) : (typeof x === 'number' ? x : null));
  const P = R.price || {}, SD = R.summaryDetail || {}, KS = R.defaultKeyStatistics || {}, FD = R.financialData || {}, CE = R.calendarEvents || {};
  const ed = CE.earnings && Array.isArray(CE.earnings.earningsDate) ? CE.earnings.earningsDate.map(v).filter(Boolean) : [];
  const recMean = v(FD.recommendationMean);
  const out = blank(s);
  Object.assign(out, {
    src: 'yahoo', type: P.quoteType || '', name: P.longName || P.shortName || '', ccy: P.currency || SD.currency || '',
    per: v(SD.trailingPE), fper: v(SD.forwardPE) ?? v(KS.forwardPE), eps: v(KS.trailingEps), feps: v(KS.forwardEps),
    pbr: v(KS.priceToBook), peg: v(KS.pegRatio), roe: v(FD.returnOnEquity), divy: v(SD.dividendYield) ?? v(SD.yield),
    beta: v(SD.beta) ?? v(KS.beta) ?? v(KS.beta3Year),
    tgt: v(FD.targetMeanPrice), tgtHi: v(FD.targetHighPrice), tgtLo: v(FD.targetLowPrice), nAn: v(FD.numberOfAnalystOpinions),
    rec: recMean != null ? Math.round((6 - recMean) * 100) / 100 : null, // Yahoo는 1=강력매수 → 5=강력매수로 뒤집음
    revG: v(FD.revenueGrowth), epsG: v(FD.earningsGrowth), earn: ed.length ? ed[0] : null,
  });
  return out;
}
