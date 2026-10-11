/**
 * 查找页面中第一个匹配的元素。
 * @param {string} selector CSS 选择器。
 * @returns {Element|null} 匹配的元素；不存在时返回 null。
 */
const $ = (selector) => document.querySelector(selector);
const USER_ID_KEY = 'agent-mem-user-id';
const state = { busy: false, history: [], userId: localStorage.getItem(USER_ID_KEY) || 'demo-user' };

/**
 * 转义动态文本，避免用户消息或记忆内容被当作 HTML 执行。
 * @param {*} value 要展示的值，会先转换为字符串。
 * @returns {string} 可安全插入 HTML 文本位置的字符串。
 */
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}
/**
 * 显示短暂的操作提示，并在 2.4 秒后隐藏。
 * @param {string} message 提示文字。
 */
function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  window.setTimeout(() => element.classList.remove('show'), 2400);
}
/**
 * 在聊天区追加消息、隐藏欢迎区，并滚动到新消息。
 * @param {string} role 消息角色，user 或 assistant。
 * @param {string} text 消息正文，插入页面前会转义。
 * @param {string} meta 可选的附加说明，默认不显示。
 * @returns {HTMLElement} 消息行，供后续更新流式正文和工具活动。
 */
function addMessage(role, text, meta = '') {
  $('#welcome').classList.add('hidden');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;
  row.innerHTML = `<div class="message-avatar">${role === 'user' ? 'U' : '✳'}</div><div class="message-body"><div class="message-content">${escapeHtml(text)}</div>${meta ? `<div class="message-meta">${escapeHtml(meta)}</div>` : ''}</div>`;
  $('#messages').append(row);
  $('#chat-area').scrollTop = $('#chat-area').scrollHeight;
  return row;
}
/**
 * 更新右侧长期记忆列表、数量和每条记录的删除入口。
 * @param {Array<{id: string, memory: string}>} memories 当前用户的记忆记录。
 */
function renderMemories(memories) {
  $('#memory-count').textContent = `${memories.length} 条记忆`;
  $('#panel-count').textContent = memories.length;
  const list = $('#memory-list');
  if (!memories.length) {
    list.innerHTML = '<div class="empty-memory"><span>✳</span><b>还没有记忆</b><small>聊过的偏好和背景<br />会整理在这里</small></div>';
    return;
  }
  list.innerHTML = memories.map((entry) => `<article class="memory-card"><div class="memory-card-top"><span class="memory-category">${guessCategory(entry.memory)}</span><button class="delete-memory" data-memory-id="${escapeHtml(entry.id)}" title="删除这条记忆" aria-label="删除这条记忆">×</button></div><p>${escapeHtml(entry.memory)}</p><small class="memory-id">Qdrant 记录 · ${escapeHtml(entry.id.slice(0, 8))}</small></article>`).join('');
  list.querySelectorAll('.delete-memory').forEach((button) => button.addEventListener('click', () => removeMemory(button.dataset.memoryId)));
}
/**
 * 根据记忆文本中的关键词生成用于 Demo 展示的分类标签。
 * @param {string} memory 记忆文本。
 * @returns {string} 偏好、工作、学习或个人信息；不影响实际记忆存储。
 */
function guessCategory(memory) {
  if (/喜欢|偏好|不喜欢|习惯/.test(memory)) return '偏好';
  if (/工作|工程师|职业|公司|项目/.test(memory)) return '工作';
  if (/学习|正在学|课程/.test(memory)) return '学习';
  return '个人信息';
}
/**
 * 请求并刷新当前身份的长期记忆，失败时显示提示。
 * 无参数；读取 state.userId，不修改当前会话历史。
 */
async function refreshMemories() {
  try {
    const response = await fetch(`/api/memories?user_id=${encodeURIComponent(state.userId)}`);
    if (!response.ok) throw new Error((await response.json()).detail || '读取记忆失败');
    renderMemories((await response.json()).memories);
  } catch (error) { toast(error.message); }
}
/**
 * 用户确认后删除单条记忆，并刷新记忆面板。
 * @param {string} memoryId 要删除的记录 ID；请求同时携带当前用户身份。
 */
async function removeMemory(memoryId) {
  if (!window.confirm('确定删除这条长期记忆吗？')) return;
  try {
    const response = await fetch(`/api/memories/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(state.userId)}`, { method: 'DELETE' });
    if (!response.ok) throw new Error((await response.json()).detail || '删除失败');
    await refreshMemories();
    toast('这条记忆已删除');
  } catch (error) { toast(error.message); }
}
/**
 * 将后端查询和写入结果整理成简短的活动摘要。
 * @param {Object} payload 包含 memory_searches、recalled_memories 和 memory_write 的结果。
 * @returns {string} 查询次数、结果数量及实际写入状态的文字说明。
 */
function describeActivity(payload) {
  const searches = payload.memory_searches || [];
  const searchLabel = !searches.length ? '搜索：未调用' :
    `搜索：调用 ${searches.length} 次${searches.some((entry) => entry.status === 'error') ? '（含失败）' : `，返回 ${(payload.recalled_memories || []).length} 条`}`;
  const events = payload.memory_write?.events || [];
  const count = (kind) => events.filter((entry) => entry.event === kind).length;
  const writeLabels = {
    saved: `写入：新增 ${count('ADD')} / 更新 ${count('UPDATE')} / 删除 ${count('DELETE')}`,
    no_change: '写入：已尝试，无变更',
    error: '写入：失败，回答已保留',
    unknown: '写入：返回状态无法确认',
  };
  return `${searchLabel} · ${writeLabels[payload.memory_write?.status] || '写入：未确认'}`;
}
/**
 * 生成可展开的记忆搜索和写入详情，所有动态文本均进行转义。
 * @param {Object} payload 查询记录和写入状态；可只包含其中一部分。
 * @returns {{searchHtml: string, writeHtml: string}} 两类活动的 HTML 片段。
 */
function activityHtml(payload) {
  const searches = payload.memory_searches || [];
  const searchHtml = searches.length ? searches.map((entry) =>
    `<div class="activity-detail"><p>搜索词：${escapeHtml(entry.query)}</p>${entry.status === 'running' ? '正在搜索…' : entry.status === 'error' ? '查询失败，本次未取得记忆' : entry.status === 'limit_reached' ? '达到本轮搜索上限，未执行查询' : entry.memories?.length ? entry.memories.map(escapeHtml).join('<br>') : '查询成功，没有匹配记忆'}</div>`
  ).join('') : '<div class="inspector-empty">Agent 未调用 search_memory</div>';
  const writeHtml = (payload.memory_write?.events || []).map((entry) =>
    `<div class="activity-detail"><b>${escapeHtml(({ ADD: '新增', UPDATE: '更新', DELETE: '删除' })[entry.event] || entry.event)}</b><p>${escapeHtml(entry.memory || '')}</p><small>记录 ID：${escapeHtml(entry.id || '')}</small></div>`
  ).join('') || `<div class="inspector-empty">${escapeHtml(describeActivity(payload).split(' · ').slice(1).join(' · '))}</div>`;
  return { searchHtml, writeHtml };
}

/**
 * 创建增量 SSE 解析器，处理跨网络分块的行、CRLF、多行 data 和心跳注释。
 * UTF-8 字节先由 readChatStream 解码，此处接收解码后的文本。
 * @param {function(Object): void} onEvent 每解析出一个完整 JSON 事件时调用的回调。
 * @returns {{push: Function}} 接收文本分块的解析器；不发送缺少空行分隔的残帧。
 */
function createSSEParser(onEvent) {
  let buffer = '';
  let data = [];
  let eventName = '';
  /**
   * 处理一条完整 SSE 行，遇到空行时合并 data 并分发事件。
   * @param {string} value 不含换行符的行内容；冒号开头的心跳注释会忽略。
   */
  function line(value) {
    if (value === '') {
      if (data.length) {
        const event = JSON.parse(data.join('\n'));
        if (!event.type && eventName) event.type = eventName;
        onEvent(event);
      }
      data = [];
      eventName = '';
      return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let valueText = colon < 0 ? '' : value.slice(colon + 1);
    if (valueText.startsWith(' ')) valueText = valueText.slice(1);
    if (field === 'data') data.push(valueText);
    if (field === 'event') eventName = valueText;
  }
  return {
    /**
     * 追加已解码的文本，并保留尚未完整接收的行。
     * @param {string} chunk 本次接收的文本分块。
     * @param {boolean} final 是否已读到流末尾，默认 false；用于处理末尾的 CR。
     */
    push(chunk, final = false) {
      buffer += chunk;
      let consumed = 0;
      for (let index = 0; index < buffer.length; index += 1) {
        const char = buffer[index];
        if (char !== '\n' && char !== '\r') continue;
        if (char === '\r' && index + 1 === buffer.length && !final) break;
        line(buffer.slice(consumed, index));
        if (char === '\r' && buffer[index + 1] === '\n') index += 1;
        consumed = index + 1;
      }
      buffer = buffer.slice(consumed);
      // A truncated frame is not a confirmed event. Only blank-line-delimited
      // events are dispatched, even when the HTTP stream closes.
    },
  };
}

/**
 * 用户靠近聊天底部时跟随新内容，避免打断向上查看历史的操作。
 * @param {boolean} force 是否强制滚到底部，发送新消息时使用，默认 false。
 */
function scrollChat(force = false) {
  const area = $('#chat-area');
  if (force || area.scrollHeight - area.scrollTop - area.clientHeight < 160) {
    area.scrollTop = area.scrollHeight;
  }
}

/**
 * 为本轮回复创建正文容器、工具活动区域和独立状态。
 * @param {string} message 本轮用户问题，完整回答到达后用于记录会话历史。
 * @returns {Object} 本轮状态，包含 DOM 引用、回答、查询、写入和完成标记。
 */
function createStreamingTurn(message) {
  const row = addMessage('assistant', '');
  const content = row.querySelector('.message-content');
  content.classList.add('stream-pending');
  const activity = document.createElement('div');
  activity.className = 'turn-activity';
  row.querySelector('.message-body').append(activity);
  const turn = { row, content, activity, message, answer: '', searches: [], write: null,
    phase: 'thinking', answerComplete: false, historyRecorded: false, done: false,
    interrupted: false, error: '' };
  renderTurnActivity(turn);
  return turn;
}

/**
 * 将记忆写入状态转换为小标题文字，仅根据实际变更统计保存数量。
 * @param {Object|null} write 写入状态和 ADD / UPDATE / DELETE 事件；null 表示未确认。
 * @returns {string} 保存进度、变更数量或失败说明。
 */
function memoryWriteLabel(write) {
  if (!write) return '未确认是否写入';
  if (write.status === 'running') return '正在检查并保存记忆';
  if (write.status === 'no_change') return '已尝试，无变更';
  if (write.status === 'error') return '失败，回答已保留';
  if (write.status === 'unknown') return '返回状态无法确认';
  if (write.status === 'saved') {
    const events = write.events || [];
    const count = (kind) => events.filter((entry) => entry.event === kind).length;
    return `新增 ${count('ADD')} / 更新 ${count('UPDATE')} / 删除 ${count('DELETE')}`;
  }
  return '未确认是否写入';
}

/**
 * 更新回复下方的工具调用与记忆保存活动，并保留用户已展开的详情。
 * @param {Object} turn 本轮状态，读取 searches、write 和完成/中断标记来渲染。
 */
function renderTurnActivity(turn) {
  const opened = new Set([...turn.activity.querySelectorAll('details[open]')].map((node) => node.dataset.activityId));
  const icon = (running, failed = false) => `<span class="activity-marker${running ? ' is-running' : failed ? ' is-error' : ''}" aria-hidden="true">${running ? '' : failed ? '!' : '✓'}</span>`;
  const searches = turn.searches.map((entry, index) => {
    const running = entry.status === 'running';
    const label = running ? '正在查找记忆' : entry.status === 'error' ? '查找失败' : entry.status === 'limit_reached' ? '达到搜索上限' : `完成 · 返回 ${(entry.memories || []).length} 条`;
    const html = activityHtml({ memory_searches: [entry] }).searchHtml;
    return `<details class="activity-item" data-activity-id="search-${index}"><summary>${icon(running, entry.status === 'error')}<span class="activity-name">search_memory</span><span class="activity-label">${label}</span></summary>${html}</details>`;
  }).join('');
  const noSearch = turn.answerComplete && !turn.searches.length ? '<div class="activity-status"><span class="activity-marker" aria-hidden="true">−</span><span>search_memory · 未调用</span></div>' : '';
  const working = !turn.answerComplete && !turn.interrupted && !turn.searches.some((entry) => entry.status === 'running');
  const status = working ? `<div class="activity-status" role="status">${icon(true)}<span>${turn.answer ? '正在生成回复' : '正在思考'}</span></div>` : '';
  const write = turn.write || (turn.interrupted ? { status: 'unconfirmed', events: [] } : null);
  const writeHtml = write ? `<details class="activity-item memory-write-activity" data-activity-id="write"><summary>${icon(write.status === 'running', write.status === 'error' || write.status === 'unconfirmed')}<span class="activity-name">Memory 保存</span><span class="activity-label">${memoryWriteLabel(write)}</span></summary><div class="activity-operation">应用在回答后尝试保存记忆 · mem0.add</div>${write.status === 'running' ? '<div class="activity-detail">正在提取本轮值得记住的信息…</div>' : activityHtml({ memory_write: write }).writeHtml}</details>` : '';
  const error = turn.interrupted ? `<div class="activity-interrupted" role="status">${escapeHtml(turn.error)}${turn.answerComplete ? ' · 完整回答已保留' : turn.answer ? ' · 已保留收到的部分回复' : ''}</div>` : '';
  turn.activity.innerHTML = `${searches}${noSearch}${status}${writeHtml}${error}`;
  turn.activity.querySelectorAll('details').forEach((node) => { node.open = opened.has(node.dataset.activityId); });
}

/**
 * 将本轮完整问答写入当前会话历史，每轮只记录一次，最多保留 20 条消息。
 * @param {Object} turn 已完成回答的本轮状态；部分流式回复不应传入。
 */
function recordCompletedAnswer(turn) {
  if (turn.historyRecorded) return;
  state.history.push({ role: 'user', content: turn.message }, { role: 'assistant', content: turn.answer.slice(0, 10000) });
  state.history = state.history.slice(-20);
  turn.historyRecorded = true;
}

/**
 * 按事件类型更新流式正文、工具状态和保存结果，完整回答到达后记录历史。
 * @param {Object} turn 当前回复的状态和 DOM 引用，处理过程中会被更新。
 * @param {Object} event 后端 SSE 事件，type 决定其余字段的含义。
 * @throws {Error} 收到后端 error 事件时抛出，由外层统一标记中断。
 */
function applyStreamEvent(turn, event) {
  if (turn.done || turn.interrupted) return;
  switch (event.type) {
    case 'status':
      turn.phase = event.phase;
      break;
    case 'text_delta':
      if (turn.answerComplete) return;
      turn.answer += event.delta || '';
      turn.content.textContent = turn.answer;
      turn.content.classList.remove('stream-pending');
      turn.content.classList.add('is-streaming');
      break;
    case 'tool_start':
      turn.searches.push({ id: event.id, name: event.name, query: event.query || '', status: 'running', memories: [] });
      break;
    case 'tool_end': {
      const index = turn.searches.findIndex((entry) => entry.id === event.id);
      const search = { ...event, query: event.query || turn.searches[index]?.query || '', memories: event.memories || [] };
      if (index >= 0) turn.searches[index] = search;
      else turn.searches.push(search);
      break;
    }
    case 'answer_done':
      turn.answer = typeof event.answer === 'string' ? event.answer : turn.answer;
      turn.answerComplete = true;
      turn.phase = 'answered';
      turn.content.textContent = turn.answer;
      turn.content.classList.remove('stream-pending', 'is-streaming');
      if (!turn.searches.length && Array.isArray(event.memory_searches)) turn.searches = event.memory_searches;
      recordCompletedAnswer(turn);
      break;
    case 'memory_write_start':
      turn.write = { status: 'running', events: [] };
      break;
    case 'memory_write_done':
      turn.write = event.memory_write;
      break;
    case 'done':
      turn.answer = typeof event.answer === 'string' ? event.answer : turn.answer;
      turn.answerComplete = true;
      turn.done = true;
      turn.content.textContent = turn.answer;
      turn.content.classList.remove('stream-pending', 'is-streaming');
      // Keep live attempt IDs/statuses, including calls rejected by the limit.
      if (!turn.searches.length && Array.isArray(event.memory_searches)) turn.searches = event.memory_searches;
      if (event.memory_write) turn.write = event.memory_write;
      recordCompletedAnswer(turn);
      break;
    case 'error':
      throw new Error(event.message || '本轮输出中断');
    default:
      return;
  }
  if (event.type !== 'text_delta' || turn.answer === event.delta) renderTurnActivity(turn);
  scrollChat();
}

/**
 * 标记本轮输出中断，保留已收到的正文，并明确尚未确认的保存结果。
 * @param {Object} turn 要停止更新的本轮状态。
 * @param {string} message 中断原因，用于回复下方的提示。
 */
function interruptTurn(turn, message) {
  turn.interrupted = true;
  turn.error = `输出中断：${message}`;
  turn.content.classList.remove('stream-pending', 'is-streaming');
  if (!turn.answer) turn.content.textContent = '暂时无法完成回复，请稍后重试。';
  turn.searches = turn.searches.map((entry) => entry.status === 'running' ? { ...entry, status: 'error' } : entry);
  if (!turn.write || turn.write.status === 'running') turn.write = { status: 'unconfirmed', events: [] };
  renderTurnActivity(turn);
}

/**
 * 逐块读取 HTTP 响应，增量解码 UTF-8，并把完整 SSE 事件交给状态处理器。
 * @param {Response} response 已成功建立的流式 HTTP 响应。
 * @param {Object} turn 接收文字和活动更新的本轮状态。
 * @throws {Error} 没有响应流、解析失败或未收到 done 就断开时抛出。
 */
async function readChatStream(response, turn) {
  if (!response.body) throw new Error('浏览器未收到流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSSEParser((event) => applyStreamEvent(turn, event));
  try {
    while (!turn.done) {
      const { done, value } = await reader.read();
      if (done) {
        parser.push(decoder.decode(), true);
        break;
      }
      parser.push(decoder.decode(value, { stream: true }));
    }
    if (!turn.done) throw new Error('连接已断开，本轮结果未完整确认');
  } finally {
    try { await reader.cancel(); } catch (_) { /* Keep the original stream failure. */ }
    reader.releaseLock();
  }
}

/**
 * 发起一轮流式聊天，管理输入状态、回复显示和完成后的记忆面板刷新。
 * @param {string} text 用户输入或建议卡片中的问题；去掉首尾空白后发送。
 * 正在处理另一轮或输入为空时直接返回，不自动重试以免重复保存。
 */
async function sendMessage(text) {
  const message = text.trim();
  if (!message || state.busy) return;
  state.busy = true;
  $('#send-button').disabled = true;
  addMessage('user', message);
  $('#message-input').value = '';
  const history = state.history.slice(-20);
  const turn = createStreamingTurn(message);
  scrollChat(true);
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ user_id: state.userId, message, history }),
    });
    if (!response.ok) {
      let detail = '请求失败';
      try { detail = (await response.json()).detail || detail; } catch (_) { /* Non-JSON HTTP error. */ }
      throw new Error(detail);
    }
    await readChatStream(response, turn);
  } catch (error) {
    interruptTurn(turn, error.message);
  } finally {
    if (turn.answerComplete) await refreshMemories();
    state.busy = false;
    $('#send-button').disabled = false;
    $('#message-input').focus();
    scrollChat();
  }
}
$('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage($('#message-input').value); });
$('#message-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#chat-form').requestSubmit(); }
  event.target.style.height = 'auto';
  event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`;
});
document.querySelectorAll('.suggestion').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));
$('#new-chat').addEventListener('click', () => {
  if (state.busy) { toast('请等当前回复完成后再开始新对话'); return; }
  state.history = [];
  $('#messages').innerHTML = '';
  $('#welcome').classList.remove('hidden');
  $('#message-input').focus();
});
$('#refresh-memories').addEventListener('click', refreshMemories);
/**
 * 切换记忆身份并持久化到浏览器，清空会话历史后加载该身份的长期记忆。
 * 无参数；读取身份输入框，当前回复尚未完成时禁止切换。
 */
function applyUserId() {
  const userId = $('#user-id').value.trim();
  if (!userId) { toast('身份标识不能为空'); return; }
  if (state.busy) { toast('请等当前回复完成后再切换身份'); return; }
  state.history = [];
  state.userId = userId;
  localStorage.setItem(USER_ID_KEY, userId);
  $('#user-id').value = userId;
  $('#profile-user').textContent = userId;
  $('#messages').innerHTML = '';
  $('#welcome').classList.remove('hidden');
  renderMemories([]);
  refreshMemories();
  toast(`已切换到 ${userId}`);
}
$('#apply-user-id').addEventListener('click', applyUserId);
$('#user-id').addEventListener('keydown', (event) => { if (event.key === 'Enter') applyUserId(); });
$('#clear-memories').addEventListener('click', async () => {
  if (!window.confirm(`确定清空身份“${state.userId}”的全部长期记忆吗？此操作无法撤销。`)) return;
  try {
    const response = await fetch(`/api/memories?user_id=${encodeURIComponent(state.userId)}`, { method: 'DELETE' });
    if (!response.ok) throw new Error((await response.json()).detail || '删除失败');
    renderMemories([]); toast('长期记忆已清空');
  } catch (error) { toast(error.message); }
});
$('#user-id').value = state.userId;
$('#profile-user').textContent = state.userId;
refreshMemories();
