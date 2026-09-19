import json
import logging
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .services import run_agent, stream_agent

logger = logging.getLogger(__name__)


def _persist_exchange(conversation: Conversation, user_text: str, reply: str) -> None:
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


def _sse_event(event_name: str, payload: dict[str, str]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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


@require_POST
def chat_stream_api(request):
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

    input_items = [
        {"role": message.role, "content": message.content}
        for message in previous_messages
    ]
    input_items.append({"role": "user", "content": user_text})

    async def response_stream():
        yield ": connected\n\n"
        try:
            async for event in stream_agent(input_items):
                if event["type"] == "delta":
                    yield _sse_event("delta", {"text": event["text"]})
                    continue

                reply = event["text"]
                await sync_to_async(_persist_exchange, thread_sensitive=True)(
                    conversation, user_text, reply
                )
                yield _sse_event(
                    "done",
                    {
                        "conversation_id": str(conversation.id),
                        "user_message": user_text,
                        "assistant_message": reply,
                    },
                )
        except Exception as exc:
            logger.exception("Streaming Agents SDK run failed")
            error = str(exc) if settings.DEBUG else "AI 助手暂时无法回答，请稍后重试。"
            yield _sse_event("error", {"error": error})

    response = StreamingHttpResponse(
        response_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response
