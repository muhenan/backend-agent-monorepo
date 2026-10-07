from django.contrib import admin

from .models import Attachment, Conversation, Message


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ["name", "conversation", "content_type", "size", "created_at"]
    readonly_fields = ["id", "bucket", "object_key", "extracted_text", "created_at"]


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["role", "content", "created_at"]
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "created_at", "updated_at"]
    ordering = ["-updated_at"]
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "conversation", "role", "created_at"]
    list_filter = ["role", "created_at"]
    search_fields = ["content"]
