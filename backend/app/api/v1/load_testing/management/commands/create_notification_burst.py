

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.api.v1.monitoring.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    ProductSnapshot,
    SnapshotParseStatus,
    SnapshotSource,
)
from app.api.v1.orders.models import OutboxEvent


class Command(BaseCommand):
    help = "Create a real alert/outbox burst for notification throughput tests"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=1000)

    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be positive")

        user_model = get_user_model()
        users = list(
            user_model.objects.filter(
                username__startswith="loadtest_",
                monitoring_targets__isnull=False,
            ).distinct().prefetch_related("monitoring_targets")
        )
        if not users:
            raise CommandError("Run prepare_load_test first")

        created = 0
        for index in range(count):
            user = users[index % len(users)]
            target = user.monitoring_targets.all()[0]
            with transaction.atomic():
                snapshot = ProductSnapshot.objects.create(
                    target=target,
                    parse_status=SnapshotParseStatus.SUCCESS,
                    source=SnapshotSource.PARSER,
                    price="999.00",
                    old_price="1199.00",
                    currency="RUB",
                    is_available=True,
                    title=target.title,
                    seller_name=target.seller_name,
                    brand=target.brand,
                    raw_data={"load_test": True, "burst_index": index},
                    checked_at=timezone.now(),
                )
                alert = Alert.objects.create(
                    user=user,
                    target=target,
                    snapshot=snapshot,
                    alert_type=AlertType.PRICE_DROPPED,
                    severity=AlertSeverity.HIGH,
                    status=AlertStatus.NEW,
                    title="Load test price drop",
                    message=f"Synthetic notification burst item {index}",
                    old_value="1199.00",
                    new_value="999.00",
                    dedup_key=f"load-burst:{timezone.now().timestamp()}:{index}",
                )
                OutboxEvent.objects.create(
                    topic="alert.created",
                    payload={
                        "alert_id": str(alert.id),
                        "user_id": str(user.id),
                        "target_id": str(target.id),
                        "snapshot_id": str(snapshot.id),
                        "alert_type": alert.alert_type,
                        "severity": alert.severity,
                    },
                )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Created notification burst: {created} alerts/outbox events"
            )
        )
