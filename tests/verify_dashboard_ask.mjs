// 從 preview.html 抽出真正上線的那段 JS，在 Node 裡跑一次。
//
// 儀表板的問答是純前端（離線可用，demo 不怕網路掛掉），沒辦法寫 Python 測試，
// 所以用這支確認它「真的會動」，而不是「看起來應該會動」。
//
// 執行：node tests/verify_dashboard_ask.mjs      （要先跑 data/make_preview.py）

import fs from 'fs';

const html = fs.readFileSync('preview.html', 'utf8');
const payload = JSON.parse(
  html.match(/<script id="payload" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

const grab = (name) => {
  const i = script.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`找不到函式 ${name}`);
  let depth = 0;
  for (let k = script.indexOf('{', i); k < script.length; k++) {
    if (script[k] === '{') depth++;
    else if (script[k] === '}' && --depth === 0) return script.slice(i, k + 1);
  }
  throw new Error(`${name} 括號不平衡`);
};
const constant = (re) => {
  const m = script.match(re);
  if (!m) throw new Error(`找不到常數 ${re}`);
  return m[0];
};

const consts = [
  constant(/var METRIC_WORDS = \[[\s\S]*?\];/),
  constant(/var RANK_WORDS = \[[\s\S]*?\];/),
  constant(/var RANK_DEFAULT_BAND = .*;/),
  constant(/var COUNT_CUE = .*;/),
].join('\n');

const M = payload.meta, R = payload.records;
const areas = [...new Set(R.map(r => r.region))].filter(r => r !== '全國');
const BANDS = M.age_groups, METRICS = M.metrics;
const num = (v) => Math.round(v).toLocaleString('en-US');

const run = new Function('M', 'R', 'areas', 'BANDS', 'METRICS', 'num',
  `${consts}
   ${grab('normQ')}
   ${grab('parseQuestion')}
   ${grab('fmtRec')}
   ${grab('resolve')}
   return (q) => { const it = parseQuestion(q); return { it, res: resolve(it) }; };`
)(M, R, areas, BANDS, METRICS, num);

// 每一題都寫明期望：答得出來，還是該誠實說沒有。
// 後面那組（false）比前面重要 —— 一個會硬掰的問答比沒有問答更危險。
const cases = [
  ['新北市 25-29 歲的平均年薪是多少？', true],
  ['哪一區的青年最多？', true],
  ['板橋區 18-24 歲有幾個人？', true],
  ['哪些行業最缺工？', true],
  ['大專及以上的薪水多少？', true],
  ['新北市 30-35 歲的勞動力參與率', true],
  ['新北市青年的居住情況？', false],        // 我們沒有居住面向的資料
  ['新北市青年的幸福指數', false],           // 清單裡根本沒有這個指標
];

let fail = 0;
for (const [q, shouldAnswer] of cases) {
  const { it, res } = run(q);
  const ok = res.ok === shouldAnswer;
  if (!ok) fail++;
  const mark = ok ? (res.ok ? '✅' : '🚫 誠實說沒有') : '❌ 不如預期';
  console.log(`${mark}　${q}`);
  console.log(`   讀成：地區=${it.region || '—'} 年齡=${it.age_group || '—'} `
    + `指標=${it.metric || '—'}${it.education ? ' 分類=' + it.education : ''}`
    + `${it.kind === 'rank' ? ' [排名]' : ''}`);
  console.log('   ' + res.text.split('\n').join('\n   '));
  console.log();
}

console.log(fail === 0
  ? `全部 ${cases.length} 題行為符合預期`
  : `❌ ${fail} 題不如預期`);
process.exit(fail === 0 ? 0 : 1);
