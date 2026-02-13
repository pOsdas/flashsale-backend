from django.db import models


class ProcessedWebhookEvent(models.Model):
    provider = models.CharField(max_length=64)
    event_id = models.CharField(max_length=128)
    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()

    class Meta:
        unique_together = [("provider", "event_id")]
        indexes = [models.Index(fields=["provider", "received_at"])]

    def __str__(self):
        return f"WebhookEvent(provider={self.provider}, event_id={self.event_id})"

