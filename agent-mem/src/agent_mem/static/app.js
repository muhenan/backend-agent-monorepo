const $ = (selector) => document.querySelector(selector);
const USER_ID_KEY = 'agent-mem-user-id';
const state = { busy: false, userId: localStorage.getItem(USER_ID_KEY) || 'demo-user' };

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}
function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  window.setTimeout(() => element.classList.remove('show'), 2400);
}
function addMessage(role, text, meta = '') {
  $('#welcome').classList.add('hidden');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;
  row.innerHTML = `<div class="message-avatar">${role === 'user' ? 'U' : '✳'}</div><div><div class="message-content">${escapeHtml(text)}</div>${meta ? `<div class="message-meta">${escapeHtml(meta)}</div>` : ''}</div>`;
  $('#messages').append(row);
  $('#chat-area').scrollTop = $('#chat-area').scrollHeight;
  return row;
}
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
function guessCategory(memory) {
  if (/喜欢|偏好|不喜欢|习惯/.test(memory)) return '偏好';
  if (/工作|工程师|职业|公司|项目/.test(memory)) return '工作';
  if (/学习|正在学|课程/.test(memory)) return '学习';
  return '个人信息';
}
async function refreshMemories() {
  try {
    const response = await fetch(`/api/memories?user_id=${encodeURIComponent(state.userId)}`);
    if (!response.ok) throw new Error((await response.json()).detail || '读取记忆失败');
    renderMemories((await response.json()).memories);
  } catch (error) { toast(error.message); }
}
async function removeMemory(memoryId) {
  if (!window.confirm('确定删除这条长期记忆吗？')) return;
  try {
    const response = await fetch(`/api/memories/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(state.userId)}`, { method: 'DELETE' });
    if (!response.ok) throw new Error((await response.json()).detail || '删除失败');
    await refreshMemories();
    toast('这条记忆已删除');
  } catch (error) { toast(error.message); }
}
function renderActivity(target, memories, emptyText) {
  target.innerHTML = memories.length ? memories.map((memory) => `<div class="inspector-item">${escapeHtml(memory)}</div>`).join('') : `<div class="inspector-empty">${emptyText}</div>`;
}
async function sendMessage(text) {
  const message = text.trim();
  if (!message || state.busy) return;
  state.busy = true;
  $('#send-button').disabled = true;
  addMessage('user', message);
  $('#message-input').value = '';
  const pending = addMessage('assistant', '正在检索记忆并思考', 'Mem0 search → OpenAI Agent → Mem0 add');
  pending.querySelector('.message-content').classList.add('typing');
  try {
    const response = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: state.userId, message }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || '请求失败');
    pending.querySelector('.message-content').classList.remove('typing');
    pending.querySelector('.message-content').textContent = payload.answer;
    pending.querySelector('.message-meta').textContent = '已结合长期记忆生成回答';
    $('#memory-inspector').classList.remove('hidden');
    renderActivity($('#recalled-list'), payload.recalled_memories, '没有检索到相关记忆');
    renderActivity($('#saved-list'), payload.saved_memories, '本轮没有提取到新的记忆');
    await refreshMemories();
  } catch (error) {
    pending.querySelector('.message-content').classList.remove('typing');
    pending.querySelector('.message-content').textContent = `暂时无法完成请求：${error.message}`;
    pending.querySelector('.message-meta').textContent = '请检查 OpenAI API Key 和 Qdrant 状态';
  } finally {
    state.busy = false;
    $('#send-button').disabled = false;
    $('#message-input').focus();
    $('#chat-area').scrollTop = $('#chat-area').scrollHeight;
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
  $('#messages').innerHTML = '';
  $('#welcome').classList.remove('hidden');
  $('#memory-inspector').classList.add('hidden');
  $('#message-input').focus();
});
$('#refresh-memories').addEventListener('click', refreshMemories);
$('#close-inspector').addEventListener('click', () => $('#memory-inspector').classList.add('hidden'));
function applyUserId() {
  const userId = $('#user-id').value.trim();
  if (!userId) { toast('身份标识不能为空'); return; }
  if (state.busy) { toast('请等当前回复完成后再切换身份'); return; }
  state.userId = userId;
  localStorage.setItem(USER_ID_KEY, userId);
  $('#user-id').value = userId;
  $('#profile-user').textContent = userId;
  $('#messages').innerHTML = '';
  $('#welcome').classList.remove('hidden');
  $('#memory-inspector').classList.add('hidden');
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
