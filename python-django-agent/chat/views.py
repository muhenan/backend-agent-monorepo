import json
import logging
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .services import run_agent

logger = logging.getLogger(__name__)


def index(request):
    return render(request, "chat/index.html", {"csrf_token": get_token(request)})


@require_GET
def conversation_messages(request, conversation_id: UUID):
    conversation = get_object_or_404(Conversation, pk=conversation_id)
    messages = [
        {"role": message.role, "content": message.content}
        for message in conversation.messages.all()
    ]
    return JsonResponse({"conversation_id": str(conversation.id), "messages": messages})


@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "请求内容不是有效的 JSON。"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求内容必须是 JSON 对象。"}, status=400)

    user_text = payload.get("message")
    if not isinstance(user_text, str) or not user_text.strip():
        return JsonResponse({"error": "请输入消息。"}, status=400)

    user_text = user_text.strip()
    if len(user_text) > 4000:
        return JsonResponse({"error": "消息不能超过 4000 个字符。"}, status=400)

    conversation_id = payload.get("conversation_id")
    if conversation_id:
        try:
            conversation_id = UUID(str(conversation_id))
        except (TypeError, ValueError):
            return JsonResponse({"error": "对话编号格式无效。"}, status=400)
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        previous_messages = list(conversation.messages.all())
    else:
        conversation = Conversation()
        previous_messages = []

    # Agents SDK accepts a list of role/content items for manual conversation history.
    input_items = [
        {"role": message.role, "content": message.content}
        for message in previous_messages
    ]
    input_items.append({"role": "user", "content": user_text})

    try:
        reply = run_agent(input_items)
    except Exception as exc:  # Return a useful development error without breaking the page.
        logger.exception("Agents SDK run failed")
        error = str(exc) if settings.DEBUG else "AI 助手暂时无法回答，请稍后重试。"
        return JsonResponse({"error": error}, status=502)

    with transaction.atomic():
        conversation.save()
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=user_text,
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=reply,
        )
        conversation.save(update_fields=["updated_at"])

    return JsonResponse(
        {
            "conversation_id": str(conversation.id),
            "user_message": user_text,
            "assistant_message": reply,
        }
    )
