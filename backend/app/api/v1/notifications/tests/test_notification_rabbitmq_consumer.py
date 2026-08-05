from unittest.mock import MagicMock

from django.test import SimpleTestCase, override_settings

from app.api.v1.notifications.rabbitmq.notification_consumer import (
    NotificationRabbitMQConsumer,
)


class NotificationRabbitMQConsumerTopologyTests(SimpleTestCase):
    @override_settings(
        RABBITMQ_URL="amqp://guest:guest@rabbitmq:5672/",
        RABBITMQ_EXCHANGE="flashsale.events.test",
        NOTIFICATION_RABBITMQ_QUEUE="flashsale.notifications.test",
        NOTIFICATION_RABBITMQ_DLQ="flashsale.notifications.test.dlq",
        NOTIFICATION_RABBITMQ_DLX="flashsale.notifications.test.dlx",
        NOTIFICATION_RABBITMQ_ROUTING_KEYS=["alert.created"],
    )
    def test_declares_notification_queue_on_shared_exchange(self):
        consumer = NotificationRabbitMQConsumer()
        consumer.channel = MagicMock()

        consumer._declare_topology()

        consumer.channel.exchange_declare.assert_any_call(
            exchange="flashsale.events.test",
            exchange_type="topic",
            durable=True,
        )
        consumer.channel.queue_bind.assert_any_call(
            queue="flashsale.notifications.test",
            exchange="flashsale.events.test",
            routing_key="alert.created",
        )
