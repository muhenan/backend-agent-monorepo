const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const path = require('node:path');

const encoder = new TextEncoder();
const empty = { answer: 'reply', memory_searches: [], recalled_memories: [], memory_write: { status: 'no_change', events: [] } };
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`;
const completeEvents = (payload = empty) => [
  { type: 'status', phase: 'thinking' },
  { type: 'text_delta', delta: payload.answer },
  { type: 'answer_done', answer: payload.answer },
  { type: 'memory_write_start' },
  { type: 'memory_write_done', memory_write: payload.memory_write },
  { type: 'done', ...payload },
];

function streamResponse(events, chunkSize = 13) {
  const bytes = encoder.encode(events.map(frame).join(''));
  return { ok: true, body: new ReadableStream({
    start(controller) {
      for (let index = 0; index < bytes.length; index += chunkSize) {
        controller.enqueue(bytes.slice(index, index + chunkSize));
      }
      controller.close();
    },
  }) };
}

function setup(events = completeEvents(), responseFactory) {
  const elements = new Map();
  const created = [];
  function element() {
    const children = new Map();
    const classes = new Set();
    return { innerHTML: '', textContent: '', value: '', style: {}, disabled: false,
      scrollTop: 0, scrollHeight: 800, clientHeight: 800, appended: [],
      classList: { add(...names) { names.forEach((name) => classes.add(name)); }, remove(...names) { names.forEach((name) => classes.delete(name)); }, contains(name) { return classes.has(name); } },
      addEventListener() {}, append(node) { this.appended.push(node); }, focus() {},
      querySelector(selector) { if (!children.has(selector)) children.set(selector, element()); return children.get(selector); },
      querySelectorAll() { return []; },
    };
  }
  const context = vm.createContext({
    document: {
      querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
      querySelectorAll() { return []; }, createElement() { const node = element(); created.push(node); return node; },
    },
    TextDecoder, Set,
    localStorage: { getItem() { return null; }, setItem() {} },
    window: { setTimeout() {}, confirm() { return true; } },
    fetch: async (url, options) => {
      if (options?.method === 'POST') {
        context.lastUrl = url;
        context.postCount = (context.postCount || 0) + 1;
        context.lastRequest = JSON.parse(options.body);
        return responseFactory ? responseFactory() : streamResponse(events);
      }
      return { ok: true, json: async () => ({ memories: [] }) };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/agent_mem/static/app.js'), 'utf8'), context);
  function turn() { return { row: elements.get('#messages').appended.at(-1), activity: created.at(-1) }; }
  return { context, elements, created, turn };
}

test('SSE parser handles arbitrary CRLF splits, comments, event names and multiline data', () => {
  const { context } = setup();
  vm.runInContext('var events = []; var parser = createSSEParser((event) => events.push(event))', context);
  const input = ': heartbeat\r\nevent: text_delta\r\ndata: {"delta":\r\ndata: "你好"}\r\n\r\ndata: {"type":"done"}\n\n';
  for (const char of input) vm.runInContext(`parser.push(${JSON.stringify(char)})`, context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(events)', context)), [{ delta: '你好', type: 'text_delta' }, { type: 'done' }]);
});

test('truncated SSE frame is not treated as a confirmed result', () => {
  const { context } = setup();
  vm.runInContext('var events = []; var parser = createSSEParser((event) => events.push(event))', context);
  vm.runInContext(`parser.push('data: {"type":"done"}\\n', true)`, context);
  assert.equal(vm.runInContext('events.length', context), 0);
});

test('streams UTF-8 safely with bytes split inside Chinese characters', async () => {
  const payload = { ...empty, answer: '你好，记忆工具。' };
  const { context, turn } = setup([], () => streamResponse(completeEvents(payload), 1));
  await vm.runInContext("sendMessage('hello')", context);
  assert.equal(turn().row.querySelector('.message-content').textContent, payload.answer);
  assert.equal(vm.runInContext('state.history[1].content', context), payload.answer);
  assert.equal(context.lastUrl, '/api/chat/stream');
});

test('renders text before completion and records answer before memory write finishes exactly once', async () => {
  let controller;
  const response = { ok: true, body: new ReadableStream({ start(value) { controller = value; } }) };
  const { context, elements, turn } = setup([], () => response);
  const pending = vm.runInContext("sendMessage('first')", context);
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  controller.enqueue(encoder.encode(frame({ type: 'text_delta', delta: 'hello' })));
  await settle();
  assert.equal(turn().row.querySelector('.message-content').textContent, 'hello');
  assert.equal(vm.runInContext('state.history.length', context), 0);
  assert.equal(elements.get('#send-button').disabled, true);
  assert.match(turn().activity.innerHTML, /正在生成回复/);
  controller.enqueue(encoder.encode(frame({ type: 'answer_done', answer: 'hello world' }) + frame({ type: 'memory_write_start' })));
  await settle();
  assert.equal(vm.runInContext('state.history.length', context), 2);
  assert.equal(vm.runInContext('state.history[1].content', context), 'hello world');
  assert.match(turn().activity.innerHTML, /正在检查并保存记忆/);
  assert.match(turn().activity.innerHTML, /应用在回答后/);
  controller.enqueue(encoder.encode(frame({ type: 'answer_done', answer: 'hello world' }) + frame({ type: 'done', ...empty, answer: 'hello world' })));
  controller.close();
  await pending;
  assert.equal(vm.runInContext('state.history.length', context), 2);
  assert.equal(elements.get('#send-button').disabled, false);
  assert.match(turn().activity.innerHTML, /未调用/);
  assert.match(turn().activity.innerHTML, /已尝试，无变更/);
});

test('tool activity progresses live, escapes query/results and retains limit attempts after done', () => {
  const { context } = setup();
  vm.runInContext("var current = createStreamingTurn('question')", context);
  const apply = (event) => vm.runInContext(`applyStreamEvent(current, ${JSON.stringify(event)})`, context);
  apply({ type: 'tool_start', id: 'call-1', name: 'search_memory', query: '<script>' });
  assert.match(vm.runInContext('current.activity.innerHTML', context), /正在查找记忆/);
  assert.match(vm.runInContext('current.activity.innerHTML', context), /&lt;script&gt;/);
  apply({ type: 'tool_end', id: 'call-1', name: 'search_memory', status: 'ok', memories: ['<img>'] });
  apply({ type: 'tool_start', id: 'call-2', name: 'search_memory', query: 'another' });
  apply({ type: 'tool_end', id: 'call-2', name: 'search_memory', status: 'limit_reached', memories: [] });
  apply({ type: 'done', ...empty, memory_searches: [{ query: '<script>', status: 'ok', memories: ['<img>'] }] });
  assert.equal(vm.runInContext('current.searches.length', context), 2);
  assert.match(vm.runInContext('current.activity.innerHTML', context), /&lt;img&gt;/);
  assert.match(vm.runInContext('current.activity.innerHTML', context), /达到搜索上限/);
});

test('shows actual saved records, no changes and write failures without claiming all attempts saved', () => {
  const { context } = setup();
  const label = vm.runInContext(`describeActivity(${JSON.stringify(empty)})`, context);
  assert.match(label, /未调用/);
  assert.match(label, /已尝试，无变更/);
  const payload = { ...empty, memory_searches: [{ query: '<script>', status: 'ok', memories: [] }, { query: 'name', status: 'error', memories: [] }], memory_write: { status: 'saved', events: [{ id: 'a', event: 'UPDATE', memory: '<img>' }] } };
  const html = vm.runInContext(`activityHtml(${JSON.stringify(payload)})`, context);
  assert.match(html.searchHtml, /没有匹配记忆/);
  assert.match(html.searchHtml, /查询失败/);
  assert.match(html.searchHtml, /&lt;script&gt;/);
  assert.match(html.writeHtml, /更新/);
  assert.match(html.writeHtml, /&lt;img&gt;/);
});

test('dropped stream preserves partial text, excludes partial history and never retries POST', async () => {
  const { context, turn } = setup([{ type: 'text_delta', delta: 'partial reply' }]);
  await vm.runInContext("sendMessage('hi')", context);
  assert.equal(turn().row.querySelector('.message-content').textContent, 'partial reply');
  assert.match(turn().activity.innerHTML, /输出中断/);
  assert.match(turn().activity.innerHTML, /未确认是否写入/);
  assert.equal(vm.runInContext('state.history.length', context), 0);
  assert.equal(context.postCount, 1);
});

test('answer_done survives later stream failure and stays in history with unconfirmed write', async () => {
  const { context, turn } = setup([{ type: 'text_delta', delta: 'final' }, { type: 'answer_done', answer: 'final' }, { type: 'memory_write_start' }, { type: 'error', phase: 'stream', message: 'connection lost' }]);
  await vm.runInContext("sendMessage('hi')", context);
  assert.equal(turn().row.querySelector('.message-content').textContent, 'final');
  assert.match(turn().activity.innerHTML, /完整回答已保留/);
  assert.match(turn().activity.innerHTML, /未确认是否写入/);
  assert.equal(vm.runInContext('state.history[1].content', context), 'final');
  assert.equal(context.postCount, 1);
});

test('confirmed memory write is preserved when final transport frame is lost', async () => {
  const write = { status: 'saved', events: [{ id: 'record-1', event: 'ADD', memory: 'likes Chinese' }] };
  const { context, turn } = setup([{ type: 'answer_done', answer: 'final' }, { type: 'memory_write_done', memory_write: write }]);
  await vm.runInContext("sendMessage('hi')", context);
  assert.match(turn().activity.innerHTML, /新增 1/);
  assert.match(turn().activity.innerHTML, /record-1/);
  assert.doesNotMatch(turn().activity.innerHTML, /未确认是否写入/);
});

test('sends only previous complete exchanges, caps history at 20 and resets identity', async () => {
  const { context, elements } = setup();
  await vm.runInContext("sendMessage('first')", context);
  assert.equal(context.lastRequest.history.length, 0);
  await vm.runInContext("sendMessage('second')", context);
  assert.equal(context.lastRequest.history.length, 2);
  assert.equal(context.lastRequest.history[0].content, 'first');
  for (let index = 0; index < 12; index += 1) await vm.runInContext(`sendMessage('turn-${index}')`, context);
  assert.equal(vm.runInContext('state.history.length', context), 20);
  assert.equal(context.lastRequest.history.length, 20);
  elements.get('#user-id').value = 'other-user';
  vm.runInContext('applyUserId()', context);
  assert.equal(vm.runInContext('state.history.length', context), 0);
});

test('identity and new-message guards remain active while memory write is running', () => {
  const { context, elements } = setup();
  vm.runInContext("state.busy = true; state.history = [{role:'user',content:'existing'}]", context);
  elements.get('#user-id').value = 'other-user';
  vm.runInContext('applyUserId()', context);
  assert.equal(vm.runInContext('state.userId', context), 'demo-user');
  assert.equal(vm.runInContext('state.history.length', context), 1);
  vm.runInContext("sendMessage('ignored')", context);
  assert.equal(context.postCount, undefined);
});

test('write failure is visible and keeps the complete reply in current conversation', async () => {
  const payload = { ...empty, memory_write: { status: 'error', events: [] } };
  const { context, turn } = setup(completeEvents(payload));
  await vm.runInContext("sendMessage('hi')", context);
  assert.match(turn().activity.innerHTML, /失败，回答已保留/);
  assert.equal(vm.runInContext('state.history[1].content', context), 'reply');
});
