const conversationStorageKey = "python-django-agent.conversation-id";
const transcript = document.querySelector("#transcript");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const newChatButton = document.querySelector("#new-chat");
const statusLabel = document.querySelector("#status");
const csrfToken = document.querySelector("meta[name='csrf-token']").content;
const attachmentInput = document.querySelector("#attachment-input");
const attachmentList = document.querySelector("#attachments");
let attachments = [];
let uploading = false;

function renderAttachments() {
  attachmentList.replaceChildren();
  for (const attachment of attachments) {
    const row = document.createElement("label");
    row.className = "attachment-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = attachment.selected;
    checkbox.disabled = uploading || sendButton.disabled;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && attachments.filter((a) => a.selected).length >= 4) {
        checkbox.checked = false;
        statusLabel.textContent = "每次提问最多选择 4 个附件。";
        return;
      }
      attachment.selected = checkbox.checked;
    });
    const link = document.createElement("a");
    link.href = attachment.download_url;
    link.textContent = attachment.name;
    link.title = "下载原文件";
    row.append(checkbox, link);
    attachmentList.append(row);
  }
}

attachmentInput.addEventListener("change", async () => {
  const file = attachmentInput.files[0];
  if (!file || uploading || sendButton.disabled) return;
  if (file.size > 10 * 1024 * 1024) {
    statusLabel.textContent = "文件不能超过 10 MB。";
    attachmentInput.value = "";
    return;
  }
  uploading = true;
  sendButton.disabled = true;
  newChatButton.disabled = true;
  attachmentInput.disabled = true;
  renderAttachments();
  statusLabel.textContent = "正在上传并解析…";
  const payload = new FormData();
  payload.append("file", file);
  const id = localStorage.getItem(conversationStorageKey);
  if (id) payload.append("conversation_id", id);
  try {
    const response = await fetch("/api/attachments/", {
      method: "POST", headers: { "X-CSRFToken": csrfToken }, body: payload,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "上传失败。");
    localStorage.setItem(conversationStorageKey, data.conversation_id);
    attachments.push({ ...data.attachment, selected: attachments.filter((a) => a.selected).length < 4 });
    statusLabel.textContent = "已保存，可以针对附件提问。";
  } catch (error) {
    statusLabel.textContent = error.message || "上传失败，请重试。";
  } finally {
    uploading = false;
    sendButton.disabled = false;
    newChatButton.disabled = false;
    attachmentInput.disabled = false;
    attachmentInput.value = "";
    renderAttachments();
  }
});
const starterPrompts = [
  "Django 的 Model、View 和 URL 分别负责什么？",
  "OpenAI Agents SDK 里的 Agent 和 Runner 是什么关系？",
  "请用简单例子解释 Django migration 是怎么工作的。",
];

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
}

function appendMessage(role, content, { pending = false } = {}) {
  document.querySelector("#empty-state")?.remove();

  const message = document.createElement("article");
  message.className = `message ${role}${pending ? " pending" : ""}`;

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "YOU" : "AI";

  const body = document.createElement("div");
  body.className = "message-body";
  const label = document.createElement("p");
  label.className = "message-label";
  label.textContent = role === "user" ? "你" : "学习助手";
  const text = document.createElement("p");
  text.className = "message-content";
  text.textContent = content;

  body.append(label, text);
  message.append(avatar, body);
  transcript.append(message);
  scrollToBottom();
  return message;
}

function createWelcomeCard() {
  const card = document.createElement("div");
  card.className = "welcome-card";
  card.id = "empty-state";

  const icon = document.createElement("div");
  icon.className = "welcome-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "✳";
  const heading = document.createElement("h2");
  heading.textContent = "你好，今天想学什么？";
  const copy = document.createElement("p");
  copy.textContent = "可以从 Django 的模型、视图和路由开始，也可以看看 Agent 与 Runner 如何配合。";
  const suggestions = document.createElement("div");
  suggestions.className = "suggestions";
  suggestions.setAttribute("aria-label", "示例问题");

  for (const prompt of starterPrompts) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.prompt = prompt;
    button.textContent = prompt;
    suggestions.append(button);
  }

  card.append(icon, heading, copy, suggestions);
  return card;
}

async function restoreConversation() {
  const conversationId = localStorage.getItem(conversationStorageKey);
  if (!conversationId) return;

  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/messages/`);
    if (!response.ok) throw new Error("找不到之前的对话");
    const data = await response.json();
    for (const message of data.messages) appendMessage(message.role, message.content);
    attachments = (data.attachments || []).map((a, index) => ({ ...a, selected: index < 4 }));
    renderAttachments();
  } catch (error) {
    localStorage.removeItem(conversationStorageKey);
    statusLabel.textContent = error.message;
  }
}

async function readEventStream(response, onEvent) {
  if (!response.body) throw new Error("浏览器不支持读取流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function dispatch(block) {
    if (!block.trim()) return;
    let eventName = "message";
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length) onEvent(eventName, JSON.parse(dataLines.join("\n")));
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) dispatch(block);
    if (done) {
      if (buffer.trim()) dispatch(buffer);
      return;
    }
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || sendButton.disabled) return;
  input.value = "";

  statusLabel.textContent = "";
  sendButton.disabled = true;
  newChatButton.disabled = true;
  attachmentInput.disabled = true;
  renderAttachments();
  const userBubble = appendMessage("user", message);
  const pendingMessage = appendMessage("assistant", "", { pending: true });
  const assistantText = pendingMessage.querySelector(".message-content");
  let completed = false;

  try {
    const response = await fetch("/api/chat/stream/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({
        message,
        conversation_id: localStorage.getItem(conversationStorageKey),
        attachment_ids: attachments.filter((a) => a.selected).map((a) => a.id),
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "请求失败，请重试。");
    }

    await readEventStream(response, (eventName, data) => {
      if (eventName === "delta") {
        assistantText.textContent += data.text;
        statusLabel.textContent = "正在生成…";
        scrollToBottom();
      } else if (eventName === "done") {
        completed = true;
        localStorage.setItem(conversationStorageKey, data.conversation_id);
        assistantText.textContent = data.assistant_message;
        pendingMessage.classList.remove("pending");
        statusLabel.textContent = "";
        input.focus();
      } else if (eventName === "error") {
        throw new Error(data.error || "AI 助手暂时无法回答，请稍后重试。");
      }
    });
    if (!completed) throw new Error("连接提前结束，请检查服务日志后重试。");
  } catch (error) {
    pendingMessage.remove();
    userBubble.remove();
    if (!input.value) input.value = message;
    statusLabel.textContent = error.message || "网络请求失败，请确认服务已经启动。";
  } finally {
    sendButton.disabled = false;
    newChatButton.disabled = false;
    attachmentInput.disabled = false;
    renderAttachments();
  }
}

form.addEventListener("submit", sendMessage);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

newChatButton.addEventListener("click", () => {
  localStorage.removeItem(conversationStorageKey);
  attachments = [];
  renderAttachments();
  transcript.replaceChildren(createWelcomeCard());
  statusLabel.textContent = "";
  input.focus();
});

transcript.addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (!button) return;
  input.value = button.dataset.prompt;
  form.requestSubmit();
});

sendButton.disabled = true;
newChatButton.disabled = true;
attachmentInput.disabled = true;
restoreConversation().finally(() => {
  sendButton.disabled = false;
  newChatButton.disabled = false;
  attachmentInput.disabled = false;
  renderAttachments();
});
