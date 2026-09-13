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

// charset 必須在前 1024 位元組內宣告。少了它，file:// 雙擊開啟時瀏覽器
// 只能猜編碼，繁中 Windows 會猜成 Big5 讓整頁變亂碼 —— 而且是「改了不相干的
// CSS 就可能翻掉」的那種不穩定失敗。離線 demo 全靠這個檔，所以鎖起來。
const headBytes = Buffer.from(html, 'utf8').subarray(0, 1024).toString('utf8');
if (!/<meta\s+charset=["']?utf-8/i.test(headBytes)) {
  console.error('❌ preview.html 前 1024 位元組內沒有 <meta charset="utf-8">');
  console.error('   （file:// 開啟時中文會變亂碼，離線 demo 會爆）');
  process.exit(1);
}

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
    querySelector() { const c = makeNode(); this.children.push(c); return c; },
    insertAdjacentHTML(pos, html) { this._html += html; },
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

const fetchCalls = [];
const CANNED = {
  ok: true, model: 'test-model',
  text: '**不建議平均分配。**\n前三大區合計佔 33%，其中板橋 106,473 人（[1]）。\n另外我自己算了一個 999,999。',
  trustworthy: false, verified: ['33', '106,473'], unverified: ['999,999'],
  summary: '2/3 個數字比對到來源；1 個對不上：999,999',
  gaps: [{ key: '行業 × 年齡的就業人數', status: 'missing', note: '官方沒有行業 × 年齡的表' }],
  records: [{ metric: '人口數', region: '新北市板橋區', age_group: '18-35', value: 106473,
              unit: '人', provenance: { confidence: 'high' } }],
};
let delayed = false; const queue = [];
const fetch = (url, opts) => {
  if (delayed) { fetchCalls.push({url,body:JSON.parse(opts.body)}); return new Promise((resolve,reject)=>queue.push({resolve,reject})); }
  fetchCalls.push({ url, body: JSON.parse(opts.body) });
  return Promise.resolve({ json: () => Promise.resolve(CANNED) });
};
const sandbox = { document, console, window: {}, JSON, Math, Object, Array, String, Number, fetch };
sandbox.globalThis = sandbox;

// ── 執行整段腳本 ─────────────────────────────────────────────
// 把它包成回傳內部函式的形式，才能從外面呼叫 runAsk。
const wrapped = script.replace('})();', 'globalThis.__ask = runAsk; globalThis.__export = exportReport; globalThis.__scan = renderScan;\n})();');
try {
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox);
} catch (err) {
  console.error('❌ 腳本執行就失敗了：' + err.message);
  console.error('   （這正是瀏覽器裡會看到的錯誤）');
  process.exit(1);
}


const assert = (await import('node:assert/strict')).default;
const el = id => document.getElementById(id);
const fire = (id,event,args={}) => (el(id).handlers[event]||[]).forEach(f=>f(args));
const settle = async (i,text) => { queue[i].resolve({json:()=>Promise.resolve({...CANNED,text})}); await new Promise(r=>setImmediate(r)); };
delayed = true;
el('area').value='新北市板橋區';fire('area','change');
el('askInput').value='請分析未來的青年政策';
fire('askInput','keydown',{key:'Enter'});fire('askInput','keydown',{key:'Enter'});
assert.equal(fetchCalls.length,1,'Repeated Enter must dispatch once');
assert.equal(fetchCalls[0].body.region,'新北市板橋區');
assert.equal(el('askGo').disabled,true);assert.equal(el('synthBtn').disabled,true);
el('askInput').value='修改後的新問題';fire('askInput','input');sandbox.__ask();
assert.equal(fetchCalls.length,1,'Editing must not unlock the in-flight request');
assert.equal(el('askGo').disabled,true);
fire('synthBtn','click');assert.equal(fetchCalls.length,1,'Synthesis shares the lock');
await settle(0,'STALE_OLD_ANSWER');
assert.ok(!el('askText').innerHTML.includes('STALE_OLD_ANSWER'));
assert.equal(el('askGo').disabled,false);
assert.equal(el('askAnswer')['aria-busy'],'false');
el('askInput').value='請分析臺北市青年政策';fire('askInput','input');sandbox.__ask();
assert.equal(fetchCalls[1].body.region,'臺北市');
el('area').value='新北市林口區';fire('area','change');
sandbox.__ask('請分析新的政策');assert.equal(fetchCalls.length,2,'Changing scope must not unlock');
await settle(1,'STALE_SCOPE');assert.ok(!el('askText').innerHTML.includes('STALE_SCOPE'));
el('askInput').value='請分析失敗時青年政策';fire('askInput','input');sandbox.__ask();
queue[2].reject(new Error('network failed'));await new Promise(r=>setImmediate(r));
assert.equal(el('askGo').disabled,false);assert.equal(el('synthBtn').disabled,false);
assert.equal(el('askAnswer')['aria-busy'],'false');
assert.ok(el('askText').textContent.includes('暫時無法使用'));
el('askInput').value='請分析新北市板橋區的青年政策';fire('askInput','input');sandbox.__ask();
assert.equal(fetchCalls[3].body.region,'新北市板橋區');
await settle(3,'DISTRICT_ANSWER');assert.ok(el('askText').innerHTML.includes('DISTRICT_ANSWER'));
fire('synthBtn','click');assert.equal(fetchCalls[4].body.action,'synthesize');
await settle(4,'SYNTH_ANSWER');assert.equal(el('askGo').disabled,false);
console.log('PASS single flight across edits, Enter, synthesis, scope changes; stale response suppression, success and failure recovery');
