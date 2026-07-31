import json
import time
from dataclasses import dataclass, field
from typing import Any

import pika
from django.conf import settings
from pika.exceptions import AMQPConnectionError, AMQPError, ChannelClosedByBroker, StreamLostError

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RabbitMQPublisher:
    url: str
    exchange: str
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    _connection: pika.BlockingConnection | None = field(default=None, init=False)
    _channel: pika.adapters.blocking_connection.BlockingChannel | None = field(default=None, init=False)

    def connect(self) -> None:
        if self._connection and self._connection.is_open and self._channel and self._channel.is_open:
            return

        self.close(silent=True)

        parameters = pika.URLParameters(self.url)

        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()

        self._channel.exchange_declare(
            exchange=self.exchange,
            exchange_type="topic",
            durable=True,
        )

        self._channel.confirm_delivery()

        logger.info(
            "rabbitmq publisher connected",
            extra={
                "service": "outbox_worker",
                "exchange": self.exchange,
            },
        )

    def publish(
            self,
            *,
            routing_key: str,
            payload: dict[str, Any],
            message_id: str,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self.connect()

                if self._channel is None:
                    raise RuntimeError("RabbitMQ channel is not initialized")

                self._channel.basic_publish(
                    exchange=self.exchange,
                    routing_key=routing_key,
                    body=body,
                    properties=pika.BasicProperties(
                        content_type="application/json",
                        delivery_mode=2,
                        message_id=message_id,
                    ),
                    mandatory=True,
                )

                logger.info(
                    "rabbitmq event published",
                    extra={
                        "service": "outbox_worker",
                        "event_id": message_id,
                        "topic": routing_key,
                    },
                )

                return

            except (
                AMQPConnectionError,
                StreamLostError,
                ChannelClosedByBroker,
                AMQPError,
                OSError,
            ) as exc:
                last_error = exc

                logger.warning(
                    "rabbitmq publish attempt failed",
                    extra={
                        "service": "outbox_worker",
                        "event_id": message_id,
                        "topic": routing_key,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "error": str(exc),
                    },
                )

                self.close(silent=True)

                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds)

        raise RuntimeError(
            f"RabbitMQ publish failed after {self.max_retries} attempts: {last_error}"
        )

    def close(self, *, silent: bool = False) -> None:
        try:
            if self._channel and self._channel.is_open:
                self._channel.close()

            if self._connection and self._connection.is_open:
                self._connection.close()

            if not silent:
                logger.info(
                    "rabbitmq publisher closed",
                    extra={
                        "service": "outbox_worker",
                        "exchange": self.exchange,
                    },
                )

        finally:
            self._channel = None
            self._connection = None

    def _is_connected(self) -> bool:
        return (
            self._connection is not None
            and self._connection.is_open
            and self._channel is not None
            and self._channel.is_open
        )


def build_rabbitmq_publisher() -> RabbitMQPublisher:
    return RabbitMQPublisher(
        url=settings.RABBITMQ_URL,
        exchange=settings.RABBITMQ_EXCHANGE,
    )
