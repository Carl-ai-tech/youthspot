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
    textContent: '', innerHTML: '', value: '', className: '', type: '',
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
check('地區排名', '哪一區的青年最多？', (t) => t.indexOf('板橋') >= 0);
check('行業排名', '哪些行業最缺工？', (t) => t.indexOf('製造業') >= 0);
check('行政區查詢', '板橋區 18-24 歲有幾個人？', (t) => t.indexOf('34,099') >= 0);
check('教育程度交叉', '大專及以上的薪水多少？', (t) => t.indexOf('75.3') >= 0);
check('沒資料時誠實說沒有', '新北市青年的居住情況？',
  (t, cls) => t.indexOf('答不出來') >= 0 && cls.indexOf('no') >= 0);
check('清單外的指標也要拒答', '新北市青年的幸福指數',
  (t, cls) => t.indexOf('答不出來') >= 0 && cls.indexOf('no') >= 0);

// 使用者問的年齡段不在我們的分組裡時，絕對不能默默拿別段的數字充數。
check('沒有的年齡段要講清楚並建議', '新北市26-28歲平均年薪',
  (t, cls) => t.indexOf('沒有') >= 0 && t.indexOf('25-29') >= 0 && cls.indexOf('no') >= 0);
check('單一歲數也一樣', '新北市30歲的平均年薪',
  (t, cls) => t.indexOf('沒有') >= 0 && cls.indexOf('no') >= 0);
check('跨越分組邊界時要說明建議值比較寬', '新北市22-27歲平均年薪',
  (t, cls) => t.indexOf('比你問的範圍寬') >= 0 && cls.indexOf('no') >= 0);
check('沒講年齡時給整段並標明', '新北市的平均年薪',
  (t) => t.indexOf('18-35') >= 0);

console.log(fail === 0 ? '全部 12 項通過' : `❌ ${fail} 項失敗`);
process.exit(fail === 0 ? 0 : 1);
