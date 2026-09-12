// Offline renderer regression: exercises production functions, no browser/network.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../data/preview_template.html', import.meta.url), 'utf8');
const start = source.indexOf('  function scanEscape(');
const end = source.indexOf('  (function bindPaste()', start);
assert.ok(start >= 0 && end > start, 'scan renderer section must exist');
const out = { hidden: true, innerHTML: '' };
const malicious = '<img src=x onerror="alert(1)">&\' injected';
const scope = {
  $: () => out, API: '/offline-only',
  num: value => String(value), confLabel: value => value,
  failText: (label, error) => label + ': ' + error.message,
  fetch: () => new Promise(() => {}),
  FileReader: class { readAsDataURL() {} },
};
vm.createContext(scope);
vm.runInContext(source.slice(start, end), scope);
function safeOutput() {
  assert.ok(!out.innerHTML.includes('<img'), 'payload must never form an image element');
  assert.ok(out.innerHTML.includes('&lt;img'), 'payload remains visible as literal text');
}
scope.scanText('15-24,100', malicious + '.csv');
safeOutput();
scope.scanImage({ name: malicious + '.png', type: 'image/png' });
safeOutput();
scope.renderScan({ ok: false, error: malicious });
safeOutput();
scope.renderScan({
  ok: true, region: malicious, year: malicious, metric: malicious, unit: malicious, model: malicious,
  rows: [{ age_label: malicious, value: malicious, band: malicious }],
  inferred: { [malicious]: { applied_label: malicious, basis: malicious } },
  printed_total: malicious, computed_total: malicious, issues: [malicious],
  aligned: [{ age_group: malicious, value: 1, provenance: {
    confidence: 'high" onmouseover="alert(1)', source_age_group: malicious,
  } }],
});
safeOutput();
assert.ok(out.innerHTML.includes('class="pill low"'), 'unknown confidence must use safe class');
assert.ok(!out.innerHTML.includes('onmouseover='), 'confidence cannot add an event attribute');
assert.ok(!out.innerHTML.includes(malicious), 'all raw payloads escaped, including summary');
// Catch branch must escape exception text too, retaining intentional line breaks.
scope.fetch = () => Promise.reject(new Error(malicious));
scope.scanText('15-24,100', 'normal.csv');
await new Promise(resolve => setImmediate(resolve));
safeOutput();
console.log('PASS scan filenames, metadata, table cells, inferred labels, issues, totals, confidence and error escaping');
