

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.api.v1.monitoring.models import (
    MonitoringTarget,
    MonitoringTargetRole,
    MonitoringTargetStatus,
)
from app.api.v1.notifications.models import NotificationChannel


class Command(BaseCommand):
    help = "Seed low-volume users and validated real marketplace URLs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--catalog",
            default="/load-data/integration-products.json",
        )
        parser.add_argument("--users", type=int, default=25)
        parser.add_argument("--targets-per-user", type=int, default=2)
        parser.add_argument("--telegram-chat-id", default="")
        parser.add_argument(
            "--output",
            default="/load-results/users.json",
        )
        parser.add_argument("--reset", action="store_true")

    def handle(self, *args, **options):
        catalog_path = Path(options["catalog"])
        if not catalog_path.exists():
            raise CommandError(
                f"Catalog does not exist: {catalog_path}. "
                "Run /app/loadcatalog first."
            )
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        products = payload.get("products", payload)
        if not products:
            raise CommandError("Integration product catalog is empty")

        users_count = options["users"]
        targets_per_user = options["targets_per_user"]
        if users_count < 1 or targets_per_user < 0:
            raise CommandError("Users must be positive and targets non-negative")

        user_model = get_user_model()
        if options["reset"]:
            user_model.objects.filter(username__startswith="loadtest_").delete()

        for index in range(1, users_count + 1):
            username = f"loadtest_{index:06d}"
            user, created = user_model.objects.get_or_create(
                username=username,
                defaults={"is_active": True},
            )
            update_fields = []
            if created:
                user.set_unusable_password()
                update_fields.append("password")
            if not user.is_active:
                user.is_active = True
                update_fields.append("is_active")
            if update_fields:
                user.save(update_fields=update_fields)

        users = list(
            user_model.objects.filter(
                username__in=[
                    f"loadtest_{index:06d}"
                    for index in range(1, users_count + 1)
                ]
            ).order_by("username")
        )

        existing = set(
            MonitoringTarget.objects.filter(user__in=users).values_list(
                "user_id", "marketplace", "external_id"
            )
        )
        targets = []
        for user_index, user in enumerate(users):
            for slot in range(targets_per_user):
                product = products[
                    (user_index * targets_per_user + slot) % len(products)
                ]
                marketplace = str(product["marketplace"])
                external_id = str(product.get("external_id") or "")
                key = (user.pk, marketplace, external_id)
                if key in existing:
                    continue
                targets.append(
                    MonitoringTarget(
                        user=user,
                        marketplace=marketplace,
                        role=(
                            MonitoringTargetRole.OWN
                            if slot == 0
                            else MonitoringTargetRole.COMPETITOR
                        ),
                        status=MonitoringTargetStatus.ACTIVE,
                        url=str(product["url"]),
                        external_id=external_id,
                        title=str(product.get("title") or "Integration product"),
                        check_interval_minutes=60,
                        next_check_at=timezone.now(),
                        is_active=True,
                    )
                )
        if targets:
            MonitoringTarget.objects.bulk_create(targets, batch_size=500)

        chat_id = options["telegram_chat_id"].strip()
        if chat_id:
            existing_channel_users = set(
                NotificationChannel.objects.filter(
                    user__in=users,
                    type=NotificationChannel.ChannelType.TELEGRAM,
                    telegram_chat_id=chat_id,
                ).values_list("user_id", flat=True)
            )
            channels = [
                NotificationChannel(
                    user=user,
                    type=NotificationChannel.ChannelType.TELEGRAM,
                    telegram_chat_id=chat_id,
                    is_active=True,
                )
                for user in users
                if user.pk not in existing_channel_users
            ]
            if channels:
                NotificationChannel.objects.bulk_create(channels, batch_size=500)

        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "generated_at": timezone.now().isoformat(),
                    "users_count": len(users),
                    "users": [
                        {"id": user.pk, "username": user.username}
                        for user in users
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Integration dataset ready: users={len(users)}, "
                f"targets={MonitoringTarget.objects.filter(user__in=users).count()}, "
                f"telegram={'enabled' if chat_id else 'disabled'}"
            )
        )
