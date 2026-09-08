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

// 最小的 DOM 替身。這樣不只能測邏輯，連 runAsk 有沒有真的把答案寫進畫面
// 都測得到 —— 之前就是因為只測邏輯，漏掉了「按鈕按下去沒反應」。
const nodes = {};
const makeNode = () => ({
  textContent: '', innerHTML: '', value: '', hidden: true, className: '', type: '',
  children: [],
  appendChild(c) { this.children.push(c); },
  addEventListener(_, fn) { this.onclick = fn; },
  removeAttribute(name) { if (name === 'hidden') this.hidden = false; },
  setAttribute(name, v) { this[name] = v; },
  focus() {},
});
const document = {
  getElementById: (id) => (nodes[id] = nodes[id] || makeNode()),
  createElement: () => makeNode(),
};

const built = new Function('M', 'R', 'areas', 'BANDS', 'METRICS', 'num', 'document', 'select',
  `${consts}
   const $ = (id) => document.getElementById(id);
   ${grab('normQ')}
   ${grab('parseQuestion')}
   ${grab('fmtRec')}
   ${grab('resolve')}
   ${grab('showAnswer')}
   ${grab('runAsk')}
   ${grab('runAskInner')}
   return { parseQuestion, resolve, runAsk };`
)(M, R, areas, BANDS, METRICS, num, document, () => {});

const run = (q) => {
  const it = built.parseQuestion(q);
  return { it, res: built.resolve(it) };
};

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

// ── 走完整條路徑：runAsk 有沒有真的把答案寫進畫面
console.log('── 畫面實際更新檢查 ──');

const el = (id) => document.getElementById(id);
el('askInput').value = '';
built.runAsk();
const emptyOk = el('askAnswer').className.indexOf('show') >= 0 && el('askText').textContent.length > 0;
console.log(`${emptyOk ? '✅' : '❌'}　輸入框空白時按「問」　→　${el('askText').textContent || '（完全沒反應）'}`);
if (!emptyOk) fail++;

built.runAsk('新北市 25-29 歲的平均年薪是多少？');
const typedOk = el('askAnswer').className.indexOf('show') >= 0
  && el('askText').textContent.indexOf('59.9') >= 0
  && el('askMeta').textContent.indexOf('平均年薪') >= 0;
console.log(`${typedOk ? '✅' : '❌'}　問一個有答案的問題　→　${el('askText').textContent}`);
if (!typedOk) fail++;

// 排名這條路徑之前沒走過 DOM，補上
built.runAsk('哪些行業最缺工？');
const rankOk = el('askAnswer').className.indexOf('show') >= 0
  && el('askText').textContent.indexOf('製造業') >= 0;
console.log(`${rankOk ? '✅' : '❌'}　問一個排名問題　→　${el('askText').textContent.split(String.fromCharCode(10))[0]}`);
if (!rankOk) fail++;

built.runAsk('新北市青年的居住情況？');
const refusedOk = el('askAnswer').className.indexOf('no') >= 0
  && el('askAnswer').className.indexOf('show') >= 0
  && el('askText').textContent.indexOf('答不出來') >= 0;
console.log(`${refusedOk ? '✅' : '❌'}　問一個沒資料的問題　→　${el('askText').textContent.split('\n')[0]}`);
if (!refusedOk) fail++;

console.log();
console.log(fail === 0
  ? `全部通過（${cases.length} 題判讀 ＋ 4 項畫面更新）`
  : `❌ ${fail} 項不如預期`);
process.exit(fail === 0 ? 0 : 1);
