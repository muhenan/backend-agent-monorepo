from django.urls import path

from . import views

urlpatterns = [
    path("api/attachments/", views.upload_attachment, name="upload_attachment"),
    path("api/attachments/<uuid:attachment_id>/download/", views.download_attachment, name="download_attachment"),
    path("", views.index, name="home"),
    path("api/chat/", views.chat_api, name="chat_api"),
    path("api/chat/stream/", views.chat_stream_api, name="chat_stream_api"),
    path(
        "api/conversations/<uuid:conversation_id>/messages/",
        views.conversation_messages,
        name="conversation_messages",
    ),
]
