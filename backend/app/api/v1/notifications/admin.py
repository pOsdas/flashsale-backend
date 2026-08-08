from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from app.api.v1.notifications.models import (
    NotificationChannel,
    NotificationDelivery,
)
from app.api.v1.notifications.services.telegram_onboarding import (
    TelegramOnboardingError,
    TelegramOnboardingService,
)


User = get_user_model()


@admin.action(description="Сгенерировать ссылку подключения Telegram")
def generate_telegram_connect_link(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Выберите ровно одного пользователя.",
            level=messages.ERROR,
        )
        return

    user = queryset.first()

    try:
        connect_link = TelegramOnboardingService.build_connect_link(user)
    except TelegramOnboardingError as exc:
        modeladmin.message_user(
            request,
            str(exc),
            level=messages.ERROR,
        )
        return

    modeladmin.message_user(
        request,
        f"Ссылка подключения Telegram: {connect_link.url}",
        level=messages.SUCCESS,
    )


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    actions = (
        generate_telegram_connect_link,
    )


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "type",
        "telegram_chat_id",
        "email",
        "webhook_url",
        "is_active",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "type",
        "is_active",
        "created_at",
    )
    search_fields = (
        "user__id",
        "user__username",
        "user__email",
        "telegram_chat_id",
        "email",
        "webhook_url",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "channel",
        "alert",
        "status",
        "sent_at",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "status",
        "created_at",
        "sent_at",
    )
    search_fields = (
        "user__id",
        "user__username",
        "user__email",
        "message_text",
        "error",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "sent_at",
    )