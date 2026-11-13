from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from chat import services
from chat.models import Room

DEMO_PASSWORD = "pyslack-demo-2026"  # 開発用のデモアカウント専用（docs/08-verification.md に記載）

DEMO_USERS = [
    ("alice@example.com", "alice", "Alice", "☕", "コーヒー休憩中"),
    ("bob@example.com", "bob", "Bob", "💻", "開発中"),
    ("carol@example.com", "carol", "Carol", "", ""),
]


class Command(BaseCommand):
    help = "動作確認用のデモユーザー 3 人と #general チャンネル・DM を作る（何度実行しても同じ状態になる）"

    @transaction.atomic
    def handle(self, *args, **options):
        users = []
        for email, username, display, emoji, text in DEMO_USERS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"username": username, "display_name": display, "status_emoji": emoji, "status_text": text},
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            users.append(user)
        alice, bob, carol = users

        general = Room.objects.filter(type=Room.Type.CHANNEL, name__iexact="general").first()
        if general is None:
            general = services.create_room(
                alice, type=Room.Type.CHANNEL, name="general", is_private=False, member_ids=[bob.id, carol.id]
            )
        services.get_or_create_dm(alice, bob)

        self.stdout.write(self.style.SUCCESS("demo users: " + ", ".join(u.email for u in users)))
        self.stdout.write(f"password: {DEMO_PASSWORD}")
