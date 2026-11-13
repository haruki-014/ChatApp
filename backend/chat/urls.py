from django.urls import path

from . import views

urlpatterns = [
    path("rooms", views.RoomListCreateView.as_view()),
    path("rooms/dm", views.DMCreateView.as_view()),
    path("channels", views.ChannelBrowseView.as_view()),
    path("rooms/<int:room_id>", views.RoomDetailView.as_view()),
    path("rooms/<int:room_id>/join", views.RoomJoinView.as_view()),
    path("rooms/<int:room_id>/leave", views.RoomLeaveView.as_view()),
    path("rooms/<int:room_id>/members", views.RoomMembersView.as_view()),
    path("rooms/<int:room_id>/members/<int:user_id>", views.RoomMemberDeleteView.as_view()),
    path("rooms/<int:room_id>/membership", views.MembershipView.as_view()),
    path("rooms/<int:room_id>/read", views.ReadView.as_view()),
    path("rooms/<int:room_id>/messages", views.MessageListCreateView.as_view()),
    path("rooms/<int:room_id>/attachments/presign", views.AttachmentPresignView.as_view()),
    path("messages/<int:message_id>", views.MessageDetailView.as_view()),
    path("attachments/<int:attachment_id>/download", views.AttachmentDownloadView.as_view()),
    path("sync", views.SyncView.as_view()),
]
