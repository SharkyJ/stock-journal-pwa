/**
 * 주식일지 — 일일 자산 스냅샷 (선택 사항)
 * 앱을 열지 않은 날에도 App_자산추이에 하루 1회 기록합니다.
 *
 * 설치: 주식일지(메인) 스프레드시트 → 확장 프로그램 → Apps Script → 이 코드 붙여넣기
 *       → installTrigger 함수를 한 번 실행(권한 허용). 매일 16~17시(시트 시간대)에 실행됩니다.
 * 제거: removeTrigger 실행.
 */
const SNAP_SHEET = 'App_자산추이';
const SNAP_HDR = ['일자','투자원금(KRW)','총평가자산(KRW)','총손익(KRW)','수익률','국장 총평가(KRW)','미장 총평가(USD)','예수금(KRW환산)','환율(USD/KRW)','국장 원금(KRW)','미장 원금(USD)','기록시각'];

function dailySnapshot() {
  const ss = SpreadsheetApp.getActive();
  const tz = ss.getSpreadsheetTimeZone();
  const s = ss.getSheetByName('총자산').getRange('A4:I8').getValues(); // 국장, 미장, 미장원화, 합계KRW, 합계USD
  const fx = ss.getSheetByName('설정').getRange('B5').getValue();
  const KR = s[0], US = s[1], TOT = s[3];
  if (!TOT[5]) return; // 값이 비어 있으면(IMPORTRANGE 미계산 등) 기록하지 않음

  let sh = ss.getSheetByName(SNAP_SHEET);
  if (!sh) { sh = ss.insertSheet(SNAP_SHEET); sh.appendRow(SNAP_HDR); sh.setFrozenRows(1); }

  const now = new Date();
  const today = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  const row = [today, TOT[1], TOT[5], TOT[6], TOT[7], KR[5], US[5], TOT[4], fx, KR[1], US[1], "'" + Utilities.formatDate(now, tz, 'yyyy-MM-dd HH:mm')];

  const dates = sh.getRange(1, 1, Math.max(1, sh.getLastRow()), 1).getValues().map(r =>
    r[0] instanceof Date ? Utilities.formatDate(r[0], tz, 'yyyy-MM-dd') : String(r[0]));
  const i = dates.indexOf(today);
  if (i > 0) sh.getRange(i + 1, 1, 1, row.length).setValues([row]);
  else sh.appendRow(row);
}

function installTrigger() {
  removeTrigger();
  ScriptApp.newTrigger('dailySnapshot').timeBased().everyDays(1).atHour(16).create();
  dailySnapshot();
}

function removeTrigger() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'dailySnapshot').forEach(t => ScriptApp.deleteTrigger(t));
}
