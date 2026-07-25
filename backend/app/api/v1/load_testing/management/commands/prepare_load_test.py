

import json
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.api.v1.load_testing.catalog import (
    build_synthetic_product,
    product_index_for_target,
)
from app.api.v1.monitoring.models import (
    MonitoringTarget,
    MonitoringTargetRole,
    MonitoringTargetStatus,
)
from app.api.v1.notifications.models import NotificationChannel


class Command(BaseCommand):
    help = "Prepare deterministic users and targets for the isolated Load Lab"

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=1000)
        parser.add_argument("--targets-per-user", type=int, default=5)
        parser.add_argument("--popular-products", type=int, default=100)
        parser.add_argument("--medium-products", type=int, default=500)
        parser.add_argument("--check-interval-minutes", type=int, default=15)
        parser.add_argument(
            "--output",
            default="/load-results/users.json",
            help="JSON file consumed by k6",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing loadtest_* users before seeding",
        )
        parser.add_argument(
            "--due-now",
            action="store_true",
            help="Make all generated targets immediately due for scanning",
        )
        parser.add_argument(
            "--without-channels",
            action="store_true",
            help="Do not create synthetic Telegram channels",
        )

    def handle(self, *args, **options):
        users_count = options["users"]
        targets_per_user = options["targets_per_user"]
        popular_products = options["popular_products"]
        medium_products = options["medium_products"]
        check_interval = options["check_interval_minutes"]

        if users_count < 1:
            raise CommandError("--users must be positive")
        if targets_per_user < 0:
            raise CommandError("--targets-per-user must be non-negative")
        if check_interval < 15:
            raise CommandError("--check-interval-minutes must be at least 15")

        user_model = get_user_model()

        if options["reset"]:
            deleted, _ = user_model.objects.filter(
                username__startswith="loadtest_",
            ).delete()
            self.stdout.write(f"Deleted existing load-test objects: {deleted}")

        existing = {
            user.username: user
            for user in user_model.objects.filter(
                username__startswith="loadtest_",
            )
        }
        missing_users = []
        for index in range(1, users_count + 1):
            username = f"loadtest_{index:06d}"
            if username in existing:
                continue
            user = user_model(username=username, is_active=True)
            user.set_unusable_password()
            missing_users.append(user)

        if missing_users:
            user_model.objects.bulk_create(missing_users, batch_size=1000)

        users = list(
            user_model.objects.filter(
                username__in=[
                    f"loadtest_{index:06d}"
                    for index in range(1, users_count + 1)
                ]
            ).order_by("username")
        )

        if len(users) != users_count:
            raise CommandError(
                f"Expected {users_count} users, found {len(users)}"
            )

        target_total = users_count * targets_per_user
        now = timezone.now()
        targets = []
        target_index = 0

        existing_pairs = set(
            MonitoringTarget.objects.filter(user__in=users).values_list(
                "user_id",
                "external_id",
            )
        )

        for user in users:
            for slot in range(targets_per_user):
                product_index = product_index_for_target(
                    target_index=target_index,
                    total_targets=target_total,
                    popular_products=popular_products,
                    medium_products=medium_products,
                )
                product = build_synthetic_product(product_index)
                target_index += 1

                pair = (user.pk, product.external_id)
                if pair in existing_pairs:
                    continue

                targets.append(
                    MonitoringTarget(
                        user=user,
                        marketplace=product.marketplace,
                        role=(
                            MonitoringTargetRole.OWN
                            if slot == 0
                            else MonitoringTargetRole.COMPETITOR
                        ),
                        status=MonitoringTargetStatus.ACTIVE,
                        url=product.url,
                        external_id=product.external_id,
                        title=f"Load Test Product {product_index}",
                        seller_name="Load Lab",
                        brand="Synthetic",
                        check_interval_minutes=check_interval,
                        next_check_at=(
                            now
                            if options["due_now"]
                            else now + timedelta(minutes=check_interval)
                        ),
                        is_active=True,
                    )
                )

        if targets:
            MonitoringTarget.objects.bulk_create(targets, batch_size=1000)

        if not options["without_channels"]:
            existing_channel_users = set(
                NotificationChannel.objects.filter(
                    user__in=users,
                    type=NotificationChannel.ChannelType.TELEGRAM,
                ).values_list("user_id", flat=True)
            )
            channels = [
                NotificationChannel(
                    user=user,
                    type=NotificationChannel.ChannelType.TELEGRAM,
                    telegram_chat_id=f"load-{user.pk}",
                    is_active=True,
                )
                for user in users
                if user.pk not in existing_channel_users
            ]
            if channels:
                NotificationChannel.objects.bulk_create(
                    channels,
                    batch_size=1000,
                )

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": timezone.now().isoformat(),
            "users_count": len(users),
            "targets_per_user": targets_per_user,
            "users": [
                {
                    "id": user.pk,
                    "username": user.username,
                }
                for user in users
            ],
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        actual_targets = MonitoringTarget.objects.filter(user__in=users).count()
        self.stdout.write(
            self.style.SUCCESS(
                "Load Lab dataset ready: "
                f"users={len(users)}, targets={actual_targets}, "
                f"catalog={output_path}"
            )
        )
