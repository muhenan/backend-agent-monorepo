const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../src/henan_rag/static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('initializePage().catch(showInitializationError);', '');

// 提供页面测试所需的最小 DOM；只模拟页面实际使用的属性和安全节点操作。
class Element {
  // 初始化元素。tag：标签名称；返回新元素，设置 children、属性和表单状态。
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.className = ''; this.textContent = ''; this.value = ''; this.files = [];
    this.disabled = false; this.hidden = false; this.checked = false; this.parentElement = null;
    const target = this;
    // 增删 CSS 类。name：类名；enabled：是否存在；无返回值。
    function toggle(name, enabled) {
      const classes = new Set(target.className.split(/\s+/).filter(Boolean));
      if (enabled) classes.add(name); else classes.delete(name);
      target.className = [...classes].join(' ');
    }
    // 移除 CSS 类。name：类名；无返回值。
    function remove(name) { toggle(name, false); }
    this.classList = {toggle, remove};
  }

  // 添加子元素。items：待追加的节点；无返回值，更新父子关系。
  append(...items) { for (const item of items) { this.children.push(item); item.parentElement = this; } }

  // 替换子元素。items：新的节点；无返回值，移除原父子关系。
  replaceChildren(...items) { for (const item of this.children) item.parentElement = null; this.children = []; this.append(...items); }

  // 设置属性。name：属性名；value：属性值；无返回值。
  setAttribute(name, value) { this.attributes[name] = value; }

  // 模拟键盘焦点。无参数；无返回值，在元素上记录获得焦点的状态。
  focus() { this.focused = true; }

  // 按标签查找后代。tag：标签名称；返回匹配元素数组。
  querySelectorAll(tag) {
    const result = [];
    // 深度遍历子树。root：父元素；无返回值，结果写入闭包数组。
    function walk(root) { for (const child of root.children) { if (child.tagName === tag) result.push(child); walk(child); } }
    walk(this); return result;
  }
}

// 构建隔离的前端运行环境。fetchImpl：模拟请求函数；saved：持久范围 JSON 或 null；返回上下文和元素表。
function environment(fetchImpl, saved = null) {
  const elements = new Map();
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const item = new Element(match[1]); item.hidden = /(?:^|\s)hidden(?:\s|$)/.test(match[2]);
    for (const attribute of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) item.setAttribute(attribute[1], attribute[2]);
    item.tabIndex = Number(item.attributes.tabindex ?? -1); elements.set(match[3], item);
  }
  const storage = new Map(); if (saved !== null) storage.set('henan-rag.knowledge-base-selection.v1', saved);
  // 查找测试元素。selector：#id；返回元素，缺失时立即失败。
  function querySelector(selector) { const item = elements.get(selector.slice(1)); assert.ok(item, selector); return item; }
  // 构建测试节点。tag：标签；返回模拟元素。
  function createElement(tag) { return new Element(tag); }
  // 读取测试存储。key：键；返回字符串或 null。
  function getItem(key) { return storage.get(key) ?? null; }
  // 写入测试存储。key：键；value：字符串；无返回值。
  function setItem(key, value) { storage.set(key, value); }
  // 接受用户删除确认。无参数；返回 true。
  function confirm() { return true; }
  const context = vm.createContext({document:{querySelector, createElement}, localStorage:{getItem,setItem}, fetch:fetchImpl,
    TextDecoder, AbortController, FormData, confirm, console});
  vm.runInContext(script, context);
  return {context, elements, storage};
}

// 构造 JSON 响应。data：响应内容；status：HTTP 状态；返回模拟 fetch Response。
function response(data, status = 200) {
  // 解析响应 JSON。无参数；返回 Promise<对象>。
  async function json() { if (status === 204) throw new Error('204 不应解析 JSON'); return data; }
  return {ok:status >= 200 && status < 300, status, json};
}

// 构造知识库 DTO。id：库 ID；name：库名；defaultFlag：是否默认；status：状态；返回对象。
function knowledgeBase(id, name, defaultFlag = false, status = 'active') {
  return {id,name,description:'',is_default:defaultFlag,status,last_error:null,document_count:0,chunk_count:0};
}

// 将 VM 数据转换到测试上下文。value：跨上下文数据；返回普通 JSON 值。
function plain(value) { return JSON.parse(JSON.stringify(value)); }

// 创建可手动完成的异步任务。无参数；返回 promise 及 resolve/reject，用于模拟网络乱序。
function deferred() {
  let resolve, reject;
  // 暴露任务完成回调。accept：resolve；decline：reject；无返回值。
  function executor(accept, decline) { resolve = accept; reject = decline; }
  const promise = new Promise(executor); return {promise,resolve,reject};
}

// 拼接测试节点的可见文字。root：节点；返回该树所有纯文本，便于判断过期响应。
function visibleText(root) { return [root.textContent, ...root.children.map(visibleText)].join(' '); }

// 验证新错误格式和 204 响应；避免 Error 展示 [object Object] 或空体 JSON 异常。
test('API handles empty 204 and structured/legacy error details', async function testApiErrors() {
  // 按路径模拟三种响应。url：请求地址；返回 Promise<Response>。
  async function fetch(url) {
    if (url === '/empty') return response(null,204);
    if (url === '/legacy') return response({detail:'旧错误'},409);
    return response({detail:{code:'KNOWLEDGE_BASE_BUSY',message:'知识库正在处理中',retryable:true}},409);
  }
  const {context} = environment(fetch);
  assert.equal(await context.api('/empty'), null);
  // 校验携带 code 的业务错误。error：抛出的异常；返回断言是否满足。
  function structured(error) { return error.message === '知识库正在处理中' && error.code === 'KNOWLEDGE_BASE_BUSY' && error.retryable; }
  await assert.rejects(context.api('/bad'), structured);
  await assert.rejects(context.api('/legacy'), /旧错误/);
});

// 验证问答选择恢复必须读取所有分页，且管理目标与问答范围独立。
test('saved chat scope outside the first page survives management pagination', async function testScopePagination() {
  const libraries = [knowledgeBase('default','默认知识库',true),knowledgeBase('second','第二页知识库')];
  const requested = [];
  // 模拟分页知识库列表。url：带 offset 的路径；返回 Promise<Response>。
  async function fetch(url) {
    requested.push(url); const parsed = new URL(url,'http://localhost');
    const offset = Number(parsed.searchParams.get('offset'));
    return response({items:offset === 0 ? [libraries[0]] : [libraries[1]],total:2,default_knowledge_base_id:'default'});
  }
  const {context,elements} = environment(fetch,JSON.stringify(['second']));
  await context.refreshKnowledgeBases();
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),['second']);
  assert.equal(vm.runInContext('activeManagementKnowledgeBaseId',context),'default');
  assert.ok(requested.includes('/api/knowledge-bases?limit=200&offset=1'));
  assert.match(elements.get('selected-scope').textContent,/第二页知识库/);
  vm.runInContext('kbOffset = 1',context); await context.refreshKnowledgeBases();
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),['second']);
  assert.equal(vm.runInContext('activeManagementKnowledgeBaseId',context),'default');
});

// 验证空挂载不自动切回默认库，超过 8 个选择保持完整、失效库剔除且多选输入去重。
test('empty saved selection remains empty and selections above eight keep every active ID', async function testEmptyAndUnlimitedScope() {
  const bases = [knowledgeBase('default','默认知识库',true)];
  // 返回知识库页。url：接口地址；返回 Promise<Response>。
  async function fetch(url) { return response({items:bases,total:1,default_knowledge_base_id:'default'}); }
  const {context,elements} = environment(fetch,'[]'); await context.refreshKnowledgeBases();
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),[]);
  assert.equal(elements.get('chat').disabled,true);
  assert.equal(elements.get('retrieve').disabled,true);
  elements.get('question').value = '测试问题'; assert.equal(context.queryPayload(),null);
  assert.match(elements.get('query-status').textContent,/至少一个/);
  const all = new Map(); for (let i = 0; i < 10; i += 1) all.set(String(i),knowledgeBase(String(i),String(i)));
  all.set('inactive',knowledgeBase('inactive','删除失败',false,'delete_failed'));
  assert.equal(context.validScope(['missing','inactive','0','0','1','2','3','4','5','6','7','8','9'],all).length,10);
  vm.runInContext("selectedChatKnowledgeBaseIds = ['default']; queryBusy = true",context);
  context.renderChatScope(); assert.equal(elements.get('chat-scope').querySelectorAll('input')[0].disabled,true);
  await context.refreshKnowledgeBases(); assert.equal(elements.get('chat-scope').querySelectorAll('input')[0].disabled,true);
});

// 验证慢响应不能把 A 库的文档覆盖 B 库的新列表。
test('stale document response does not overwrite a switched management target', async function testStaleDocuments() {
  const old = deferred();
  // 延迟 A 库响应，B 库即时返回。url：文档列表路径；返回 Promise<Response>。
  async function fetch(url) {
    if (url.includes('/a/')) return old.promise;
    return response({items:[{document_id:'b-doc',source:'B资料',status:'ready',chunk_count:1,file_available:true}],total:1});
  }
  const {context,elements} = environment(fetch);
  vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context);
  const older = context.loadDocuments();
  vm.runInContext("activeManagementKnowledgeBaseId = 'b'",context); await context.loadDocuments();
  old.resolve(response({items:[{document_id:'a-doc',source:'A资料',status:'ready',chunk_count:1,file_available:true}],total:1}));
  await older;
  assert.match(visibleText(elements.get('documents')),/B资料/);
  assert.doesNotMatch(visibleText(elements.get('documents')),/A资料/);
});

// 验证旧请求的失败也不能污染已切换目标的状态。
test('stale document failure is ignored after target switch', async function testStaleDocumentFailure() {
  const old = deferred();
  // 延迟 A 库请求，B 库返回空页。url：文档路径；返回 Promise<Response>。
  async function fetch(url) { return url.includes('/a/') ? old.promise : response({items:[],total:0}); }
  const {context,elements} = environment(fetch);
  vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context); const older = context.loadDocuments();
  vm.runInContext("activeManagementKnowledgeBaseId = 'b'",context); await context.loadDocuments();
  elements.get('ingest-status').textContent = 'B 库状态'; old.reject(new Error('A 库旧错误')); await older;
  assert.equal(elements.get('ingest-status').textContent,'B 库状态');
  assert.match(visibleText(elements.get('documents')),/没有文档/);
});

// 验证上传捕获目标、不发送 tenant 参数且上传中禁切库，问答仍独立可用。
test('upload uses its captured knowledge-base target and locks only management controls', async function testUploadTarget() {
  const pending = deferred(), libraries = [knowledgeBase('a','产品资料'),knowledgeBase('default','默认知识库',true)];
  let uploadedUrl, uploadedBody;
  // 模拟上传以及后续列表刷新。url：请求地址；options：fetch 配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (options.method === 'POST') { uploadedUrl = url; uploadedBody = options.body; return pending.promise; }
    if (url.includes('/documents?')) return response({items:[],total:0});
    return response({items:libraries,total:2,default_knowledge_base_id:'default'});
  }
  const {context,elements} = environment(fetch);
  await context.refreshKnowledgeBases(); vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context); context.renderManagementTarget();
  elements.get('file').files = [new Blob(['资料'])];
  const uploading = context.uploadDocument();
  context.openManagementPage(); context.openChatPage();
  assert.equal(uploadedUrl,'/api/knowledge-bases/a/documents'); assert.equal(uploadedBody.get('tenant_id'),null);
  assert.equal(elements.get('upload').disabled,true); assert.equal(elements.get('create-kb').disabled,true);
  assert.equal(elements.get('knowledge-bases').querySelectorAll('button')[0].disabled,true);
  assert.equal(elements.get('chat').disabled,false);
  assert.equal(vm.runInContext('managementBusy',context),true);
  assert.equal(elements.get('management-page').hidden,true);
  pending.resolve(response({source:'资料.md',chunk_count:2},201)); await uploading;
  assert.match(elements.get('ingest-status').textContent,/已入库到「产品资料」/);
  assert.equal(elements.get('upload').disabled,false);
  context.openManagementPage(); assert.match(elements.get('ingest-status').textContent,/已入库到「产品资料」/);
});

// 验证删除选中库清除该范围，但不会自动挂载默认库。
test('deleting a selected library removes it without defaulting the chat scope', async function testDeleteScope() {
  let deleted = false;
  const original = [knowledgeBase('default','默认知识库',true),knowledgeBase('a','产品资料')];
  // 模拟删除库及刷新。url：接口路径；options：fetch 配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (options.method === 'DELETE') { assert.equal(url,'/api/knowledge-bases/a'); deleted = true; return response(null,204); }
    if (url.includes('/documents?')) return response({items:[],total:0});
    const items = deleted ? [original[0]] : original; return response({items,total:items.length,default_knowledge_base_id:'default'});
  }
  const {context,elements} = environment(fetch,JSON.stringify(['a'])); await context.refreshKnowledgeBases();
  vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context); await context.deleteKnowledgeBase();
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),[]);
  assert.equal(elements.get('chat').disabled,true); assert.equal(vm.runInContext('activeManagementKnowledgeBaseId',context),'default');
});

// 验证真实前端请求使用聊天范围，并保留回复时的名称快照；管理刷新不解锁正在回答的选择器。
test('chat captures selected scope independently and preserves answer snapshot on rename', async function testChatSnapshot() {
  const pending = deferred(); let body;
  const libraries = [knowledgeBase('default','默认知识库',true),knowledgeBase('a','管理库'),knowledgeBase('b','聊天库')];
  // 延迟聊天响应，其余请求返回完整库列表。url：接口；options：fetch 配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (url === '/api/chat/stream') { body = JSON.parse(options.body); return pending.promise; }
    return response({items:libraries,total:libraries.length,default_knowledge_base_id:'default'});
  }
  const {context,elements} = environment(fetch,JSON.stringify(['b'])); await context.refreshKnowledgeBases();
  vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context); elements.get('question').value = '测试问题';
  const answering = context.askAgent(); assert.deepEqual(body.knowledge_base_ids,['b']); assert.equal(body.tenant_id,undefined);
  context.openManagementPage(); assert.equal(elements.get('chat-page').hidden,true);
  libraries[2].name = '聊天库新名称'; await context.refreshKnowledgeBases();
  for (const input of elements.get('chat-scope').querySelectorAll('input')) assert.equal(input.disabled,true);
  assert.equal(elements.get('create-kb').disabled,false);
  const bytes = new TextEncoder().encode('event: text_delta\ndata: {"delta":"答案"}\n\nevent: done\ndata: {"answer":"答案","citations":[],"knowledge_bases":[{"id":"b","name":"聊天库"}]}\n\n');
  let consumed = false;
  // 一次返回 SSE 后结束。无参数；返回 Promise<reader result>。
  async function read() { if (consumed) return {done:true}; consumed = true; return {done:false,value:bytes}; }
  // 提供正常结束的取消函数。无参数；返回 Promise<void>。
  async function cancel() {}
  // 释放读取锁。无参数；无返回值。
  function releaseLock() {}
  // 提供 stream reader。无参数；返回 reader 对象。
  function getReader() { return {read,cancel,releaseLock}; }
  pending.resolve({ok:true,body:{getReader}}); await answering;
  assert.equal(elements.get('chat-page').hidden,true); context.openChatPage();
  assert.equal(elements.get('answer').textContent,'答案'); assert.equal(elements.get('answer-scope').textContent,'本次挂载：聊天库');
  assert.match(elements.get('selected-scope').textContent,/聊天库新名称/);
  assert.equal(elements.get('chat').disabled,false);
});

// 验证两个 Tab 的语义、键盘导航及页面切换只隐藏面板，不清空问答、文件或选择状态。
test('tabs keep one visible panel, ARIA links and existing inputs while keyboard navigation works', function testTabs() {
  const {context,elements} = environment();
  assert.equal(elements.get('chat-tab').attributes.role,'tab');
  assert.equal(elements.get('chat-tab').attributes['aria-controls'],'chat-page');
  assert.equal(elements.get('chat-page').attributes['aria-labelledby'],'chat-tab');
  assert.equal(elements.get('management-tab').attributes['aria-controls'],'management-page');
  assert.equal(elements.get('management-page').attributes.role,'tabpanel');
  assert.equal(elements.get('chat-page').hidden,false); assert.equal(elements.get('management-page').hidden,true);
  elements.get('question').value = '保留问题'; elements.get('answer').textContent = '保留回复';
  elements.get('file').files = [new Blob(['待上传'])];
  vm.runInContext("selectedChatKnowledgeBaseIds = ['chat-library']; activeManagementKnowledgeBaseId = 'management-library'",context);
  context.openManagementPage();
  assert.equal(elements.get('chat-page').hidden,true); assert.equal(elements.get('management-page').hidden,false);
  assert.equal(elements.get('management-tab').attributes['aria-selected'],'true');
  assert.equal(elements.get('chat-tab').tabIndex,-1);
  let prevented = false;
  // 记录方向键已被消费。无参数；无返回值，更新闭包中的 prevented。
  function preventDefault() { prevented = true; }
  context.navigateTabs({key:'ArrowRight',preventDefault});
  assert.equal(prevented,true); assert.equal(elements.get('chat-tab').focused,true);
  assert.equal(elements.get('chat-page').hidden,false); assert.equal(elements.get('management-page').hidden,true);
  assert.equal(elements.get('question').value,'保留问题'); assert.equal(elements.get('answer').textContent,'保留回复');
  assert.equal(elements.get('file').files.length,1);
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),['chat-library']);
  assert.equal(vm.runInContext('activeManagementKnowledgeBaseId',context),'management-library');
  context.navigateTabs({key:'End',preventDefault}); assert.equal(elements.get('management-page').hidden,false);
  context.navigateTabs({key:'Home',preventDefault}); assert.equal(elements.get('chat-page').hidden,false);
});

// 验证搜索只筛选可见库、全选包含搜索外的所有可用库，超过 8 个 ID 原样发送并可清空。
test('scope search, select-all and payload preserve more than eight libraries without truncation', async function testScopeTools() {
  const libraries = [];
  for (let i = 0; i < 12; i += 1) libraries.push(knowledgeBase(`kb-${i}`,`资料库 ${i}`,i === 0));
  libraries.push(knowledgeBase('inactive','已停用',false,'delete_failed'));
  // 返回全部知识库。url：请求路径；返回 Promise<Response>。
  async function fetch(url) { return response({items:libraries,total:libraries.length,default_knowledge_base_id:'kb-0'}); }
  const {context,elements,storage} = environment(fetch); await context.refreshKnowledgeBases();
  elements.get('scope-search').value = '资料库 11'; context.searchChatScope();
  assert.equal(elements.get('chat-scope').querySelectorAll('input').length,1);
  context.selectAllChatScope();
  elements.get('question').value = '所有库中的问题';
  assert.equal(context.queryPayload().knowledge_base_ids.length,12);
  assert.equal(JSON.parse(storage.get('henan-rag.knowledge-base-selection.v1')).length,12);
  assert.match(elements.get('selected-scope').textContent,/已挂载 12 个/);
  assert.match(elements.get('selected-scope').textContent,/另有 7 个/);
  assert.match(context.scopePreview(context.queryPayload().knowledge_base_ids),/另有 7 个知识库/);
  assert.match(context.referencePreview(libraries.slice(0,12)),/另有 7 个知识库/);
  assert.match(context.referenceNames(libraries.slice(0,12)),/资料库 11/);
  elements.get('scope-search').value = ''; context.searchChatScope();
  assert.equal(elements.get('chat-scope').querySelectorAll('input').length,12);
  for (const input of elements.get('chat-scope').querySelectorAll('input')) assert.equal(input.checked,true);
  vm.runInContext('selectedChatKnowledgeBaseIds = []',context); context.renderChatScope();
  for (const input of elements.get('chat-scope').querySelectorAll('input')) { input.checked = true; input.onchange(); }
  assert.equal(context.queryPayload().knowledge_base_ids.length,12);
  context.setQueryBusy(true,true); context.clearChatScope();
  assert.equal(vm.runInContext('selectedChatKnowledgeBaseIds.length',context),12);
  assert.equal(elements.get('select-all-scope').disabled,true); assert.equal(elements.get('clear-scope').disabled,true);
  context.setQueryBusy(false); context.clearChatScope();
  assert.deepEqual(plain(vm.runInContext('selectedChatKnowledgeBaseIds',context)),[]);
  assert.equal(elements.get('chat').disabled,true);
});

// 验证切到管理页后流式回答仍运行，返回聊天页可停止并保留已经显示的文字和工具状态。
test('switching tabs during SSE preserves the active request and allows stopping after returning', async function testTabDuringStream() {
  const waiting = deferred(), readPending = deferred(); let first = true, canceled = false;
  const libraries = [knowledgeBase('default','默认知识库',true)];
  const bytes = new TextEncoder().encode('event: tool_start\ndata: {"call_id":"call","knowledge_bases":[{"id":"default","name":"默认知识库"}]}\n\nevent: text_delta\ndata: {"delta":"已收到的内容"}\n\n');
  // 先返回一段回答，再等待取消。无参数；返回 Promise<reader result>。
  async function read() { if (first) { first = false; return {done:false,value:bytes}; } waiting.resolve(); return readPending.promise; }
  // 标记读取取消。无参数；返回 Promise<void>。
  async function cancel() { canceled = true; }
  // 模拟释放读取锁。无参数；无返回值。
  function releaseLock() {}
  // 提供持续运行的 reader。无参数；返回 reader。
  function getReader() { return {read,cancel,releaseLock}; }
  // 返回知识库列表或持续运行的流。url：接口；options：请求配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (url !== '/api/chat/stream') return response({items:libraries,total:1,default_knowledge_base_id:'default'});
    // 模拟浏览器在 AbortController 取消后停止网络读取。无参数；无返回值。
    function abortRead() { const error = new Error('已取消'); error.name = 'AbortError'; readPending.reject(error); }
    options.signal.addEventListener('abort',abortRead,{once:true}); return {ok:true,body:{getReader}};
  }
  const {context,elements} = environment(fetch); await context.refreshKnowledgeBases();
  elements.get('question').value = '测试流式回答'; const answering = context.askAgent(); await waiting.promise;
  context.openManagementPage();
  assert.equal(elements.get('chat-page').hidden,true); assert.equal(vm.runInContext('queryBusy',context),true);
  assert.equal(elements.get('answer').textContent,'已收到的内容'); assert.equal(elements.get('stop').hidden,false);
  assert.match(visibleText(elements.get('tool-calls')),/检索中/);
  context.openChatPage(); context.stopAnswer(); await answering;
  assert.equal(canceled,true); assert.equal(elements.get('answer').textContent,'已收到的内容');
  assert.match(elements.get('query-status').textContent,/已停止生成/);
  assert.match(visibleText(elements.get('tool-calls')),/已停止/);
  assert.equal(vm.runInContext('queryBusy',context),false); assert.equal(elements.get('stop').hidden,true);
});

// 验证超过旧有 1000 页的列表仍完整加载，真正的重复或空页保护继续生效。
test('scope loader has no page-count cap and rejects early empty pages', async function testUncappedPagination() {
  let calls = 0;
  // 每页提供一个不同的库，模拟总数超过旧分页上限。url：带 offset 的路径；返回 Promise<Response>。
  async function fetch(url) {
    calls += 1; const offset = Number(new URL(url,'http://localhost').searchParams.get('offset'));
    return response({items:[knowledgeBase(`kb-${offset}`,`库 ${offset}`)],total:1002});
  }
  const {context} = environment(fetch); const loaded = await context.loadAllKnowledgeBases();
  assert.equal(loaded.size,1002); assert.equal(calls,1002);
  // 返回一页后提前结束。url：分页地址；返回 Promise<Response>。
  async function emptyPage(url) { return response({items:[],total:2}); }
  const broken = environment(emptyPage); await assert.rejects(broken.context.loadAllKnowledgeBases(),/列表发生变化/);
});

// 验证 SSE 在中文 UTF-8 三字节和 CRLF 被拆分时仍正确解码。
test('SSE parser handles UTF-8 fragments, CRLF, zero-hit libraries and callback errors', async function testStreamFragments() {
  const {context,elements} = environment();
  const bytes = new TextEncoder().encode('event: text_delta\r\ndata: {"delta":"知识库"}\r\n\r\nevent: done\r\ndata: {"answer":"知识库"}\r\n\r\n');
  let index = 0, canceled = false, released = false;
  // 逐次返回 3 字节。无参数；返回 Promise<reader result>。
  async function read() { if (index >= bytes.length) return {done:true}; const value = bytes.slice(index,index + 3); index += 3; return {done:false,value}; }
  // 标记 reader 取消。无参数；返回 Promise<void>。
  async function cancel() { canceled = true; }
  // 标记 reader 解锁。无参数；无返回值。
  function releaseLock() { released = true; }
  // 获取 reader。无参数；返回读取对象。
  function getReader() { return {read,cancel,releaseLock}; }
  const events = [];
  // 保存解码事件。name：事件名；data：JSON；无返回值。
  function onEvent(name,data) { events.push([name,data]); }
  await context.readEventStream({body:{getReader}},onEvent);
  assert.deepEqual(plain(events),[['text_delta',{delta:'知识库'}],['done',{answer:'知识库'}]]);
  assert.equal(canceled,false); assert.equal(released,true);
  context.startTool({call_id:'call',knowledge_bases:[{id:'a',name:'产品'},{id:'b',name:'技术'}],question:'查询'});
  context.finishTool({call_id:'call',chunk_count:1,elapsed_ms:200,hits_by_knowledge_base:[{id:'a',name:'产品',chunk_count:1},{id:'b',name:'技术',chunk_count:0}]});
  assert.match(visibleText(elements.get('tool-calls')),/技术 0 条/);
  index = 0; released = false;
  // 模拟事件处理失败。name：事件名；data：JSON；无返回值，直接抛出异常。
  function fail(name,data) { throw new Error('事件失败'); }
  await assert.rejects(context.readEventStream({body:{getReader}},fail),/事件失败/);
  assert.equal(canceled,true); assert.equal(released,true);
});

// 验证异常分页终止，不能只依赖第一页移除持久选择或无限请求。
test('repeated or prematurely empty scope pages fail rather than loop', async function testPaginationGuard() {
  let calls = 0;
  // 反复返回同一页。url：请求地址；返回 Promise<Response>。
  async function fetch(url) { calls += 1; return response({items:[knowledgeBase('same','重复')],total:3}); }
  const {context} = environment(fetch); await assert.rejects(context.loadAllKnowledgeBases(),/重复/); assert.equal(calls,2);
});

// 构造文档 DTO。id：文档 ID；knowledgeBaseId：所属库；status：状态；返回文档摘要。
function storedDocument(id, knowledgeBaseId = 'a', status = 'ready') {
  return {document_id:id,knowledge_base_id:knowledgeBaseId,source:`${id}.md`,status,chunk_count:4,file_available:true};
}

// 构造真实分块页形状。doc：文档；indices：原始分块索引；offset/nextOffset：后端游标；返回带向量预览和 payload 的页。
function storedChunkPage(doc, indices, offset = 0, nextOffset = null) {
  const items = indices.map(makeChunk);
  // 创建具有倒序 UUID 与递增索引的分块。index：原始索引；返回 DTO，验证前端无需按 Point ID 排序。
  function makeChunk(index) {
    const text = `分块 ${index} 的完整文本\n第二行内容`;
    return {point_id:`${String(1000 - index).padStart(8,'0')}-0000-4000-8000-000000000001`,chunk_index:index,text,character_count:text.length,
      indexing_state:'ready',vector_dimensions:1536,vector_preview:[.1,.2,.3,.4,.5,.6,.7,.8],
      payload:{knowledge_base_id:doc.knowledge_base_id,document_id:doc.document_id,chunk_index:index,text,indexing_state:'ready'}};
  }
  return {document:doc,knowledge_base:{id:doc.knowledge_base_id,name:'产品资料'},items,total:4,limit:12,offset,next_offset:nextOffset};
}

// 配置文档查看目标。context：VM；id：当前管理库 ID；无返回值，提供库状态供切换检查。
function setInspectionTarget(context, id = 'a') {
  vm.runInContext(`knowledgeBases = new Map([['a',{id:'a',name:'产品资料',status:'active'}],['b',{id:'b',name:'部署资料',status:'active'}]]); activeManagementKnowledgeBaseId = '${id}'`,context);
}

// 验证分页使用后端索引游标、保留空洞和真实顺序，且只加载所需页。
test('document inspector paginates by returned chunk-index cursor and preserves server order', async function testInspectionPagination() {
  const doc = storedDocument('manual'), requested = [];
  // 按不连续游标返回两页。url：分块路径；返回 Promise<Response>。
  async function fetch(url) {
    requested.push(url); const offset = Number(new URL(url,'http://localhost').searchParams.get('offset'));
    return response(storedChunkPage(doc,offset === 0 ? [0,5] : [20,40],offset,offset === 0 ? 20 : null));
  }
  const {context,elements} = environment(fetch); setInspectionTarget(context);
  await context.inspectDocument('a',doc);
  assert.equal(elements.get('document-inspector').hidden,false); assert.equal(elements.get('document-inspector').focused,true);
  assert.equal(requested.length,1); assert.match(requested[0],/limit=12&offset=0$/);
  assert.deepEqual(elements.get('inspect-directory').querySelectorAll('button').map(item => item.dataset.chunkIndex),['0','5']);
  context.selectInspectionChunk(5); assert.match(visibleText(elements.get('inspect-content')),/分块 #6/);
  await context.nextInspectionPage(); assert.match(requested[1],/offset=20$/);
  assert.deepEqual(elements.get('inspect-directory').querySelectorAll('button').map(item => item.dataset.chunkIndex),['20','40']);
  assert.equal(elements.get('inspect-next').disabled,true); assert.equal(elements.get('inspect-prev').disabled,false);
  await context.previousInspectionPage(); assert.match(requested[2],/offset=0$/); assert.equal(elements.get('inspect-prev').disabled,true);
  assert.equal(requested.length,3);
});

// 验证完整文本与实际 payload 仅作为纯文本展示，Tab 切换不丢失所选块或展开状态。
test('document inspector displays full plain text, storage metadata and keeps state across tabs', async function testInspectionPlainTextAndTabs() {
  const doc = storedDocument('unsafe'), page = storedChunkPage(doc,[0,7]);
  const unsafeText = '<img src=x onerror="globalThis.bad=true">\n<script>alert(1)</script> & 原文';
  page.items[1].text = unsafeText; page.items[1].payload.text = unsafeText; page.items[1].payload.extra = '<svg onload=bad()>';
  // 返回含 HTML 特殊字符的实际存储内容。url：分块接口；返回 Promise<Response>。
  async function fetch(url) { return response(page); }
  const {context,elements} = environment(fetch); setInspectionTarget(context); await context.inspectDocument('a',doc); context.selectInspectionChunk(7);
  const content = elements.get('inspect-content'), text = content.children.find(item => item.className === 'stored-text');
  assert.equal(text.textContent,unsafeText); assert.equal(text.children.length,0);
  assert.match(visibleText(content),/1536/); assert.match(visibleText(content),/00000993-0000-4000/);
  const details = content.querySelectorAll('details')[0]; details.open = true;
  const payload = details.querySelectorAll('pre')[1]; assert.deepEqual(JSON.parse(payload.textContent),page.items[1].payload);
  assert.equal(content.querySelectorAll('img').length,0); assert.equal(content.querySelectorAll('script').length,0);
  context.openChatPage(); context.openManagementPage();
  assert.equal(vm.runInContext('selectedInspectionIndex',context),7); assert.equal(details.open,true); assert.equal(text.textContent,unsafeText);
});

// 验证切文档后的旧成功及旧异常都不会污染当前内容或错误提示。
test('document inspector ignores stale success and failure after selecting another document', async function testInspectionStaleDocuments() {
  for (const fail of [false,true]) {
    const old = deferred(), first = storedDocument('first'), second = storedDocument('second');
    // 第一份文档延迟，第二份立即完成。url：分块路径；返回 Promise<Response>。
    async function fetch(url) { return url.includes('/first/') ? old.promise : response(storedChunkPage(second,[2])); }
    const {context,elements} = environment(fetch); setInspectionTarget(context);
    const pending = context.inspectDocument('a',first); await context.inspectDocument('a',second);
    if (fail) old.reject(new Error('旧文档错误')); else old.resolve(response(storedChunkPage(first,[0])));
    await pending;
    assert.match(elements.get('inspect-title').textContent,/second/); assert.doesNotMatch(visibleText(elements.get('inspect-content')),/分块 #1/);
    assert.doesNotMatch(elements.get('inspect-status').textContent,/旧文档错误/); assert.equal(elements.get('inspect-retry').hidden,true);
    assert.equal(vm.runInContext('inspectionLoading',context),false);
  }
});

// 验证切库或关闭详情会立即清除文本，即使旧请求随后才完成也不再出现。
test('document inspector clears and invalidates requests on library switch or close', async function testInspectionTargetInvalidation() {
  const old = deferred(), doc = storedDocument('manual');
  // 延迟返回分块页。url：接口路径；返回 Promise<Response>。
  async function fetch(url) { return old.promise; }
  const {context,elements} = environment(fetch); setInspectionTarget(context);
  const pending = context.inspectDocument('a',doc);
  vm.runInContext("activeManagementKnowledgeBaseId = 'b'",context); context.renderManagementTarget();
  assert.equal(elements.get('document-inspector').hidden,true);
  old.resolve(response(storedChunkPage(doc,[0]))); await pending;
  assert.equal(elements.get('inspect-content').children.length,0); assert.equal(vm.runInContext('inspectedDocument',context),null);
  setInspectionTarget(context); await context.inspectDocument('a',doc); context.closeDocumentInspection();
  assert.equal(elements.get('inspect-content').children.length,0); assert.equal(elements.get('inspect-title').textContent,'文档分块');
});

// 验证下一页失败后的重试仍使用失败游标，不退回前一页，标题和业务错误可读。
test('document inspector retries the failed next-page cursor while keeping document title', async function testInspectionRetryCursor() {
  const doc = storedDocument('manual'), requested = []; let attempt = 0;
  // 下一页第一次失败、第二次成功。url：分块路径；返回 Promise<Response>。
  async function fetch(url) {
    requested.push(url); const offset = Number(new URL(url,'http://localhost').searchParams.get('offset'));
    if (offset === 20 && ++attempt === 1) return response({detail:{code:'DATABASE_UNAVAILABLE',message:'数据库暂时不可用'}},503);
    return response(storedChunkPage(doc,offset === 0 ? [0,5] : [20,40],offset,offset === 0 ? 20 : null));
  }
  const {context,elements} = environment(fetch); setInspectionTarget(context); await context.inspectDocument('a',doc); await context.nextInspectionPage();
  assert.match(elements.get('inspect-title').textContent,/manual.md/); assert.match(elements.get('inspect-status').textContent,/数据库暂时不可用/);
  assert.equal(elements.get('inspect-retry').hidden,false); assert.equal(elements.get('inspect-content').children.length,0);
  await context.reloadDocumentInspection(); assert.match(requested[2],/offset=20$/); assert.equal(elements.get('inspect-retry').hidden,true);
  assert.match(elements.get('inspect-page').textContent,/第 2 页/); await context.previousInspectionPage(); assert.match(requested[3],/offset=0$/);
});

// 验证异常响应不能改变查看目标或让加载控件永久锁住。
test('document inspector rejects mismatched document responses and allows retry', async function testInspectionWrongResponse() {
  const doc = storedDocument('manual'); let calls = 0;
  // 先返回另一份文档，再返回正确目标。url：分块路径；返回 Promise<Response>。
  async function fetch(url) { calls += 1; return response(storedChunkPage(calls === 1 ? storedDocument('wrong','b') : doc,[0])); }
  const {context,elements} = environment(fetch); setInspectionTarget(context); await context.inspectDocument('a',doc);
  assert.match(elements.get('inspect-status').textContent,/当前文档不匹配/); assert.match(elements.get('inspect-title').textContent,/manual/);
  assert.equal(vm.runInContext('inspectionLoading',context),false); assert.equal(elements.get('inspect-retry').disabled,false);
  await context.reloadDocumentInspection(); assert.match(visibleText(elements.get('inspect-content')),/分块 #1/);
});

// 验证删除当前查看文档立即取消旧详情，删除完成后的迟到响应也不能复活内容。
test('deleting an inspected document invalidates its pending chunk request', async function testInspectionDeleteDocument() {
  const pending = deferred(), doc = storedDocument('manual'), libraries = [knowledgeBase('a','产品资料')]; let deleted = false;
  // 分块请求延迟，其余操作模拟真实删除后刷新。url：接口；options：请求配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (url.includes('/chunks?')) return pending.promise;
    if (options.method === 'DELETE') { deleted = true; return response(null,204); }
    if (url.includes('/documents?')) return response({items:deleted ? [] : [doc],total:deleted ? 0 : 1});
    return response({items:libraries,total:1,default_knowledge_base_id:'a'});
  }
  const {context,elements} = environment(fetch); await context.refreshKnowledgeBases(); await context.loadDocuments();
  const inspecting = context.inspectDocument('a',doc);
  const deleteButton = elements.get('documents').querySelectorAll('button').find(item => item.textContent === '删除');
  await deleteButton.onclick(); assert.equal(elements.get('document-inspector').hidden,true);
  pending.resolve(response(storedChunkPage(doc,[0]))); await inspecting;
  assert.equal(vm.runInContext('inspectedDocument',context),null); assert.equal(elements.get('inspect-content').children.length,0);
});

// 验证删除当前库会清除文档详情，在途响应不能在默认库管理页显示原库内容。
test('deleting an inspected library invalidates its pending chunk request', async function testInspectionDeleteLibrary() {
  const pending = deferred(), doc = storedDocument('manual'), libraries = [knowledgeBase('default','默认知识库',true),knowledgeBase('a','产品资料')]; let deleted = false;
  // 删除库后只返回默认库。url：接口；options：请求配置；返回 Promise<Response>。
  async function fetch(url,options = {}) {
    if (url.includes('/chunks?')) return pending.promise;
    if (options.method === 'DELETE') { deleted = true; return response(null,204); }
    if (url.includes('/documents?')) return response({items:[],total:0});
    const items = deleted ? [libraries[0]] : libraries; return response({items,total:items.length,default_knowledge_base_id:'default'});
  }
  const {context,elements} = environment(fetch); await context.refreshKnowledgeBases();
  vm.runInContext("activeManagementKnowledgeBaseId = 'a'",context); const inspecting = context.inspectDocument('a',doc);
  await context.deleteKnowledgeBase(); assert.equal(elements.get('document-inspector').hidden,true);
  pending.resolve(response(storedChunkPage(doc,[0]))); await inspecting;
  assert.equal(vm.runInContext('activeManagementKnowledgeBaseId',context),'default');
  assert.equal(vm.runInContext('inspectedDocument',context),null); assert.equal(elements.get('inspect-content').children.length,0);
});

// 验证未入库文档不请求分块，成功入库的空页展示明确状态而不伪造内容。
test('document inspector explains non-ready and empty documents', async function testInspectionEmptyStates() {
  let calls = 0; const doc = storedDocument('empty'); doc.chunk_count = 0;
  // 返回真实的空分块页。url：分块路径；返回 Promise<Response>。
  async function fetch(url) { calls += 1; const page = storedChunkPage(doc,[]); page.total = 0; return response(page); }
  const {context,elements} = environment(fetch); setInspectionTarget(context);
  await context.inspectDocument('a',storedDocument('pending','a','importing')); assert.equal(calls,0);
  assert.match(elements.get('inspect-status').textContent,/尚未成功入库/);
  await context.inspectDocument('a',doc); assert.equal(calls,1); assert.match(elements.get('inspect-status').textContent,/没有可展示/);
  assert.equal(elements.get('inspect-layout').hidden,true); assert.equal(elements.get('inspect-pager').hidden,true);
});
