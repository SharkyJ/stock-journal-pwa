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
//   미장에서 Yahoo가 막히면(crumb 429 등) 마지막으로 받은 Yahoo 자료(최대 7일)를, 그것도 없으면 네이버 해외주식 자료를 돌려줌
//   둘 다 비공식 API라 응답 형식이 바뀌면 이 파일만 고치면 됩니다.
// GET /fund?s=<심볼>&diag=1
//   진단용: 소스별 성공 여부·받은 값·캐시 상태를 보여줌 (브라우저 주소창에서 직접 열어 확인)
// 환경 변수(선택) ALLOWED_ORIGINS: 허용할 앱 주소, 쉼표 구분. 기본값은 아래 ALLOW_DEFAULT.
// KV 바인딩(선택) CACHE: 재무 자료·Yahoo crumb을 인스턴스 간에 보관. 없으면 인스턴스 메모리만 씀.
//   무료 플랜의 Worker 인스턴스는 자주 새로 뜨므로 KV가 있어야 캐시가 실제로 유지되고 Yahoo 호출이 줄어듦.

const ALLOW_DEFAULT = 'https://sharkyj.github.io,http://localhost:8000';
const RANGES = {
  '1d': ['1d', '5m'], '5d': ['5d', '30m'], '1mo': ['1mo', '1d'], '3mo': ['3mo', '1d'], '1y': ['1y', '1d'], '5y': ['5y', '1wk'],
  '2y': ['2y', '1d'], '5yd': ['5y', '1d'],
};
const UA = { 'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36', 'Accept': 'application/json' };
// crumb 발급용: 데스크톱 브라우저와 비슷한 헤더
const BH = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9',
};
const MEM = new Map(); // 같은 인스턴스 안에서의 짧은 캐시 (workers.dev에서는 Cache API가 동작하지 않음)
const FUND_FRESH = 6 * 3600;      // 재무 자료를 새로 받지 않고 쓰는 시간(초)
const FUND_FALLBACK = 3600;       // 네이버 해외주식 대체 자료는 1시간 뒤 Yahoo를 다시 시도
const FUND_KEEP = 7 * 86400;      // 조회 실패 시 대신 보여줄 수 있는 지난 자료의 보관 기간
const COOL_MS = 30 * 60e3;        // Yahoo가 429를 주면 이 시간 동안 Yahoo 재무 호출을 쉼
let YC = null;         // Yahoo 쿠키·crumb (quoteSummary용)
let YCP = null;        // 진행 중인 crumb 발급 (동시 요청이 각자 발급받지 않게 공유)
let YCOOL = 0;         // 이 시각(ms)까지 Yahoo 재무 호출 안 함

export default {
  async fetch(req, env, ctx) {
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
      if (url.searchParams.get('diag') === '1') return json(await diagnose(s, env));
      const [out, status] = await fundRoute(s, env, ctx);
      return json(out, status);
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
//   stale true = 최신 조회에 실패해 지난 자료를 보낸 것, warn = 실패 사유
const blank = s => ({ s, src: '', type: '', name: '', ccy: '', per: null, fper: null, eps: null, feps: null, pbr: null, peg: null, roe: null, divy: null, beta: null, tgt: null, tgtHi: null, tgtLo: null, nAn: null, rec: null, revG: null, epsG: null, earn: null, at: Math.floor(Date.now() / 1000) });

const isKr = s => /^[0-9A-Z]{6}\.(KS|KQ)$/.test(s);
// "12.3배", "$4.56", "1,234원" → 숫자
const toNum = v => {
  if (v == null) return null;
  const m = String(v).replace(/,/g, '').replace(/[−–]/g, '-').match(/-?\d+(\.\d+)?/);
  return m ? +m[0] : null;
};

/* 캐시: 인스턴스 메모리 → KV(CACHE 바인딩이 있으면). 재무 항목 = { data, fresh: 이 시각(ms)까지 새로 받지 않음 } */
async function cacheGet(env, k) {
  const m = MEM.get('C|' + k);
  if (m && m.exp > Date.now()) return m.data;
  if (!env.CACHE) return null;
  try {
    const v = await env.CACHE.get(k, 'json');
    if (v) MEM.set('C|' + k, { exp: Date.now() + 10 * 60e3, data: v });
    return v;
  } catch (e) { return null; }
}
function cachePut(env, ctx, k, v, ttlSec) {
  if (MEM.size > 300) MEM.clear();
  MEM.set('C|' + k, { exp: Date.now() + ttlSec * 1000, data: v });
  if (!env.CACHE) return;
  const p = env.CACHE.put(k, JSON.stringify(v), { expirationTtl: Math.max(60, Math.round(ttlSec)) }).catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(p);
}

async function fundRoute(s, env, ctx) {
  const now = Date.now(), kr = isKr(s), ck = 'f:' + s;
  const prev = await cacheGet(env, ck);
  const old = prev && prev.data && prev.data.src !== 'none' ? prev.data : null;
  if (prev && prev.data && prev.fresh > now) return [prev.data, 200];
  const save = (d, freshSec) => { cachePut(env, ctx, ck, { data: d, fresh: now + freshSec * 1000 }, FUND_KEEP); return [d, 200]; };
  const stale = errs => [{ ...old, stale: true, warn: errs.join(' · ') }, 200];

  let nErr = null;
  if (kr) {
    const n = await naverKr(s).catch(e => { nErr = '네이버 ' + e.message; return null; });
    if (n) return save(n, FUND_FRESH);
  }
  const y = await yahooSummary(s, env).catch(e => ({ error: e.message }));
  if (!y.error) return save(y, FUND_FRESH);
  if (y.notFound && !kr) return [{ error: y.error }, 404];
  const errs = [nErr, 'Yahoo ' + y.error].filter(Boolean);
  if (kr && !nErr) return save({ ...blank(s), src: 'none' }, FUND_FRESH); // 국장 ETF 등 재무가 없는 종목
  if (!kr) {
    // Yahoo가 막힘: 포워드 PER·목표주가가 있는 지난 Yahoo 자료를 우선, 없으면 네이버 해외주식(실적 PER·EPS·PBR 위주)
    if (old && old.src === 'yahoo') return stale(errs);
    const n = await naverUs(s).catch(e => { errs.push('네이버 ' + e.message); return null; });
    if (n) { n.warn = errs.join(' · '); return save(n, FUND_FALLBACK); }
  }
  if (old) return stale(errs);
  return [{ error: '재무 정보를 가져오지 못했습니다 · ' + errs.join(' · ') }, 502];
}

// 네이버 증권 모바일 API: totalInfos[{code,key,value}] + consensusInfo
// 재무지표가 없는 종목(ETF 등)은 null → Yahoo로
async function naverKr(s) {
  const code = s.slice(0, 6);
  const res = await fetch(`https://m.stock.naver.com/api/stock/${code}/integration`, { headers: { ...UA, 'Referer': 'https://m.stock.naver.com/' } });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const j = await res.json().catch(() => null);
  if (!j || !Array.isArray(j.totalInfos)) return null;
  const ti = {};
  j.totalInfos.forEach(x => { if (x && x.code) ti[x.code] = x.value; });
  const ci = j.consensusInfo || {};
  const out = blank(s);
  const dy = toNum(ti.dividendYieldRatio);
  Object.assign(out, {
    src: 'naver', type: 'EQUITY', name: j.stockName || '', ccy: 'KRW',
    per: toNum(ti.per), fper: toNum(ti.cnsPer), eps: toNum(ti.eps), feps: toNum(ti.cnsEps), pbr: toNum(ti.pbr),
    divy: dy != null ? Math.round(dy * 100) / 1e4 : null,
    tgt: toNum(ci.priceTargetMean), rec: toNum(ci.recommMean),
  });
  if (out.per == null && out.fper == null && out.pbr == null && out.tgt == null) return null; // ETF 등 → Yahoo로
  return out;
}

// 네이버 해외주식: api.stock.naver.com/stock/{로이터 코드}/basic → stockItemTotalInfos[{code,key,value}]
// 로이터 코드 접미사는 나스닥 .O, 그 밖의 거래소는 종목마다 달라 순서대로 시도. debug면 받은 항목 목록(codes)도 붙임
async function naverUs(s, debug) {
  const base = s.replace(/-/g, '.');
  const norm = x => String(x || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  let last = '';
  for (const rc of [base + '.O', base, base + '.N', base + '.K']) {
    const res = await fetch(`https://api.stock.naver.com/stock/${encodeURIComponent(rc)}/basic`, { headers: { ...UA, 'Referer': 'https://m.stock.naver.com/' } }).catch(() => null);
    if (!res) { last = '연결 실패'; continue; }
    if (!res.ok) { last = 'HTTP ' + res.status; continue; }
    const j = await res.json().catch(() => null);
    const infos = j && (j.stockItemTotalInfos || j.totalInfos);
    if (!Array.isArray(infos) || !infos.length) { last = '응답 형식'; continue; }
    const sym = j.symbolCode || j.tickerCode || '';
    if (sym && norm(sym) !== norm(base)) { last = '다른 종목'; continue; }
    const find = (codes, label) => {
      const x = infos.find(i => i && (codes.includes(i.code) || label.test(String(i.key || '').replace(/\s/g, ''))));
      return x ? toNum(x.value) : null;
    };
    const dy = find(['dividendYieldRatio'], /^배당수익률/);
    const out = blank(s);
    Object.assign(out, {
      src: 'naver', rc, type: /etf/i.test(String(j.stockEndType || j.stockType || '')) ? 'ETF' : 'EQUITY',
      name: j.stockName || j.stockNameEng || '', ccy: (j.currencyType && j.currencyType.code) || 'USD',
      per: find(['per'], /^PER(\(|$)/i), fper: find(['cnsPer', 'estimatedPer', 'forwardPer'], /^(추정|선행|예상)PER/i),
      eps: find(['eps'], /^EPS(\(|$)/i), feps: find(['cnsEps', 'estimatedEps', 'forwardEps'], /^(추정|선행|예상)EPS/i),
      pbr: find(['pbr'], /^PBR(\(|$)/i), divy: dy != null ? Math.round(dy * 100) / 1e4 : null,
    });
    if (debug) out.codes = infos.map(i => `${i.code}:${i.key}=${i.value}`);
    if (debug || out.type === 'ETF') return out;
    if (out.per == null && out.eps == null && out.pbr == null && out.fper == null) throw new Error('재무지표 없음');
    return out;
  }
  throw new Error(last || '종목을 찾지 못함');
}

/* ---------- Yahoo crumb ---------- */
// Yahoo는 데이터센터 IP·브라우저가 아닌 클라이언트의 crumb 발급을 429로 자주 막음.
// 그래서 crumb은 KV에 6시간 보관해 인스턴스끼리 나눠 쓰고, 429가 나면 30분간 Yahoo 재무 호출을 쉼(지난 자료·네이버로 대체).
function cookiesOf(res) {
  const sc = typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : String(res.headers.get('set-cookie') || '').split(/,(?=\s*[A-Za-z0-9_]+=)/);
  return sc.map(c => c.split(';')[0].trim()).filter(Boolean);
}
function coolErr(until) {
  return new Error(`일시 차단(429) · ${Math.max(1, Math.ceil((until - Date.now()) / 60e3))}분 뒤 재시도`);
}
async function coolUntil(env) {
  if (YCOOL > Date.now()) return YCOOL;
  if (env.CACHE) {
    const v = +(await env.CACHE.get('y:cool').catch(() => 0)) || 0;
    if (v > Date.now()) return (YCOOL = v);
  }
  return 0;
}
async function setCool(env) {
  YCOOL = Date.now() + COOL_MS;
  if (env.CACHE) await env.CACHE.put('y:cool', String(YCOOL), { expirationTtl: Math.ceil(COOL_MS / 1000) }).catch(() => {});
}

async function fetchCrumb() {
  let cookies = [];
  for (const u of ['https://fc.yahoo.com/', 'https://finance.yahoo.com/']) {
    const r = await fetch(u, { headers: BH, redirect: 'manual' }).catch(() => null);
    if (!r) continue;
    cookies = cookies.concat(cookiesOf(r));
    if (r.body) r.body.cancel().catch(() => {});
    if (cookies.some(c => /^A3=/.test(c))) break;
  }
  const cookie = [...new Set(cookies)].join('; ');
  let st = 0;
  for (const host of ['query1', 'query2']) {
    const r = await fetch(`https://${host}.finance.yahoo.com/v1/test/getcrumb`, { headers: { ...BH, 'Accept': '*/*', Cookie: cookie } }).catch(() => null);
    if (!r) continue;
    const t = (await r.text().catch(() => '')).trim();
    if (r.ok && t && t.length <= 40 && !/[<\s]/.test(t)) return { cookie, crumb: t, exp: Date.now() + 6 * 3600e3 };
    st = r.status;
  }
  const e = new Error('crumb ' + (st || '연결 실패') + (cookie ? '' : ' · 쿠키 없음'));
  e.status = st;
  throw e;
}

async function yCrumb(env, force) {
  const now = Date.now();
  if (!force && YC && YC.exp > now) return YC;
  if (!force && env.CACHE) {
    const k = await env.CACHE.get('y:crumb', 'json').catch(() => null);
    if (k && k.crumb && k.exp > now) return (YC = k);
  }
  const cu = await coolUntil(env);
  if (cu) throw coolErr(cu);
  try {
    YC = await fetchCrumb();
  } catch (e) {
    if (e.status === 429) await setCool(env);
    throw e;
  }
  if (env.CACHE) await env.CACHE.put('y:crumb', JSON.stringify(YC), { expirationTtl: 6 * 3600 }).catch(() => {});
  return YC;
}

async function yahooSummary(s, env) {
  const cu = await coolUntil(env);
  if (cu) throw coolErr(cu);
  const mods = 'price,summaryDetail,defaultKeyStatistics,financialData,calendarEvents';
  let j = null;
  for (let attempt = 0; attempt < 2 && !j; attempt++) {
    const c = await yCrumb(env, attempt > 0);
    const res = await fetch(`https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(s)}?modules=${mods}&crumb=${encodeURIComponent(c.crumb)}`, { headers: { ...UA, Cookie: c.cookie } });
    if (res.status === 401 || res.status === 403) { YC = null; continue; } // crumb 만료 → 새로 발급받아 한 번 더
    if (res.status === 404) return { error: '재무 정보가 없는 종목입니다', notFound: true };
    if (res.status === 429) { await setCool(env); throw new Error('quoteSummary 429'); }
    if (!res.ok) throw new Error('HTTP ' + res.status);
    j = await res.json().catch(() => null);
    if (!j) throw new Error('응답 형식');
  }
  if (!j) throw new Error('crumb 인증 실패');
  const R = j.quoteSummary && j.quoteSummary.result && j.quoteSummary.result[0];
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

// /fund?s=…&diag=1 : 소스별 결과와 캐시 상태 (캐시에 쓰지 않음)
async function diagnose(s, env) {
  const now = Date.now(), c = await cacheGet(env, 'f:' + s), cu = await coolUntil(env);
  const brief = d => (!d || d.error ? d : Object.fromEntries(Object.entries(d).filter(([, x]) => x != null && x !== '')));
  const out = {
    s, kv: !!env.CACHE,
    yahooCooldownMin: cu ? Math.ceil((cu - now) / 60e3) : 0,
    cached: c && c.data ? { src: c.data.src, savedMinAgo: Math.round((now / 1000 - c.data.at) / 60), freshMinLeft: Math.round((c.fresh - now) / 60e3) } : null,
  };
  out.yahoo = brief(await yahooSummary(s, env).catch(e => ({ error: e.message })));
  if (isKr(s)) out.naver = brief(await naverKr(s).catch(e => ({ error: e.message })));
  else out.naverUs = brief(await naverUs(s, true).catch(e => ({ error: e.message })));
  return out;
}
