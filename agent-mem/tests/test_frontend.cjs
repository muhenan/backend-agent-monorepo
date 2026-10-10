const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const path = require('node:path');

function setup(payload) {
  const elements = new Map();
  function element() {
    const children = new Map();
    return { innerHTML: '', textContent: '', value: '', style: {}, disabled: false,
      classList: { add() {}, remove() {} }, addEventListener() {}, append() {}, after() {}, focus() {},
      querySelector(selector) { if (!children.has(selector)) children.set(selector, element()); return children.get(selector); },
      querySelectorAll() { return []; },
    };
  }
  const context = vm.createContext({
    document: {
      querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
      querySelectorAll() { return []; }, createElement: element,
    },
    localStorage: { getItem() { return null; }, setItem() {} },
    window: { setTimeout() {}, confirm() { return true; } },
    fetch: async (url, options) => {
      if (options?.method === 'POST') {
        context.lastRequest = JSON.parse(options.body);
        return { ok: true, json: async () => payload };
      }
      return { ok: true, json: async () => ({ memories: [] }) };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/agent_mem/static/app.js'), 'utf8'), context);
  return { context, elements };
}

const empty = { answer: 'reply', memory_searches: [], recalled_memories: [], memory_write: { status: 'no_change', events: [] } };

test('shows skipped search and attempted write without claiming saved', () => {
  const { context } = setup(empty);
  const label = vm.runInContext(`describeActivity(${JSON.stringify(empty)})`, context);
  assert.match(label, /未调用/);
  assert.match(label, /已尝试，无变更/);
});

test('distinguishes empty search, search failure and actual update; escapes content', () => {
  const { context } = setup(empty);
  const payload = { ...empty, memory_searches: [{query:'<script>', status:'ok', memories:[]}, {query:'name', status:'error', memories:[]}], memory_write:{status:'saved', events:[{id:'a',event:'UPDATE',memory:'<img>'}]} };
  const html = vm.runInContext(`activityHtml(${JSON.stringify(payload)})`, context);
  assert.match(html.searchHtml, /没有匹配记忆/);
  assert.match(html.searchHtml, /查询失败/);
  assert.match(html.searchHtml, /&lt;script&gt;/);
  assert.match(html.writeHtml, /更新/);
  assert.match(html.writeHtml, /&lt;img&gt;/);
});

test('sends only previous exchanges and resets context on identity switch', async () => {
  const { context, elements } = setup(empty);
  await vm.runInContext("sendMessage('first')", context);
  assert.equal(context.lastRequest.history.length, 0);
  await vm.runInContext("sendMessage('second')", context);
  assert.equal(context.lastRequest.history.length, 2);
  assert.equal(context.lastRequest.history[0].content, 'first');
  assert.match(elements.get('#recalled-list').innerHTML, /未调用/);
  elements.get('#user-id').value = 'other-user';
  vm.runInContext('applyUserId()', context);
  assert.equal(vm.runInContext('state.history.length', context), 0);
});

test('write failure is visible and keeps response in current conversation', async () => {
  const payload = {...empty, memory_write:{status:'error',events:[]}};
  const { context, elements } = setup(payload);
  await vm.runInContext("sendMessage('hi')", context);
  assert.match(elements.get('#saved-list').innerHTML, /失败，回答已保留/);
  assert.equal(vm.runInContext('state.history[1].content', context), 'reply');
});
