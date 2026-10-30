const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('inventory/static/inventory/js/inventory-ui.js', 'utf8');
function setup() {
  const product = { value: '1', addEventListener(type, fn) { this[type] = fn; } };
  const cost = { value: '77.123456' };
  const line = {
    dataset: {},
    querySelector(selector) {
      if (selector === "select[name$='-product']") return product;
      if (selector === "input[name$='-unit_cost']") return cost;
      return null;
    },
    querySelectorAll() { return []; }
  };
  const context = { productMeta: { '1': { buying_price: '123.456789' }, '2': { buying_price: '0.000000' }, '3': {} },
    enhanceProductSelect() {}, syncLine() {} };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('      function formatPurchaseCost('), source.indexOf('      function appendLine(')), context);
  return { product, cost, line, context };
}
test('draft initialization and manual edits preserve costs; selection supplies exact defaults', () => {
  const { product, cost, line, context } = setup();
  context.bindLine(line);
  assert.equal(cost.value, '77.123456');
  product.change();
  assert.equal(cost.value, '123.456789');
  cost.value = '88.555555';
  context.syncLine(line);
  assert.equal(cost.value, '88.555555');
  product.value = '2'; product.change();
  assert.equal(cost.value, '0');
  product.value = '3'; product.change();
  assert.equal(cost.value, '');
  product.value = ''; product.change();
  assert.equal(cost.value, '');
});
test('default costs omit trailing zeroes without rounding significant decimals', () => {
  const { context, product, cost, line } = setup();
  context.bindLine(line);
  for (const [raw, expected] of [
    ['100.000000', '100'], ['100.500000', '100.5'],
    ['100.050000', '100.05'], ['100.000001', '100.000001'],
    ['9999999999.123450', '9999999999.12345'], ['0.000000', '0']
  ]) {
    context.productMeta['1'].buying_price = raw;
    product.change();
    assert.equal(cost.value, expected);
  }
});
test('bulk selection supplies the same catalog default', () => {
  const { cost, line, context } = setup();
  const select = { replaceChildren() {} };
  const originalQuery = line.querySelector;
  line.querySelector = selector => selector === "select[name$='-product']" ? Object.assign(select, {value: '1'}) : originalQuery(selector);
  Object.assign(context, {
    totalForms: { value: '0' }, template: {innerHTML: '<article></article>'},
    document: {createElement() { return { firstElementChild: line }; }},
    Option: function () {}, formset: {appendChild() {}}, bindLine() {}
  });
  vm.runInContext(source.slice(source.indexOf('      function appendLine('), source.indexOf('      formset.querySelectorAll("[data-purchase-line]").forEach(bindLine);')), context);
  context.appendLine({id: 1, buying_price: '45.123456'});
  assert.equal(cost.value, '45.123456');
  assert.equal(context.totalForms.value, '1');
});
