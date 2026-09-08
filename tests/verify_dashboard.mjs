// 把 preview.html 裡**整段**腳本原封不動跑一次，用假的 DOM 接住它。
//
// 為什麼要跑整段：先前的版本只抽出幾個函式，然後把 M、R、BANDS、METRICS
// 當參數餵進去 —— 等於幫程式把「其實沒定義的變數」補上了，所以
// `METRICS is not defined` 這種錯永遠測不出來，卻在真實瀏覽器裡直接爆掉。
//
// 現在完全不餵任何東西。腳本自己去 payload 讀資料、自己宣告變數，
// 少宣告一個就會在這裡爆，跟在瀏覽器裡一樣。
//
// 執行：node tests/verify_dashboard.mjs      （要先跑 data/make_preview.py）

import fs from 'fs';
import vm from 'vm';

const html = fs.readFileSync('preview.html', 'utf8');
const payloadText = html.match(
  /<script id="payload" type="application\/json">([\s\S]*?)<\/script>/)[1];
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

// ── 最小可用的假 DOM ──────────────────────────────────────────
const registry = {};

function makeNode(id) {
  const node = {
    id: id || '',
    textContent: '', value: '', className: '', type: '',
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; if (v === '') this.children = []; },
    hidden: false,
    style: {},
    dataset: {},
    children: [],
    handlers: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(evt, fn) { (this.handlers[evt] = this.handlers[evt] || []).push(fn); },
    removeAttribute(name) { if (name === 'hidden') this.hidden = false; else delete this[name]; },
    setAttribute(name, v) { if (name === 'hidden') this.hidden = true; else this[name] = v; },
    getAttribute(name) { return this[name]; },
    querySelectorAll() { return []; },
    click() { (this.handlers.click || []).forEach((f) => f({})); },
    focus() {},
  };
  return node;
}

const document = {
  getElementById(id) {
    if (id === 'payload') {
      const n = makeNode('payload');
      n.textContent = payloadText;
      return n;
    }
    return (registry[id] = registry[id] || makeNode(id));
  },
  createElement() { return makeNode(); },
  querySelectorAll() { return []; },
};

const sandbox = { document, console, window: {}, JSON, Math, Object, Array, String, Number };
sandbox.globalThis = sandbox;

// ── 執行整段腳本 ─────────────────────────────────────────────
// 把它包成回傳內部函式的形式，才能從外面呼叫 runAsk。
const wrapped = script.replace('})();', 'globalThis.__ask = runAsk;\n})();');
try {
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox);
} catch (err) {
  console.error('❌ 腳本執行就失敗了：' + err.message);
  console.error('   （這正是瀏覽器裡會看到的錯誤）');
  process.exit(1);
}

const runAsk = sandbox.__ask;
if (typeof runAsk !== 'function') {
  console.error('❌ 拿不到 runAsk');
  process.exit(1);
}

const el = (id) => document.getElementById(id);
let fail = 0;

function check(label, question, expect) {
  el('askInput').value = '';
  runAsk(question);
  const text = el('askText').textContent;
  const shown = el('askAnswer').className.indexOf('show') >= 0;
  const errored = text.indexOf('程式出錯了') >= 0;
  const ok = shown && !errored && expect(text, el('askAnswer').className);
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　${label}`);
  console.log(`   ${text.split(String.fromCharCode(10)).join(String.fromCharCode(10) + '   ')}`);
  console.log();
}

console.log('把 preview.html 的整段腳本跑一次，檢查問答\n');

check('空白時按「問」', undefined, (t) => t.indexOf('先在上面') >= 0);
check('查一個數字', '新北市 25-29 歲的平均年薪是多少？', (t) => t.indexOf('59.9') >= 0);
// 排名的名次現在畫成圖表，文字只留標題，勝出者寫在結論裡
check('地區排名', '哪一區的青年最多？',
  (t) => t.indexOf('人口數前') >= 0 && el('askVerdict').textContent.indexOf('板橋') >= 0
    && el('askChart').children.length > 0);
check('行業排名', '哪些行業最缺工？',
  (t) => t.indexOf('職缺數前') >= 0 && el('askVerdict').textContent.indexOf('製造業') >= 0
    && el('askChart').children.length > 0);
check('行政區查詢', '板橋區 18-24 歲有幾個人？', (t) => t.indexOf('34,099') >= 0);
check('教育程度交叉', '大專及以上的薪水多少？', (t) => t.indexOf('75.3') >= 0);
check('沒資料時誠實說沒有', '新北市青年的居住情況？',
  (t, cls) => t.indexOf('答不出來') >= 0 && cls.indexOf('no') >= 0);
check('清單外的指標也要拒答', '新北市青年的幸福指數',
  (t, cls) => t.indexOf('答不出來') >= 0 && cls.indexOf('no') >= 0);

// 任意年齡區間：預設的四段不夠用，引擎本來就算得出任何區間。
check('任意區間的薪資', '新北市26-28歲平均年薪',
  (t) => t.indexOf('26-28 歲的平均年薪') >= 0 && t.indexOf('推估') >= 0);
check('任意區間的人口是精確值', '新北市26-28歲有多少人',
  (t) => t.indexOf('26-28 歲的人口數') >= 0 && t.indexOf('沒有經過推估') >= 0);
check('單一歲數也算得出來', '新北市30歲有多少人',
  (t) => t.indexOf('30-30 歲的人口數') >= 0);
check('跨越官方分組邊界一樣算得出來', '新北市22-27歲平均年薪',
  (t) => t.indexOf('22-27 歲的平均年薪') >= 0);
check('行政區的任意區間人口', '板橋區26-28歲有多少人',
  (t) => t.indexOf('板橋區') >= 0 && t.indexOf('26-28') >= 0);
check('沒講年齡時給整段並標明', '新北市的平均年薪',
  (t) => t.indexOf('18-35') >= 0);

// 比較兩個地區：要有圖表和一句結論，不能只給一句話
function checkCompare(label, question, expect) {
  el('askInput').value = '';
  runAsk(question);
  const text = el('askText').textContent;
  const verdict = el('askVerdict').textContent;
  const bars = el('askChart').children.length;
  const errored = text.indexOf('程式出錯了') >= 0;
  const ok = !errored && expect(text, verdict, bars, el('askAnswer').className);
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　${label}`);
  console.log(`   ${text}`);
  if (verdict) console.log(`   結論：${verdict}`);
  console.log(`   圖表：${bars ? '有' : '無'}`);
  console.log();
}

checkCompare('比較兩個行政區的人口', '比較汐止區和林口區22-25歲青年人口',
  (t, v, bars) => t.indexOf('22-25') >= 0 && v.length > 0 && bars > 0);
checkCompare('失業率沒有行政區資料要說清楚', '比較汐止區和林口區22-25歲青年失業率',
  (t, v, bars, cls) => t.indexOf('只有縣市層級') >= 0 && cls.indexOf('no') >= 0);
checkCompare('全市失業率答得出來', '新北市 18-24 歲的失業率',
  (t) => t.indexOf('失業率') >= 0 && t.indexOf('%') >= 0);
checkCompare('排名也要有圖表和結論', '哪一區的青年最多？',
  (t, v, bars) => bars > 0 && v.indexOf('第一名') >= 0);

console.log(fail === 0 ? '全部 17 項通過' : `❌ ${fail} 項失敗`);
process.exit(fail === 0 ? 0 : 1);
