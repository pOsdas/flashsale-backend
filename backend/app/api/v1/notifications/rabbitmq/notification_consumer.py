import json
import signal
import time
from typing import Any

import pika
from django.conf import settings

from app.api.v1.common.outbox_handlers import get_outbox_handlers
from app.api.v1.notifications.notification_metrics import (
    NOTIFICATION_CONSUMER_HEARTBEAT_TIMESTAMP_SECONDS,
    NOTIFICATION_CONSUMER_LAST_FAILED_TIMESTAMP_SECONDS,
    NOTIFICATION_CONSUMER_LAST_PROCESSED_TIMESTAMP_SECONDS,
    NOTIFICATION_CONSUMER_MESSAGES_IN_PROGRESS,
    NOTIFICATION_CONSUMER_RUNNING,
    NOTIFICATION_MESSAGE_PROCESSING_DURATION_SECONDS,
    NOTIFICATION_MESSAGES_TOTAL,
    refresh_notification_delivery_state_metrics,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


class NotificationRabbitMQConsumer:
    HEARTBEAT_INTERVAL_SECONDS = 15
    DELIVERY_METRICS_REFRESH_INTERVAL_SECONDS = 60

    def __init__(self) -> None:
        self.rabbitmq_url = settings.RABBITMQ_URL
        self.exchange_name = settings.RABBITMQ_EXCHANGE
        self.queue_name = getattr(
            settings,
            "NOTIFICATION_RABBITMQ_QUEUE",
            "flashsale.notifications",
        )
        self.dead_letter_exchange_name = getattr(
            settings,
            "NOTIFICATION_RABBITMQ_DLX",
            "flashsale.notifications.dlx",
        )
        self.dead_letter_queue_name = getattr(
            settings,
            "NOTIFICATION_RABBITMQ_DLQ",
            "flashsale.notifications.dlq",
        )
        self.routing_keys = getattr(
            settings,
            "NOTIFICATION_RABBITMQ_ROUTING_KEYS",
            ["alert.created"],
        )
        self.prefetch_count = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_PREFETCH_COUNT",
                10,
            )
        )

        self.queue_max_length = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_QUEUE_MAX_LENGTH",
                50_000,
            )
        )
        self.queue_max_length_bytes = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_QUEUE_MAX_LENGTH_BYTES",
                256 * 1024 * 1024,
            )
        )
        self.message_ttl_ms = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_MESSAGE_TTL_MS",
                7 * 24 * 60 * 60 * 1000,
            )
        )

        self.dlq_max_length = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_DLQ_MAX_LENGTH",
                10_000,
            )
        )
        self.dlq_max_length_bytes = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_DLQ_MAX_LENGTH_BYTES",
                128 * 1024 * 1024,
            )
        )
        self.dlq_message_ttl_ms = int(
            getattr(
                settings,
                "NOTIFICATION_RABBITMQ_DLQ_MESSAGE_TTL_MS",
                14 * 24 * 60 * 60 * 1000,
            )
        )

        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.adapters.blocking_connection.BlockingChannel | None = None
        self.should_stop = False

    def start(self) -> None:
        self._register_signal_handlers()

        try:
            self._connect()
            self._declare_topology()

            assert self.channel is not None

            self.channel.basic_qos(
                prefetch_count=self.prefetch_count,
            )
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self._on_message,
                auto_ack=False,
            )

            NOTIFICATION_CONSUMER_RUNNING.set(1)

            self._schedule_heartbeat()
            self._schedule_delivery_metrics_refresh()

            logger.info(
                "Notification RabbitMQ consumer started",
                extra={
                    "service": "notification_consumer",
                    "queue": self.queue_name,
                    "exchange": self.exchange_name,
                    "routing_keys": self.routing_keys,
                    "prefetch_count": self.prefetch_count,
                },
            )

            self.channel.start_consuming()

        finally:
            NOTIFICATION_CONSUMER_RUNNING.set(0)
            self.close()

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop_signal)
        signal.signal(signal.SIGTERM, self._handle_stop_signal)

    def _handle_stop_signal(self, signum, frame) -> None:
        self.should_stop = True

        logger.info(
            "Notification RabbitMQ consumer stopping",
            extra={
                "service": "notification_consumer",
                "signal": signum,
            },
        )

        if self.channel and self.channel.is_open:
            self.channel.stop_consuming()

    def _schedule_heartbeat(self) -> None:
        if (
                self.should_stop
                or self.connection is None
                or self.connection.is_closed
        ):
            return

        NOTIFICATION_CONSUMER_HEARTBEAT_TIMESTAMP_SECONDS.set(
            time.time()
        )

        self.connection.call_later(
            self.HEARTBEAT_INTERVAL_SECONDS,
            self._schedule_heartbeat,
        )

    def _schedule_delivery_metrics_refresh(self) -> None:
        if (
                self.should_stop
                or self.connection is None
                or self.connection.is_closed
        ):
            return

        refresh_notification_delivery_state_metrics()

        self.connection.call_later(
            self.DELIVERY_METRICS_REFRESH_INTERVAL_SECONDS,
            self._schedule_delivery_metrics_refresh,
        )

    def _connect(self) -> None:
        parameters = pika.URLParameters(self.rabbitmq_url)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

    def _declare_topology(self) -> None:
        assert self.channel is not None

        self.channel.exchange_declare(
            exchange=self.exchange_name,
            exchange_type="topic",
            durable=True,
        )

        self.channel.exchange_declare(
            exchange=self.dead_letter_exchange_name,
            exchange_type="topic",
            durable=True,
        )

        self.channel.queue_declare(
            queue=self.dead_letter_queue_name,
            durable=True,
            arguments={
                "x-max-length": self.dlq_max_length,
                "x-max-length-bytes": self.dlq_max_length_bytes,
                "x-message-ttl": self.dlq_message_ttl_ms,
                "x-overflow": "drop-head",
            },
        )

        self.channel.queue_bind(
            queue=self.dead_letter_queue_name,
            exchange=self.dead_letter_exchange_name,
            routing_key="#",
        )

        self.channel.queue_declare(
            queue=self.queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self.dead_letter_exchange_name,
                "x-max-length": self.queue_max_length,
                "x-max-length-bytes": self.queue_max_length_bytes,
                "x-message-ttl": self.message_ttl_ms,
                "x-overflow": "reject-publish",
            },
        )

        for routing_key in self.routing_keys:
            self.channel.queue_bind(
                queue=self.queue_name,
                exchange=self.exchange_name,
                routing_key=routing_key,
            )

    def _on_message(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        started_at = time.monotonic()
        topic = method.routing_key or "unknown"

        NOTIFICATION_CONSUMER_MESSAGES_IN_PROGRESS.inc()

        try:
            message = self._decode_message(body=body)
            event_topic = message["topic"]
            event_payload = message["payload"]
            event_id = message.get("event_id", "")

            self._handle_event(
                event_id=event_id,
                topic=event_topic,
                payload=event_payload,
            )

        except Exception as exc:
            NOTIFICATION_MESSAGES_TOTAL.labels(
                topic=topic,
                status="failed",
            ).inc()

            NOTIFICATION_CONSUMER_LAST_FAILED_TIMESTAMP_SECONDS.set(
                time.time()
            )

            logger.exception(
                "Notification RabbitMQ message processing failed",
                extra={
                    "service": "notification_consumer",
                    "routing_key": topic,
                    "message_id": getattr(properties, "message_id", ""),
                    "error": str(exc),
                },
            )

            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=False,
            )
            return

        finally:
            duration = time.monotonic() - started_at

            NOTIFICATION_MESSAGE_PROCESSING_DURATION_SECONDS.labels(
                topic=topic,
            ).observe(duration)

            NOTIFICATION_CONSUMER_MESSAGES_IN_PROGRESS.dec()

        NOTIFICATION_MESSAGES_TOTAL.labels(
            topic=topic,
            status="processed",
        ).inc()

        NOTIFICATION_CONSUMER_LAST_PROCESSED_TIMESTAMP_SECONDS.set(
            time.time()
        )

        channel.basic_ack(
            delivery_tag=method.delivery_tag,
        )

    def _decode_message(self, body: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("RabbitMQ message body is not valid JSON") from exc

        if not isinstance(decoded, dict):
            raise ValueError("RabbitMQ message body must be JSON object")

        if "topic" not in decoded:
            raise ValueError("RabbitMQ message does not contain topic")

        if "payload" not in decoded:
            raise ValueError("RabbitMQ message does not contain payload")

        if not isinstance(decoded["payload"], dict):
            raise ValueError("RabbitMQ message payload must be JSON object")

        return decoded

    def _handle_event(
        self,
        event_id: str,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        handlers = get_outbox_handlers()
        handler = handlers.get(topic)

        if handler is None:
            raise RuntimeError(f"No handler registered for topic: {topic}")

        logger.info(
            "Notification RabbitMQ event handling started",
            extra={
                "service": "notification_consumer",
                "event_id": event_id,
                "topic": topic,
                "payload": payload,
            },
        )

        handler(payload)

        logger.info(
            "Notification RabbitMQ event handled",
            extra={
                "service": "notification_consumer",
                "event_id": event_id,
                "topic": topic,
            },
        )

    def close(self) -> None:
        if self.channel and self.channel.is_open:
            self.channel.close()

        if self.connection and self.connection.is_open:
            self.connection.close()

        logger.info(
            "Notification RabbitMQ consumer closed",
            extra={
                "service": "notification_consumer",
            },
        )
