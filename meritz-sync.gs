/**
 * 주식일지 — 메리츠증권 Open API 매매·입출금·환전 동기화 (Apps Script)
 * 설계: docs/broker-api-sync.md
 *
 * 설치: 주식일지(메인) 스프레드시트 → 확장 프로그램 → Apps Script → 새 파일 meritz-sync.gs 에 붙여넣기
 *   1) 프로젝트 설정 → 스크립트 속성에 추가
 *        MERITZ_APP_KEY      앱키
 *        MERITZ_APP_SECRET   앱 시크릿
 *        (선택) MERITZ_MAC   12자리 16진수. 비우면 처음 실행할 때 생성해 저장합니다.
 *        (선택) MERITZ_ACCOUNT  시트 계좌명. 기본값 '메리츠증권'
 *   2) testConnection 실행 → 실행 로그 확인 (단계 1: 9443 호출 · 토큰 · 조회 · 적요코드 표)
 *   3) syncMeritz 실행 → App_체결수신 시트에 '대기' 행 생성 (시트 거래·흐름에는 쓰지 않음)
 *   4) installTrigger 실행 → 매일 07:40, 16:40 (시트 시간대) 자동 실행. 제거는 removeTrigger.
 *
 * 안전장치: 조회(GET) 경로만 허용합니다. 주문·환전 POST는 코드에 없습니다.
 *           앱키·시크릿·토큰은 스크립트 속성에만 있고, 로그와 시트에는 계좌번호를 남기지 않습니다.
 */
const MZ = {
  BASE: 'https://openapi.imeritz.com:9443',
  ALLOW: ['/accounts/v1/me/deposit', '/accounts/v1/me/holdings', '/accounts/v1/me/overseas/holdings',
          '/accounts/v1/me/overseas/currencies', '/accounts/v1/me/transactions',
          '/forex/v1/exchanges/history', '/trading/v1/orders/history', '/trading/v1/overseas/orders/history'],
  BASE_DATE: '20261018',        // 현금투자 기준일(2026-10-17) 다음 날부터 가져옴
  STAGE: 'App_체결수신',
  HDR: ['키','수신시각','증권사','계좌구분','유형','거래일자','티커','종목명','수량','단가','수수료·세금',
        '원화금액','달러금액','환율','통화','상태','반영위치','원본요약'],
  GAP_MS: 120,                  // TPS 10 이하
};

/* ---------- 설정 · 인증 ---------- */
function mzProps_() { return PropertiesService.getScriptProperties(); }
function mzMac_() {
  const p = mzProps_(); let m = p.getProperty('MERITZ_MAC');
  if (!/^[0-9A-Fa-f]{12}$/.test(m || '')) {
    m = '02' + Array.from({length: 10}, () => '0123456789ABCDEF'[Math.floor(Math.random() * 16)]).join('');
    p.setProperty('MERITZ_MAC', m);
  }
  return m.toUpperCase();
}
function mzAccount_() { return mzProps_().getProperty('MERITZ_ACCOUNT') || '메리츠증권'; }

function mzToken_() {
  const p = mzProps_();
  const tok = p.getProperty('MERITZ_TOKEN'), exp = Number(p.getProperty('MERITZ_TOKEN_EXP') || 0);
  if (tok && Date.now() < exp - 10 * 60 * 1000) return tok;   // 만료 10분 전까지 재사용 (revoke 호출 금지)
  const key = p.getProperty('MERITZ_APP_KEY'), sec = p.getProperty('MERITZ_APP_SECRET');
  if (!key || !sec) throw new Error('스크립트 속성 MERITZ_APP_KEY / MERITZ_APP_SECRET 이 없습니다.');
  const r = UrlFetchApp.fetch(MZ.BASE + '/oauth2/token', {
    method: 'post', contentType: 'application/x-www-form-urlencoded', muteHttpExceptions: true,
    headers: {mac_address: mzMac_()},
    payload: {grant_type: 'client_credentials', client_id: key, client_secret: sec, scope: 'login'},
  });
  const code = r.getResponseCode(); let j = {};
  try { j = JSON.parse(r.getContentText()); } catch (e) {}
  if (code !== 200 || !j.access_token) throw new Error('토큰 발급 실패 HTTP ' + code + ' ' + mzSafe_(j));
  p.setProperty('MERITZ_TOKEN', j.access_token);
  p.setProperty('MERITZ_TOKEN_EXP', String(Date.now() + Number(j.expires_in || 43200) * 1000));
  return j.access_token;
}

/* ---------- 조회 (연속조회 포함) ---------- */
function mzGet_(path, params, maxPages) {
  if (MZ.ALLOW.indexOf(path) < 0) throw new Error('허용되지 않은 경로: ' + path);
  const rows = []; let cont = null, page = 0, last = {};
  do {
    const q = Object.assign({}, params || {});
    if (cont) { q.tr_cont = '1'; q.tr_cont_key = cont; }
    const qs = Object.keys(q).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(q[k])).join('&');
    Utilities.sleep(MZ.GAP_MS);
    const r = UrlFetchApp.fetch(MZ.BASE + path + (qs ? '?' + qs : ''), {
      method: 'get', muteHttpExceptions: true,
      headers: {authorization: 'Bearer ' + mzToken_(), mac_address: mzMac_(), 'content-type': 'application/json; charset=utf-8'},
    });
    let j = {};
    try { j = JSON.parse(r.getContentText()); } catch (e) { throw new Error(path + ' 응답 파싱 실패 HTTP ' + r.getResponseCode()); }
    last = j;
    const rc = String(j.rsp_cd || '');
    if (rc === '5820' || rc === '5822') break;                      // 조회할 내역 없음
    if (r.getResponseCode() !== 200 || (rc && ['0000', '5762', '5766'].indexOf(rc) < 0))
      throw new Error(path + ' 오류 HTTP ' + r.getResponseCode() + ' ' + rc + ' ' + (j.rsp_msg || '') + ' ' + (j.rsp_sub_msg || ''));
    const d = j.data; if (Array.isArray(d)) rows.push.apply(rows, d); else if (d) rows.push(d);
    cont = (String(j.tr_cont) === '1' && j.tr_cont_key) ? j.tr_cont_key : null;   // 키 뒤 공백 유지
  } while (cont && ++page < (maxPages || 50));
  return {rows: rows, last: last};
}

/* ---------- 단계 1: 연결 테스트 ---------- */
function testConnection() {
  const log = s => Logger.log(s);
  log('MAC ' + mzMac_() + ' / 계좌명 ' + mzAccount_());
  try { mzToken_(); log('✔ 토큰 발급 (9443 호출 가능)'); }
  catch (e) { log('✘ ' + e.message); return; }
  const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd');
  const from = Utilities.formatDate(new Date(Date.now() - 30 * 864e5), 'Asia/Seoul', 'yyyyMMdd');
  const tests = [
    ['예수금', '/accounts/v1/me/deposit', {}],
    ['국내+해외 잔고', '/accounts/v1/me/holdings', {nthn_rmnd_incu_yn: 'N'}],
    ['해외 잔고', '/accounts/v1/me/overseas/holdings', {}],
    ['외화 잔고', '/accounts/v1/me/overseas/currencies', {}],
    ['거래내역 30일', '/accounts/v1/me/transactions', {from: from, to: today, sort: 'ASC'}],
    ['환전내역 30일', '/forex/v1/exchanges/history', {iqry_sta_date: from, iqry_fin_date: today}],
    ['해외 주문체결 30일', '/trading/v1/overseas/orders/history', {oder_sta_date: from, oder_fin_date: today, cntg_cls_code: '1'}],
  ];
  const out = {};
  tests.forEach(t => {
    try { const r = mzGet_(t[1], t[2]); out[t[0]] = r.rows; log('✔ ' + t[0] + ': ' + r.rows.length + '건, 필드 ' + Object.keys(r.rows[0] || {}).join(',')); }
    catch (e) { log('✘ ' + t[0] + ': ' + e.message); }
  });
  (out['국내+해외 잔고'] || []).forEach(h => log('  보유 ' + h.iscd + ' ' + h.isnm + ' ' + (num_(h.rmnd_qty) || num_(h.hldn_issu_rmnd_qty) || num_(h.rmnd_dqty)) + ' @' + h.byng_unpr + ' [' + h.mksc_cls_name + ']'));
  // 단계 2 준비: 적요코드 표 (매매/입출금/환전 분류 확정용)
  const kinds = {};
  (out['거래내역 30일'] || []).forEach(t => {
    const k = [t.tr_type_code, t.smcd, t.smcd_name, t.io_cls_code].join(' | ');
    kinds[k] = kinds[k] || {n: 0, ex: ''}; kinds[k].n++;
    if (!kinds[k].ex) kinds[k].ex = [t.tr_date, t.iscd || '-', 'qty ' + t.tr_qty, 'amt ' + t.tr_amt, 'fx ' + t.frcr_tr_amt, 'unpr ' + t.byng_unpr].join(' ');
  });
  log('— 적요 분류 (거래유형 | 적요코드 | 적요명 | 입출구분 → 건수, 예시)');
  Object.keys(kinds).sort().forEach(k => log('  ' + k + ' → ' + kinds[k].n + '건, ' + kinds[k].ex));
  (out['환전내역 30일'] || []).forEach(x => log('  환전 ' + x.tr_date + ' 구분' + x.mnec_clcd + ' ' + x.frcr_tr_amt + ' ' + x.crcd + ' @' + x.aply_exrt + ' = ' + x.tr_amt + '원 ' + x.rspn_cntt));
}

/* ---------- 단계 3: 스테이징 동기화 ---------- */
function syncMeritz() {
  const ss = SpreadsheetApp.getActive(), tz = ss.getSpreadsheetTimeZone();
  const acct = mzAccount_();
  let sh = ss.getSheetByName(MZ.STAGE);
  if (!sh) { sh = ss.insertSheet(MZ.STAGE); sh.appendRow(MZ.HDR); sh.setFrozenRows(1); }
  const old = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 10).getValues() : [];
  const have = new Set(old.map(r => String(r[0])));
  // 2026-11-02 이전 버전이 해외 매매를 결제일 기준(MZ|TR)으로 기록한 행: 같은 매매를 체결일 기준으로 다시 넣지 않도록 지문 보관
  const oldTr = new Set(old.filter(r => /^MZ\|TR\|/.test(r[0]) && /^(매수|매도)$/.test(r[4])).map(r => [mzSym_(r[6]), r[4], Number(r[8]), Number(r[9])].join('|')));
  const tickers = mzTickerMap_();
  const now = "'" + Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd HH:mm');
  const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd');
  const fmt = d => d ? d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8) : '';
  const num = v => { const n = Number(String(v == null ? '' : v).replace(/,/g, '').trim()); return isFinite(n) ? n : 0; };
  const add = [];
  const push = (key, a) => { if (have.has(key)) return; have.add(key); add.push([key, now, '메리츠증권', acct].concat(a)); };

  // 1) 거래내역: 국내 매매 · 입출금 (적요코드 기준). 해외 매매는 거래일자(tr_date)가 결제일이라
  //    2)의 해외 주문체결내역(체결일)으로 기록하고, 여기서는 수수료·세금만 모아 둔다.
  const ovFee = {};
  mzGet_('/accounts/v1/me/transactions', {from: MZ.BASE_DATE, to: today, sort: 'ASC'}).rows.forEach(t => {
    if (t.cncl_yn === 'Y') return;
    const name = String(t.smcd_name || ''), iscd = String(t.iscd || '').trim();
    const c = mzClassify_(t);
    if (c === null) return;                                               // RP·환전 등 기록하지 않는 행
    const kind = c;
    const usd = num(t.frcr_tr_amt) !== 0 && (kind === '매수' || kind === '매도' || kind === '기타');
    const fee = ['deal_fee', 'fee', 'trtx', 'srtx', 'intx', 'rstx', 'ovsc_deal_fee'].reduce((s, k) => s + num(t[k]), 0);
    if ((kind === '매수' || kind === '매도') && usd) {
      const k = [mzSym_(iscd), kind, num(t.tr_qty), num(t.frcr_tr_amt)].join('|');
      (ovFee[k] = ovFee[k] || []).push(fee); return;
    }
    const tk = iscd ? (tickers[mzSym_(iscd)] || '') : '';
    push('MZ|TR|' + t.tr_date + '|' + String(t.tran_srno).trim(), [
      kind, fmt(t.tr_date), tk || iscd, t.kor_isnm || '', num(t.tr_qty), num(t.byng_unpr), fee,
      usd ? '' : num(t.tr_amt), usd ? num(t.frcr_tr_amt) : '', '', usd ? 'USD' : 'KRW',
      kind === '기타' ? '무시' : (kind === '매수' || kind === '매도') && !tk ? '새 종목' : '대기', '',
      [t.tr_type_code, t.smcd, name, t.io_cls_code].join('|')]);
  });

  // 2) 해외 주문체결내역 → 체결일 기준 매수/매도 (90일 한도 → 창 분할)
  mzOverseasFills_(MZ.BASE_DATE, today).forEach(o => {
    const kind = o.seln_byov_clcd === '1' ? '매도' : o.seln_byov_clcd === '2' ? '매수' : '';
    const q = num(o.cntg_dqty), px = num(o.cntg_crnc_prc), amt = num(o.cntg_camt) || Math.round(q * px * 100) / 100;
    if (!kind || !(q > 0)) return;
    const sym = mzSym_(o.iscd), fp = [sym, kind, q, px].join('|');
    if (oldTr.has(fp)) { oldTr.delete(fp); return; }                       // 이전 버전에서 이미 수신
    const fk = [sym, kind, q, amt].join('|'), fee = (ovFee[fk] || []).shift() || 0;
    const d = String(o.cntg_ymdh || '').slice(0, 10) || fmt(o.oder_date);
    const tk = tickers[sym] || '';
    push('MZ|OV|' + o.oder_date + '|' + String(o.oder_no).trim() + '|' + String(o.cntg_ymdh || '').replace(/\D/g, '') + '|' + String(o.cntg_no || '').trim(), [
      kind, d, tk || String(o.iscd).trim(), o.kor_isnm || '', q, px, fee, '', amt, '', String(o.crcd || 'USD').trim(),
      tk ? '대기' : '새 종목', '', 'ord ' + o.oder_date + ' ' + (o.cntg_ymdh || '') + ' ' + (o.rctf_cncl_cls_name || '')]);
  });

  // 3) 환전내역 → 달러 매수 / 달러 매도
  mzGet_('/forex/v1/exchanges/history', {iqry_sta_date: MZ.BASE_DATE, iqry_fin_date: today}).rows.forEach(x => {
    if (String(x.rspn_cntt).indexOf('정상') < 0 || String(x.crcd).trim() !== 'USD') return;
    const kind = x.mnec_clcd === '1' ? '달러 매수' : x.mnec_clcd === '2' ? '달러 매도' : '';
    if (!kind) return;
    push('MZ|FX|' + x.aplt_date + '|' + String(x.aplt_srno).trim(), [
      kind, fmt(x.tr_date), '', '', '', '', '', num(x.tr_amt), num(x.frcr_tr_amt), num(x.aply_exrt), 'USD',
      '대기', '', 'chan ' + x.chan_type_cls_code + ' spread ' + x.spead]);
  });

  if (add.length) sh.getRange(sh.getLastRow() + 1, 1, add.length, MZ.HDR.length).setValues(add);
  Logger.log('App_체결수신 신규 ' + add.length + '건');
}

function mzOverseasFills_(from, to) {
  const out = [], D = s => new Date(s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + 'T00:00:00+09:00');
  const f8 = d => Utilities.formatDate(d, 'Asia/Seoul', 'yyyyMMdd');
  for (let a = D(from); a <= D(to); a = new Date(a.getTime() + 89 * 864e5)) {
    const b = new Date(Math.min(a.getTime() + 88 * 864e5, D(to).getTime()));
    out.push.apply(out, mzGet_('/trading/v1/overseas/orders/history', {oder_sta_date: f8(a), oder_fin_date: f8(b), cntg_cls_code: '1'}).rows);
  }
  return out.filter(o => String(o.ovrs_stck_oder_stat_cls_code || '0') === '0');
}
function diagOverseas() {   // 진단: 해외 체결 행 로그 (체결일·결제일 확인용)
  const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyyMMdd');
  mzOverseasFills_('20261010', today).forEach(o => Logger.log([o.oder_date, o.cntg_ymdh, o.iscd, o.seln_byov_clcd, o.cntg_dqty, o.cntg_crnc_prc, o.cntg_camt, o.oder_rctf_cncl_cls_code, o.cntg_no].join(' | ')));
}

/* 적요 분류 (tr_type_code | smcd | smcd_name), 2026-11-02 메리츠 실제 응답 기준
 *   01 015170 외화예탁금이용료 / 01 223010 배당금      → '기타' (무시로 기록, 시트 반영 안 함)
 *   01 102010 전자계좌입금 → 입금      02 102020 전자계좌출금 → 출금
 *   02 007510 환전외화매수(자체) 등 환전 → 기록 안 함 (환전내역 API로 가져옴)
 *   05 055050 주식매수 / 05 255070 해외주식 매수 → 매수     06 255040 해외주식 매도 → 매도
 *   05·06 083050/083060 슈퍼365-RP, 112050/112040 USDRP → 기록 안 함 (예수금 운용)
 *   국내 주식매도 코드는 아직 미관측 → 적요명 '주식매도'로 판정. 그 밖에는 '미분류'(대기)로 남겨 사람이 판단 */
const MZ_SMCD = {'055050': '매수', '255070': '매수', '255040': '매도', '102010': '입금', '102020': '출금',
                 '015170': '기타', '223010': '기타'};
function mzClassify_(t) {
  const code = String(t.smcd || '').trim(), name = String(t.smcd_name || '');
  if (/RP/i.test(name) || /환전/.test(name)) return null;
  if (MZ_SMCD[code]) return MZ_SMCD[code];
  if (/^(해외)?\s*주식\s*매수$/.test(name)) return '매수';
  if (/^(해외)?\s*주식\s*매도$/.test(name)) return '매도';
  if (/배당|이용료|이자|세금|분배/.test(name)) return '기타';
  return '미분류';
}

/* 티커 매핑: 국장/미장 상세 파일의 각 종목 탭 B2(GOOGLEFINANCE 티커) → 심볼 */
function mzSym_(iscd) {
  let s = String(iscd).trim().toUpperCase();
  if (s.indexOf(':') >= 0) return s.split(':').pop().replace('-', '.');
  if (/^A\d/.test(s) || /^A[0-9A-Z]{6}$/.test(s)) return s.slice(1);          // A005930 → 005930
  return s.split('.')[0].toUpperCase();                                    // PLTR.OQ → PLTR
}
function mzTickerMap_() {
  const map = {}, ss = SpreadsheetApp.getActive();
  ['URL_KR', 'URL_US'].forEach(n => {
    const r = ss.getRangeByName(n); if (!r) return;
    try {
      const id = (String(r.getValue()).match(/[-\w]{25,}/) || [])[0];
      if (!id) throw new Error('파일 ID를 찾지 못함');
      SpreadsheetApp.openById(id).getSheets().forEach(t => {
        const tk = String(t.getRange('B2').getValue() || '').trim();
        if (/^[A-Z]+:[0-9A-Z.\-]+$/.test(tk)) map[tk.split(':')[1].replace('-', '.').toUpperCase()] = tk;
      });
    } catch (e) { Logger.log('티커 매핑 실패 ' + n + ': ' + e.message); }
  });
  return map;
}

function num_(v) { const n = Number(String(v == null ? '' : v).replace(/,/g, '').trim()); return isFinite(n) ? n : 0; }
function mzSafe_(o) { return JSON.stringify(o || {}).replace(/\d{8,}/g, '****').slice(0, 300); }

/* ---------- 트리거 ---------- */
function installTrigger() {
  removeTrigger();
  ScriptApp.newTrigger('syncMeritz').timeBased().atHour(7).nearMinute(40).everyDays(1).create();
  ScriptApp.newTrigger('syncMeritz').timeBased().atHour(16).nearMinute(40).everyDays(1).create();
}
function removeTrigger() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncMeritz').forEach(t => ScriptApp.deleteTrigger(t));
}
