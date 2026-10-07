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
from django.http import FileResponse
from django.core.exceptions import RequestDataTooBig
from pathlib import Path

from .models import Attachment, Conversation, Message
from .attachments import attachment_json, build_input, ensure_bucket, storage_client, validate_file
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
    return JsonResponse({"conversation_id": str(conversation.id), "messages": messages,
                         "attachments": [attachment_json(a) for a in conversation.attachments.all()]})


def _selected_attachments(payload, conversation):
    ids = payload.get("attachment_ids", [])
    if not isinstance(ids, list) or len(ids) > settings.ATTACHMENT_MAX_COUNT:
        raise ValueError("每次提问最多选择 4 个附件。")
    try:
        ids = {UUID(str(value)) for value in ids}
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("附件编号格式无效。") from exc
    attachments = list(Attachment.objects.filter(conversation=conversation, id__in=ids)) if not conversation._state.adding else []
    if len(attachments) != len(ids):
        raise ValueError("附件不存在或不属于当前会话。")
    return attachments


@require_POST
def upload_attachment(request):
    try:
        upload = request.FILES.get("file")
        if upload is None:
            raise ValueError("请选择文件。")
        data, content_type, text = validate_file(upload)
        conversation_id = request.POST.get("conversation_id")
        if conversation_id:
            try:
                conversation_id = UUID(conversation_id)
            except ValueError as exc:
                raise ValueError("对话编号格式无效。") from exc
            conversation = get_object_or_404(Conversation, pk=conversation_id)
        else:
            conversation = Conversation()
    except (ValueError, RequestDataTooBig) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    attachment = Attachment(conversation=conversation, name=Path(upload.name).name[:255],
                            content_type=content_type, size=len(data), bucket=settings.S3_BUCKET,
                            extracted_text=text)
    attachment.object_key = f"conversations/{conversation.id}/{attachment.id}{Path(upload.name).suffix.lower()}"
    client = storage_client()
    try:
        ensure_bucket(client)
        client.put_object(Bucket=attachment.bucket, Key=attachment.object_key, Body=data, ContentType=content_type)
        try:
            with transaction.atomic():
                conversation.save()
                attachment.save()
        except Exception:
            client.delete_object(Bucket=attachment.bucket, Key=attachment.object_key)
            raise
    except Exception:
        logger.exception("Attachment upload failed")
        return JsonResponse({"error": "附件保存失败，请确认 MinIO 已启动。"}, status=502)
    return JsonResponse({"conversation_id": str(conversation.id), "attachment": attachment_json(attachment)}, status=201)


@require_GET
def download_attachment(request, attachment_id):
    attachment = get_object_or_404(Attachment, pk=attachment_id)
    try:
        response = storage_client().get_object(Bucket=attachment.bucket, Key=attachment.object_key)
    except Exception:
        logger.exception("Attachment download failed")
        return JsonResponse({"error": "文件暂时无法下载。"}, status=502)
    result = FileResponse(response["Body"], as_attachment=True, filename=attachment.name,
                          content_type=attachment.content_type)
    result["Content-Length"] = response["ContentLength"]
    result["X-Content-Type-Options"] = "nosniff"
    return result


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

    try:
        attachments = _selected_attachments(payload, conversation)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    try:
        input_items = build_input(previous_messages, user_text, attachments)
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

    try:
        attachments = _selected_attachments(payload, conversation)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    async def response_stream():
        yield ": connected\n\n"
        try:
            input_items = await sync_to_async(build_input, thread_sensitive=True)(previous_messages, user_text, attachments)
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
