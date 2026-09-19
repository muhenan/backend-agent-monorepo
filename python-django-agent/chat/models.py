import uuid

from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "对话"
        verbose_name_plural = "对话"

    def __str__(self) -> str:
        return f"对话 {self.id}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "用户"
        ASSISTANT = "assistant", "助手"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "消息"
        verbose_name_plural = "消息"

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.content[:40]}"

