const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parse, duplicates, importEntries, Controller} = require('../static/inventory/js/serial-validation.js');

test('normalization matches server rules and preserves zeroes and internal characters', () => {
  assert.deepEqual(parse(' 0001,abc\r\n ABC \nStraße\nA|B'), ['0001', 'ABC', 'ABC', 'STRASSE', 'A|B']);
});
test('same-field and cross-row duplicates identify every affected row', () => {
  const entries = [{key:'a', label:'Line 1', value:'001\n 001'}, {key:'b', label:'Line 2', value:'001,DEF'}];
  const errors = duplicates(entries);
  assert.equal(errors.size, 2);
  assert.match(errors.get('b')[0], /001.*Line 1, Line 2/);
  entries[0].value = '0001';
  assert.equal(duplicates(entries).size, 0);
});
test('excluded import rows do not conflict, and import pipe delimiters are normalized', () => {
  const entries = importEntries([{row: 7, serial_numbers:'001|002'}, {excluded:true, serial_numbers:'001'}]);
  assert.equal(entries.length, 1);
  assert.deepEqual(parse(entries[0].value), ['001', '002']);
});
test('a stale remote response cannot replace feedback for more recent input', async () => {
  const originalFetch = global.fetch, waiting = [];
  global.fetch = () => new Promise(resolve => waiting.push(resolve));
  try {
    const controller = Object.create(Controller.prototype);
    Object.assign(controller, {revision:0, request:null, verified:null, remote:new Set(),
      form:{dataset:{serialValidationUrl:'/check'},querySelector(){return {value:'csrf'};}},
      entries(){return [];}, snapshot(){return {serials:[this.serial], mode:'purchase'};},
      render(){return this.remote.size === 0;}, serial:'OLD'});
    const first = controller.validate();
    controller.serial = 'NEW'; const second = controller.validate();
    waiting[1]({ok:true,json:async()=>({conflicts:[],reason:'exists'})});
    assert.equal(await second, true);
    waiting[0]({ok:true,json:async()=>({conflicts:['OLD'],reason:'exists'})});
    assert.equal(await first, false);
    assert.equal(controller.remote.size, 0);
  } finally { global.fetch = originalFetch; }
});
test('failed live requests retain local validation and show a server-validation fallback notice', async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => { throw Error('offline'); };
  try {
    const controller = Object.create(Controller.prototype);
    Object.assign(controller, {revision:0, request:null, verified:null, remote:new Set(),
      form:{dataset:{serialValidationUrl:'/check'},querySelector(){return {value:'csrf'};}},
      entries(){return [{key:'a',label:'Line 1',value:'ABC,abc'}];},
      snapshot(){return {serials:['ABC'],mode:'purchase'};},
      render(entries, notice){this.notice = notice; return duplicates(entries).size === 0;}});
    assert.equal(await controller.validate(), false);
    assert.match(controller.notice, /unavailable.*before stock is changed/);
  } finally { global.fetch = originalFetch; }
});
