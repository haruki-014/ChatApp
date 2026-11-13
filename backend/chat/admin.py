from django.contrib import admin

from .models import Attachment, Message, Room, RoomMember


class RoomMemberInline(admin.TabularInline):
    model = RoomMember
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "name", "is_private", "last_message_at", "created_at")
    list_filter = ("type", "is_private")
    search_fields = ("name",)
    inlines = [RoomMemberInline]
    raw_id_fields = ("created_by",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "sender", "short_body", "created_at", "edited_at", "deleted_at")
    list_filter = ("room__type",)
    raw_id_fields = ("room", "sender")

    @admin.display(description="body")
    def short_body(self, obj):
        return obj.body[:40]


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "content_type", "size", "room", "message", "created_at")
    raw_id_fields = ("room", "message", "uploaded_by")
