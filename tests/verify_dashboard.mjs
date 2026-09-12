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
const fetch = (url, opts) => {
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

const runAsk = sandbox.__ask;
if (typeof runAsk !== 'function') {
  console.error('❌ 拿不到 runAsk');
  process.exit(1);
}

const el = (id) => document.getElementById(id);
let fail = 0;
let total = 0;   // 由各項檢查自己累加，避免總數寫死之後跟實際項數對不上

function check(label, question, expect) {
  el('askInput').value = '';
  runAsk(question);
  const text = el('askText').textContent;
  const shown = el('askAnswer').className.indexOf('show') >= 0;
  const errored = text.indexOf('程式出錯了') >= 0;
  const ok = shown && !errored && expect(text, el('askAnswer').className);
  total++;
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
// 規則認不得的問題要交給模型，不再自己回「答不出來」。
// 查數字、排名、比較那些引擎答得出來的仍然走本機規則（上面那些 check 就是在驗這件事）。
{
  const before = fetchCalls.length;
  el('askInput').value = '';
  runAsk('新北市青年的居住情況？');
  const pending = el('askText').textContent.indexOf('思考中') >= 0;
  await new Promise((r) => setTimeout(r, 0));        // 讓 fetch 的 promise 跑完
  await new Promise((r) => setTimeout(r, 0));
  const sent = fetchCalls.length === before + 1;
  const call = fetchCalls[fetchCalls.length - 1];
  const routed = sent && call.body.action === 'advise' && call.body.question === '新北市青年的居住情況？';
  const html = el('askText').innerHTML || '';
  const rendered = html.indexOf('<b>不建議平均分配。</b>') >= 0 && html.indexOf('<br>') >= 0 && html.indexOf('<button type="button" class="citeref" data-i="1"') >= 0;
  const meta = el('askMeta').textContent;
  const flagged = meta.indexOf('999,999') >= 0 && meta.indexOf('模型自行估算') >= 0 && html.indexOf('<mark class="unv"') >= 0 && meta.indexOf('claude') < 0;
  const warned = el('askAnswer').className.indexOf('warn') >= 0;
  const citeHtml = el('askCite').innerHTML || '';
  // 來源清單：列出被引用的機關與資料集，每筆一個可點的 [N]
  const cited = citeHtml.indexOf('這則回答的資料來源') >= 0 && citeHtml.indexOf('class="citeref" data-i="1"') >= 0;
  const gapShown = (el('askChart').children || []).some((c) => /gaps/.test(c.innerHTML || '') && (c.innerHTML || '').indexOf('官方沒有') >= 0);
  // 先只給第一句結論，其餘收在「看完整分析」裡；展開後可再收起
  const leadOnly = html.indexOf('<span class="lead"><b>不建議平均分配。</b></span>') >= 0
    && html.indexOf('class="more"') >= 0 && html.indexOf('<span class="full" id="askFull" hidden>') >= 0
    && html.indexOf('前三大區') > html.indexOf('askFull');
  const collapsed = el('askAnswer').className.indexOf('collapsed') >= 0;
  const full = el('askFull'); full.hidden = true;
  const moreBtn = el('askMore'); moreBtn.className = 'more';
  (el('askText').handlers.click || []).forEach((fn) => fn({ target: moreBtn }));
  const expanded = full.hidden === false && moreBtn.textContent === '收起 ▴'
    && el('askAnswer').className.indexOf('collapsed') < 0;
  (el('askText').handlers.click || []).forEach((fn) => fn({ target: moreBtn }));
  const recollapsed = full.hidden === true && moreBtn.textContent === '看完整分析 ▾';
  const ok = pending && routed && rendered && flagged && warned && cited
    && leadOnly && collapsed && expanded && recollapsed && gapShown;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　規則認不得的問題交給模型`);
  console.log(`   先顯示思考中 ${pending ? '✓' : '✗'}　送到 advise ${routed ? '✓' : '✗'}　粗體與換行有渲染 ${rendered ? '✓' : '✗'}`);
  console.log(`   對不上的數字有標出 ${flagged ? '✓' : '✗'}　答案標 warn ${warned ? '✓' : '✗'}　有資料履歷 ${cited ? '✓' : '✗'}`);
  console.log(`   先只顯示結論 ${leadOnly && collapsed ? '✓' : '✗'}　展開 ${expanded ? '✓' : '✗'}　再收起 ${recollapsed ? '✓' : '✗'}`);
  console.log();
}
// 對照組：引擎答得出來的問題**不該**打到模型
{
  const before = fetchCalls.length;
  el('askInput').value = '';
  runAsk('新北市 25-29 歲的平均年薪是多少？');
  const ok = fetchCalls.length === before && el('askText').textContent.indexOf('59.9') >= 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　引擎答得出來的問題不打模型（fetch 呼叫數不變）`);
  console.log();
}

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

// 問答只能由「問」按鈕或 Enter 觸發，不能由失焦觸發。
// input 的 change 事件是「失去焦點且值有變動」才觸發 —— 綁在上面的話，
// 使用者打完字點畫面別處就會自動送出，看起來像「還沒按就自己回答了」。
{
  const handlers = el('askInput').handlers || {};
  const bad = Object.keys(handlers).filter((k) => k === 'change');
  total++;
  if (bad.length) fail++;
  console.log(`${bad.length ? '❌' : '✅'}　問答輸入框沒有綁 change（避免失焦就自動送出）`);
  const keys = Object.keys(handlers).sort().join(', ');
  console.log(`   實際綁定：${keys || '(無)'}`);
  console.log();
}

// 比較兩區時，「讀成」那行要把兩個地區都列出來。
// 這行是問答的透明度賣點：它宣稱「我把你的問題讀成這樣」，
// 少列一個地區等於那句宣稱本身不誠實。
check('比較兩區時讀成要列出兩區', '比較汐止區和林口區22-25歲青年人口',
  () => {
    const m = el('askMeta').textContent;
    return m.indexOf('林口區') >= 0 && m.indexOf('汐止區') >= 0;
  });

// 問了比資料更細的地區時，絕對不可以拿粗一級的數字默默充數。
// 這是整個專案的立場：每個數字都要講清楚是哪裡來的。
// 先前問「林口區哪個行業最缺工」會直接端出全國排名且隻字不提。
check('行政區問全國級指標要講明白', '林口區哪個行業最缺工',
  (t) => t.indexOf('只統計到全國') >= 0 && t.indexOf('不是林口區') >= 0
    && t.indexOf('職缺數前') >= 0);
// 行政區「問情況」而不是「查數字」時要交給模型（它拿得到該區的人口／勞動力／勞參率），
// 不能用「只統計到全市」把人擋在門口；純查數字的問法仍留在本機講原因。
{
  const before = fetchCalls.length;
  el('askInput').value = ''; runAsk('林口區工作供需問題');
  const routed = fetchCalls.length === before + 1 && el('askMeta').textContent.indexOf('林口') >= 0;
  el('askInput').value = ''; runAsk('林口區 18-24 歲的平均年薪');
  const stayed = fetchCalls.length === before + 1 && el('askText').textContent.indexOf('只統計到') >= 0;
  const ok = routed && stayed;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　行政區問情況交給模型 ${routed ? '✓' : '✗'}；行政區查數字留在本機講原因 ${stayed ? '✓' : '✗'}`);
  console.log();
}
check('行政區問全市級指標要說原因', '林口區 18-24 歲的平均年薪',
  (t, cls) => t.indexOf('只統計到新北市') >= 0 && t.indexOf('換成新北市') >= 0
    && cls.indexOf('no') >= 0);
// 對照組：有行政區資料的指標不該冒出這句警語
check('有行政區資料時不要多嘴', '林口區 18-24 歲有多少人',
  (t) => t.indexOf('只統計到') < 0 && t.indexOf('人口數') >= 0);

// 比較兩個地區：要有圖表和一句結論，不能只給一句話
function checkCompare(label, question, expect) {
  el('askInput').value = '';
  runAsk(question);
  const text = el('askText').textContent;
  const verdict = el('askVerdict').textContent;
  const bars = el('askChart').children.length;
  const errored = text.indexOf('程式出錯了') >= 0;
  const ok = !errored && expect(text, verdict, bars, el('askAnswer').className);
  total++;
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

// 六都比較：首長最常問「我們排第幾」
const benchCount = el('benchBox').children.length;
const benchOk = benchCount >= 4;
total++;
if (!benchOk) fail++;
console.log(`${benchOk ? '✅' : '❌'}　六都比較產出 ${benchCount} 個指標`);
console.log();

// 時間趨勢：命題的「整合出青年動態」
const trendCount = el('trendBox').children.length;
const trendOk = trendCount >= 3;   // 兩張圖 + 交叉驗證
total++;
if (!trendOk) fail++;
console.log(`${trendOk ? '✅' : '❌'}　時間趨勢產出 ${trendCount} 個區塊`);
console.log();

// 建議／洞察卡片：一行一條、可展開、每條有「帶到問答」。
// 先前六張卡全展開是兩千五百字。內文與依據都要還在（它們是問答答案的稽核底稿），
// 只是不再一開始就攤開。
{
  const rows = [...el('policyList').children, ...el('insightList').children];
  const compact = rows.filter((r) => r.className === 'nrow' && /<summary>/.test(r.innerHTML || ''));
  const withAsk = rows.filter((r) => /class="nrow-ask" data-q="/.test(r.innerHTML || ''));
  const withBasis = rows.filter((r) => /class="basis"/.test(r.innerHTML || ''));
  const nested = rows.filter((r) => /data-q="[^"]*「[^"]*「/.test(r.innerHTML || ''));
  const ok = rows.length > 0 && compact.length === rows.length
    && withAsk.length === rows.length && withBasis.length === rows.length && nested.length === 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　${rows.length} 條建議／洞察：可收合 ${compact.length}、有帶到問答 ${withAsk.length}、保留依據 ${withBasis.length}`
    + (nested.length ? `、${nested.length} 條問題文字有引號套引號` : ''));
}

// 「帶到問答」與範例問句只填不送。任何不是「問」或 Enter 的動作都不該觸發回答。
{
  const before = el('askText').textContent;
  el('askInput').value = '';
  const fakeBtn = { dataset: { q: '測試問題 —— 政策上該怎麼調整？' } };
  (el('policyList').handlers.click || []).forEach((f) => f({ target: fakeBtn, preventDefault() {} }));
  const filled = el('askInput').value === fakeBtn.dataset.q;
  const notSent = el('askText').textContent === before;
  el('askInput').value = '';
  const tip = el('askTips').children[0];
  if (tip) tip.click();
  const tipFilled = !!tip && el('askInput').value === tip.textContent;
  const tipNotSent = el('askText').textContent === before;
  const ok = filled && notSent && tipFilled && tipNotSent;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　帶到問答／範例問句：填入 ${filled && tipFilled ? '是' : '否'}、未自動送出 ${notSent && tipNotSent ? '是' : '否'}`);
  console.log();
}

// 洞察卡 ↔ 圖表：每張卡要有「看圖」，每張圖要掛 data-chart，而且鍵要對得上。
{
  const rows = [...el('insightList').children];
  const withChart = rows.filter((r) => /class="nrow-chart" data-chart="/.test(r.innerHTML || ''));
  const keys = rows.map((r) => ((r.innerHTML || '').match(/nrow-chart" data-chart="([^"]+)"/) || [])[1]).filter(Boolean);
  const figs = [...el('trendBox').children].filter((c) => c['data-chart']);
  const figKeys = new Set(figs.map((f) => f['data-chart']));
  const dangling = keys.filter((k) => !figKeys.has(k));
  const ok = rows.length > 0 && withChart.length === rows.length && figs.length >= 4 && dangling.length === 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　洞察卡 ${withChart.length}/${rows.length} 張有看圖鈕，圖 ${figs.length} 張掛了 data-chart`
    + (dangling.length ? `，${dangling.length} 個鍵找不到對應的圖：${dangling.join(', ')}` : '，鍵全部對得上'));
  console.log();
}

// 居住負擔（第四個資料來源）：兩張六都季序列圖、六都比較多兩個指標、
// 問答查得到而且一定要講「全體家戶，不是青年」—— 這是唯一非青年的數字，
// 標籤掉了就會被當成青年數字引用。
{
  const figs = [...el('trendBox').children].filter((c) => c['data-chart'] === 'housing');
  const html = figs.map((c) => c.innerHTML || '').join('');
  const twoFigs = figs.length === 2 && /房價所得比/.test(html) && /貸款負擔率/.test(html);
  const tagged = (html.match(/scopetag">全體家戶/g) || []).length === 2 && /不是青年/.test(html);
  const fixNoted = /資料修正/.test(html) && /100 倍/.test(html);
  const quarterly = /2002Q1/.test(html);
  const ok = twoFigs && tagged && fixNoted && quarterly;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　居住負擔畫出 ${figs.length} 張六都季序列圖`
    + `（家戶標籤 ${tagged ? '有' : '無'}、來源修正說明 ${fixNoted ? '有' : '無'}、季標籤 ${quarterly ? '有' : '無'}）`);
  console.log();

  const bench = [...el('benchBox').children].map((c) => c.innerHTML || '').join('');
  const bOk = bench.indexOf('房價所得比<span class="scopetag">全體家戶') >= 0 && bench.indexOf('12.63 倍') >= 0
    && bench.indexOf('貸款負擔率<span class="scopetag">全體家戶') >= 0 && bench.indexOf('55.3%') >= 0;
  total++;
  if (!bOk) fail++;
  console.log(`${bOk ? '✅' : '❌'}　六都比較有房價所得比與貸款負擔率，帶家戶標籤、單位正確`);
  console.log();
}
check('居住指標查得到，而且講明是家戶不是青年', '新北市房價所得比',
  (t) => t.indexOf('12.63 倍') >= 0 && t.indexOf('全體家戶') >= 0 && t.indexOf('不是青年') >= 0
    && t.indexOf('全體 歲') < 0);
check('口語問買房也對得到指標', '新北市買房要幾年不吃不喝', (t) => t.indexOf('12.63 倍') >= 0);
check('房貸負擔率是百分比不是小數', '新北市的房貸負擔', (t) => t.indexOf('55.3%') >= 0);
check('行政區問居住指標要說只有全市', '林口區房價所得比',
  (t) => t.indexOf('12.63') < 0 && (t.indexOf('全市') >= 0 || t.indexOf('新北市') >= 0));

// 掃描結果：判讀完先收成一行摘要（讀了幾列、合計對不對、18–35 歲多少），
// 三道步驟按了才展開，可收起、可清除 —— 三張表攤開會把畫面吃掉。
{
  const d = { ok: true, region: '新北市', year: 2024, metric: '就業者', unit: '千人', model: 'test-model',
    rows: [{ age_label: '15-24', value: 120, band: '15-24' }, { age_label: '25-29', value: 215, band: '25-29' }],
    printed_total: 335, computed_total: 335, issues: [],
    aligned: [{ age_group: '18-35', value: 552, unit: '千人', provenance: { confidence: 'medium', source_age_group: '五歲組' } }] };
  el('scanOut').hidden = false;
  sandbox.__scan(d);
  const html = el('scanOut').innerHTML || '';
  const summarised = html.indexOf('class="scan-sum"') >= 0 && html.indexOf('讀出 2 列') >= 0
    && html.indexOf('合計對得上') >= 0 && html.indexOf('三道查核通過') >= 0 && html.indexOf('552,000 人') >= 0;
  const foldedFirst = html.indexOf('<div class="scan-steps" id="scanSteps" hidden>') >= 0
    && html.indexOf('class="scan-step"') > html.indexOf('scanSteps');
  const steps = el('scanSteps'); steps.hidden = true;
  const more = el('scanMore'); more.id = 'scanMore';
  (el('scanOut').handlers.click || []).forEach((fn) => fn({ target: more }));
  const expanded = steps.hidden === false && more.textContent === '收起 ▴';
  (el('scanOut').handlers.click || []).forEach((fn) => fn({ target: more }));
  const folded = steps.hidden === true;
  const clear = el('scanClear'); clear.id = 'scanClear';
  (el('scanOut').handlers.click || []).forEach((fn) => fn({ target: clear }));
  const cleared = el('scanOut').hidden === true && (el('scanOut').innerHTML || '') === '';
  const ok = summarised && foldedFirst && expanded && folded && cleared;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　掃描結果先收成一行摘要：摘要 ${summarised ? '✓' : '✗'}　步驟預設收起 ${foldedFirst ? '✓' : '✗'}　展開 ${expanded ? '✓' : '✗'}　收起 ${folded ? '✓' : '✗'}　清除 ${cleared ? '✓' : '✗'}`);
  console.log();
}

// 缺工面板：年變化率與方向來自預測記錄攤平後的 _annual_pct／_direction。
// 先前前端讀 fc.extras（不存在），整個面板每列都是「— 0.0%/年」卻沒人發現 ——
// 因為看起來像正常畫面。鎖成測試：至少要有一列不是 flat，而且註記的顯著行業數 > 0。
{
  const bars = [...el('vacBars').children].map((c) => c.innerHTML || '');
  const live = bars.filter((h) => h.indexOf('↑ ') >= 0 || h.indexOf('↓ ') >= 0);
  const note = el('vacNote').innerHTML || '';
  const upN = +((note.split('<b>')[1] || '').split(' 個行業的成長趨勢')[0] || 0);
  const ok = bars.length >= 10 && live.length > 0 && upN > 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　缺工面板 ${bars.length} 列，${live.length} 列有方向與年變化率，${upN} 個行業顯著上升`);
  console.log();
}

// 業務情境入口：按「議員質詢某區」要把地區切到板橋、把問題填進框、但不自動送出
{
  const before = fetchCalls.length;
  el('askInput').value = '';
  (el('scenarios').handlers.click || []).forEach((fn) => fn({ target: { dataset: { scn: 'quiz' } } }));
  const filled = el('askInput').value.indexOf('板橋區') >= 0;
  const switched = el('area').value === '新北市板橋區' && (el('heroLabel').innerHTML || el('heroLabel').textContent).indexOf('18-35') >= 0;
  const notSent = fetchCalls.length === before && el('askText').textContent.indexOf('情境') < 0 && el('askMeta').textContent.indexOf('情境入口') >= 0;
  const ok = filled && switched && notSent;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　情境入口：填入問題 ${filled ? '✓' : '✗'}　切到板橋區 ${switched ? '✓' : '✗'}　未自動送出 ${notSent ? '✓' : '✗'}`);
  console.log();
  el('area').value = '新北市';
  (el('area').handlers.change || []).forEach((fn) => fn());
}

// pipeline 狀態：每個來源都要有進件方式、最後抓取時間（人工／上傳的除外）與檢查；規模那一行要從資料算
{
  const t = el('pipeTable').innerHTML || '';
  const rowsN = (t.match(/<tr>/g) || []).length;
  const auto = (t.match(/badge ntpc">自動/g) || []).length;
  const manual = (t.match(/badge est">人工/g) || []).length;
  const stamped = t.split('<td class="mono">').filter((x) => /^20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}/.test(x)).length;
  const scale = el('scaleLine').innerHTML || '';
  const scaleOk = scale.indexOf('6 個資料提供單位') >= 0 && scale.indexOf('782 筆') >= 0 && scale.indexOf('1978–') >= 0;
  const method = el('methodBox').innerHTML || '';
  const methodOk = method.indexOf('pp') >= 0 && method.indexOf('三道查核') >= 0 && method.indexOf('逐數字驗證') >= 0;
  const ok = rowsN >= 12 && auto >= 10 && manual === 1 && stamped >= 11 && scaleOk && methodOk;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　pipeline 狀態：${rowsN} 個來源（自動 ${auto}、人工 ${manual}）、${stamped} 個有抓取時間；規模行 ${scaleOk ? '✓' : '✗'}；方法說明含回測數字 ${methodOk ? '✓' : '✗'}`);
  console.log();
}

// 青年基本法權益覆蓋：八個面向都要出現，有資料／部分／缺口的數量要跟資料一致，缺口要附候選來源
{
  const h = el('rightsBox').innerHTML || '';
  const rows = (h.match(/class="rrow /g) || []).length;
  const covered = (h.match(/rrow covered/g) || []).length, partial = (h.match(/rrow partial/g) || []).length, gap = (h.match(/rrow rgap/g) || []).length;
  const note = el('rightsNote').innerHTML || '';
  const counted = note.indexOf(covered + ' 個有資料') >= 0 && note.indexOf(partial + ' 個部分') >= 0 && note.indexOf(gap + ' 個缺口') >= 0;
  const arrows = (h.match(/ ← /g) || []).length;
  const ok = rows === 17 && covered + partial + gap === 17 && counted && arrows >= 20 && h.indexOf('就業職涯') >= 0 && h.indexOf('國際交流') >= 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　權益覆蓋：${rows} 個面向（有資料 ${covered}、部分 ${partial}、缺口 ${gap}），${arrows} 條缺口附了候選來源`);
  console.log();
}

// 供需錯配：職缺 × 就業人數的方向矩陣。每個有職缺資料的行業都要有一列、要有優先（p0）的行業、要標全國全年齡
{
  const h = el('mismatchBox').innerHTML || '';
  const rows = (h.match(/<tr class="p[0-3]">/g) || []).length;
  const p0 = (h.match(/<tr class="p0">/g) || []).length;
  const scoped = h.indexOf('全國 · 全年齡') >= 0 && h.indexOf('行業 × 年齡') >= 0;
  const ok = rows >= 15 && p0 >= 1 && scoped && h.indexOf('缺工擴大') >= 0;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　供需錯配：${rows} 個行業、${p0} 個「缺工擴大但人沒進去」、範圍標示 ${scoped ? '✓' : '✗'}`);
  console.log();
}

// 貼表格文字：按「填例子」要填進去，按「辨識」要送 scan_text，空白時不送
{
  const before = fetchCalls.length;
  el('pasteText').value = '';
  (el('pasteGo').handlers.click || []).forEach((fn) => fn({}));
  const emptyBlocked = fetchCalls.length === before && el('askText').textContent.indexOf('先貼') >= 0;
  (el('pasteSample').handlers.click || []).forEach((fn) => fn({}));
  const filled = el('pasteText').value.indexOf('應屆畢業生') >= 0 && el('pasteText').value.indexOf('合計') >= 0;
  (el('pasteGo').handlers.click || []).forEach((fn) => fn({}));
  const call = fetchCalls[fetchCalls.length - 1];
  const sent = fetchCalls.length === before + 1 && call.body.action === 'scan_text' && call.body.text.indexOf('應屆畢業生') >= 0;
  const ok = emptyBlocked && filled && sent;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　貼表格文字：空白不送 ${emptyBlocked ? '✓' : '✗'}　例子含文字標籤 ${filled ? '✓' : '✗'}　送到 scan_text ${sent ? '✓' : '✗'}`);
  console.log();
}

// 匯出報告（Spec P2-3）：按下去要 (1) 把所有收合區展開 (2) 填好封面 (3) 叫列印
// (4) 列印完把收合狀態還原。少了 (1) 印出來全是標題；少了 (4) 使用者回到畫面時全部展開。
{
  const folds = [{ open: false }, { open: true }, { open: false }];
  const origQSA = document.querySelectorAll;
  document.querySelectorAll = (sel) => (sel === 'details' ? folds : []);
  let printed = 0; const winHandlers = {};
  sandbox.window.print = () => { printed++; };
  sandbox.window.addEventListener = (e, fn) => { (winHandlers[e] = winHandlers[e] || []).push(fn); };
  sandbox.window.removeEventListener = () => {};
  const r = sandbox.__export();
  const allOpen = folds.every((d) => d.open);
  const head = el('reportHead').innerHTML || '';
  const headOk = /新北市　青年統計簡報/.test(head) && /18–35 歲/.test(head) && /資料版本 20\d\d-/.test(head);
  (winHandlers.afterprint || []).forEach((fn) => fn());
  const restored = folds[0].open === false && folds[1].open === true && folds[2].open === false;
  document.querySelectorAll = origQSA;
  const ok = r && r.opened === 3 && allOpen && printed === 1 && headOk && restored;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　匯出報告：展開 ${allOpen ? '是' : '否'}、封面 ${headOk ? '是' : '否'}、叫列印 ${printed} 次、列印後還原 ${restored ? '是' : '否'}`);
  console.log();
}

// 48 年全國分齡序列：這是手上最長也唯一分齡的資料，資料一直都在快取裡，
// 先前只取了最新一年。斷掉的話會無聲少一張圖，所以驗它畫出來了。
const trendHtml = [...el('trendBox').children].map((c) => c.innerHTML || '').join('');
const natOk = /1978/.test(trendHtml) && /分齡勞動力參與率/.test(trendHtml);
total++;
if (!natOk) fail++;
console.log(`${natOk ? '✅' : '❌'}　48 年全國分齡序列有畫出來`);
// 地圖要有四個指標鈕：人口、勞參率、所得中位數（財政部）、工作機會密度（普查）
{
  const mmh = el('mapMetric').innerHTML || '';
  const ok = ['人口數', '勞動力參與率', '綜合所得中位數', '工作機會密度'].every((m) => mmh.indexOf('data-m="' + m + '"') >= 0);
  total++; if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　地圖四個指標鈕（含行政區所得與工作機會密度）`);
}

// 折線圖必須可以滑。先前每一張都是靜止的圖片 —— 看得到形狀，
// 卻讀不出任何一年的值，圖表等於只剩裝飾功能。
const charts = [...el('trendBox').children].filter((c) => /<polyline/.test(c.innerHTML || ''));
const withHover = charts.filter((c) => /class="lc-tip"/.test(c.innerHTML || ''));
const hoverOk = charts.length > 0 && withHover.length === charts.length;
total++;
if (!hoverOk) fail++;
console.log(`${hoverOk ? '✅' : '❌'}　${charts.length} 張折線圖，可滑讀數值的 ${withHover.length} 張`);
console.log();

// 施政建議：命題的「提供施政協助」，整段腳本跑完後應該有內容
const policyCount = el('policyList').children.length;
const policyOk = policyCount >= 3;
total++;
if (!policyOk) fail++;
console.log(`${policyOk ? '✅' : '❌'}　施政建議產出 ${policyCount} 條`);

// 異常與洞察（Spec P1-3）：每張卡都必須帶信心度與依據。
// 只數張數不夠 —— 這一區整個賣點就是「每個發現都查得到出處」，
// 少了 pill 或 basis 等於把統計宣稱變成沒有來源的斷言。
// 直接檢查渲染出來的 HTML 字串，而不是幫假 DOM 加上 querySelector ——
// 假 DOM 一旦比瀏覽器多會些什麼，驗證就開始比真實環境寬鬆。
const insightCards = [...el('insightList').children];
const insightBad = insightCards.filter(
  (c) => !/class="pill /.test(c.innerHTML) || !/class="basis"/.test(c.innerHTML),
);
const insightOk = insightCards.length >= 1 && insightBad.length === 0;
total++;
if (!insightOk) fail++;
console.log(
  `${insightOk ? '✅' : '❌'}　異常洞察產出 ${insightCards.length} 條` +
    (insightBad.length ? `（${insightBad.length} 條缺少信心度或依據）` : '，每條都有信心度與依據'),
);
// 主數字必須落在正確的最終值。數字動畫在假 DOM 裡沒有 requestAnimationFrame，
// 所以這項一併驗證動畫程式有做能力偵測 —— 沒做的話整段腳本會在載入時就爆。
const heroText = el('heroValue').textContent;
const heroOk = /^[0-9][0-9,]*$/.test(heroText);
total++;
if (!heroOk) fail++;
console.log(`${heroOk ? '✅' : '❌'}　主數字落在最終值 ${JSON.stringify(heroText)}（動畫需有能力偵測）`);
console.log();

// 地圖：29 個行政區都要畫出來，而且每一塊都要掛得上 data-name（hover/click 靠它）。
// 只檢查「有沒有 svg 內容」會漏掉「畫出來但點不動」這種情形。
const mapHtml = el('ntpcMap').innerHTML || '';
const pathCount = (mapHtml.match(/<path /g) || []).length;
const namedCount = (mapHtml.match(/data-name="/g) || []).length;
// 上色必須走 style。SVG 呈現屬性不解析 var()，寫成 fill="var(--x)" 會畫出
// 29 個看不見的形狀 —— 數量檢查全過，畫面卻是空的。所以要驗上色方式本身。
const styledFill = (mapHtml.match(/style="fill:/g) || []).length;
const badAttrFill = /<path[^>]*\sfill="var\(/.test(mapHtml);
const mapOk = pathCount === 29 && namedCount === 29 && styledFill === 29 && !badAttrFill;
total++;
if (!mapOk) fail++;
console.log(`${mapOk ? '✅' : '❌'}　地圖畫出 ${pathCount} 個行政區、可互動 ${namedCount} 個、有上色 ${styledFill} 個（應各為 29）`
  + (badAttrFill ? ' ← fill 寫成呈現屬性，Chrome 不會解析 var()' : ''));

// 圖例：色階沒有標示對應數值就只是好看的顏色
const legendHtml = el('mapLegend').innerHTML || '';
const legendOk = /class="ramp"/.test(legendHtml) && /分位數分級/.test(legendHtml);
total++;
if (!legendOk) fail++;
console.log(`${legendOk ? '✅' : '❌'}　地圖圖例有色階與分級說明`);
console.log();

// 下一個淡水：散佈圖、係數、相似區、殘差四張圖都要畫出來；參考區預設淡水；散佈圖上色要走 style
{
  // 主區塊：走勢圖與相似區、殘差直接看；散佈圖與係數收在「模型怎麼算的」摺頁裡，所以要往下找
  const walk = (n, out) => { (n.children || []).forEach((c) => { out.push(c); walk(c, out); }); return out; };
  const figs = ['drvTrend', 'drvSimilar', 'drvResid', 'drvModel'].reduce((acc, id) => walk(el(id), acc), []);
  const keys = figs.map((f) => f['data-chart']).filter(Boolean);
  const html = figs.map((f) => f.innerHTML || '').join('');
  const dots = (html.match(/class="sc-dot /g) || []).length;
  const badFill = /<circle class="sc-dot[^>]*\sfill="var\(/.test(html);
  const refOk = /新北市淡水區<small>參考區/.test(html);
  const coefOk = /模型 A/.test(html) && /模型 B/.test(html) && /★/.test(html);
  const watch = walk(el('watchBox'), []).map((c) => c.innerHTML || '').join('');
  const watchOk = /正在移入/.test(watch) && /下一個候選/.test(watch) && /問 AI/.test(watch);
  const ok = ['migration', 'drivers', 'drivers-coef', 'drivers-similar', 'drivers-resid'].every((k) => keys.indexOf(k) >= 0) && dots >= 150 && !badFill && refOk && coefOk && watchOk;
  total++;
  if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　下一個淡水：${keys.length} 張圖（含走勢）、散佈 ${dots} 點、參考區淡水 ${refOk ? '✓' : '✗'}、影響力表 ${coefOk ? '✓' : '✗'}、預警摘要 ${watchOk ? '✓' : '✗'}${badFill ? ' ← fill 寫成呈現屬性' : ''}`);
  console.log();
}

// 公式工具庫：八條算法都要有白話、公式、用在哪、限制
{
  const h = el('formulaList').innerHTML || '';
  const n = (h.match(/<details class="fx"/g) || []).length;
  const ok = n >= 8 && /世代淨遷入/.test(h) && /多元迴歸/.test(h) && (h.match(/class="where"><b>限制<\/b>/g) || []).length === n && /高 10% 的區/.test(h);
  total++; if (!ok) fail++;
  console.log(`${ok ? '✅' : '❌'}　公式工具庫：${n} 條，每條有白話／公式／用在哪／限制`);
  console.log();
}

console.log(fail === 0 ? `全部 ${total} 項通過` : `❌ ${total} 項裡有 ${fail} 項失敗`);
process.exit(fail === 0 ? 0 : 1);
