import httpx
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from prometheus_client import start_http_server
from app.core.logging import get_logger

from app.api.v1.monitoring.services.product_preview import (
    ProductPreviewService,
)
from app.api.v1.notifications.telegram.action_rate_limiter import (
    TelegramActionRateLimiter,
)
from app.api.v1.notifications.telegram.client import TelegramBotClient
from app.api.v1.notifications.telegram.commands import (
    TELEGRAM_BOT_COMMANDS,
)
from app.api.v1.notifications.telegram.help_handler import (
    TelegramHelpHandler,
)
from app.api.v1.notifications.telegram.notifications_handler import (
    TelegramNotificationsHandler,
)
from app.api.v1.notifications.telegram.pending_product import (
    TelegramPendingProductStore,
)
from app.api.v1.notifications.telegram.polling import (
    TelegramPollingRunner,
)
from app.api.v1.notifications.telegram.product_callback_handler import (
    TelegramProductCallbackHandler,
)
from app.api.v1.notifications.telegram.product_link_handler import (
    TelegramProductLinkHandler,
)
from app.api.v1.notifications.telegram.products_handler import (
    TelegramProductsHandler,
)
from app.api.v1.notifications.telegram.replies import (
    TelegramReplyService,
)
from app.api.v1.notifications.telegram.router import TelegramUpdateRouter
from app.api.v1.notifications.telegram.start_handler import (
    TelegramStartHandler,
)
from app.api.v1.notifications.telegram.target_alert_settings_handler import (
    TelegramTargetAlertSettingsHandler,
)
from app.api.v1.notifications.telegram.target_history_handler import (
    TelegramTargetHistoryHandler,
)
from app.api.v1.notifications.telegram.target_interval_handler import (
    TelegramTargetIntervalHandler,
)
from app.api.v1.notifications.telegram.user_context import (
    TelegramUserContextResolver,
)


logger = get_logger(__name__)


class Command(BaseCommand):
    help = "Run Telegram bot polling"

    def handle(self, *args, **options) -> None:
        metrics_port = int(
            getattr(
                settings,
                "NOTIF_TELEGRAM_METRICS_PORT",
                8013,
            )
        )
        bot_token = settings.NOTIF_TELEGRAM_BOT_TOKEN

        if not bot_token:
            raise RuntimeError(
                "NOTIF_TELEGRAM_BOT_TOKEN is empty"
            )

        start_http_server(metrics_port)

        client = TelegramBotClient(
            token=bot_token,
        )

        commands_registered = False

        for attempt in range(1, 4):
            try:
                client.set_my_commands(
                    commands=TELEGRAM_BOT_COMMANDS,
                )
            except httpx.TransportError as exc:
                logger.warning(
                    "Telegram bot command registration failed",
                    extra={
                        "service": "telegram_bot",
                        "attempt": attempt,
                        "max_attempts": 3,
                        "error": str(exc),
                    },
                )

                if attempt < 3:
                    time.sleep(attempt * 2)
            else:
                commands_registered = True

                logger.info(
                    "Telegram bot commands registered",
                    extra={
                        "service": "telegram_bot",
                        "commands_count": len(TELEGRAM_BOT_COMMANDS),
                    },
                )
                break

        if not commands_registered:
            logger.warning(
                "Telegram bot started without updating command menu",
                extra={
                    "service": "telegram_bot",
                },
            )

        user_context_resolver = TelegramUserContextResolver()
        action_rate_limiter = TelegramActionRateLimiter(
            preview_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_LIMIT",
                    5,
                )
            ),
            preview_window_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_WINDOW_SECONDS",
                    60,
                )
            ),
            check_now_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_LIMIT",
                    3,
                )
            ),
            check_now_window_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_WINDOW_SECONDS",
                    60,
                )
            ),
            callback_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_LIMIT",
                    30,
                )
            ),
            callback_window_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_WINDOW_SECONDS",
                    60,
                )
            ),
        )
        replies = TelegramReplyService(
            client=client,
            rate_limit_limit=(
                settings.NOTIF_TELEGRAM_REPLY_RATE_LIMIT_LIMIT
            ),
            rate_limit_window_seconds=(
                settings.NOTIF_TELEGRAM_REPLY_RATE_LIMIT_WINDOW_SECONDS
            ),
        )
        pending_store = TelegramPendingProductStore(
            ttl_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PENDING_PRODUCT_TTL_SECONDS",
                    600,
                )
            ),
            lock_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PENDING_PRODUCT_LOCK_SECONDS",
                    30,
                )
            ),
        )
        start_handler = TelegramStartHandler(
            replies=replies,
            user_context_resolver=user_context_resolver,
        )
        help_handler = TelegramHelpHandler(
            replies=replies,
        )
        product_link_handler = TelegramProductLinkHandler(
            replies=replies,
            preview_service=ProductPreviewService(),
            pending_store=pending_store,
            action_rate_limiter=action_rate_limiter,
            preview_rate_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_LIMIT",
                    5,
                )
            ),
            preview_rate_limit_window_seconds=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_WINDOW_SECONDS",
                    60,
                )
            ),
        )
        product_callback_handler = (
            TelegramProductCallbackHandler(
                client=client,
                pending_store=pending_store,
                user_context_resolver=(
                    user_context_resolver
                ),
            )
        )
        products_handler = TelegramProductsHandler(
            client=client,
            replies=replies,
            user_context_resolver=user_context_resolver,
            action_rate_limiter=action_rate_limiter,
            page_size=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_PRODUCTS_PAGE_SIZE",
                    3,
                )
            ),
        )
        target_alert_settings_handler = (
            TelegramTargetAlertSettingsHandler(
                client=client,
                user_context_resolver=user_context_resolver,
                products_handler=products_handler,
            )
        )
        target_interval_handler = TelegramTargetIntervalHandler(
            client=client,
            user_context_resolver=user_context_resolver,
            products_handler=products_handler,
        )
        target_history_handler = TelegramTargetHistoryHandler(
            client=client,
            user_context_resolver=user_context_resolver,
            products_handler=products_handler,
            history_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_TARGET_HISTORY_LIMIT",
                    5,
                )
            ),
        )
        notifications_handler = TelegramNotificationsHandler(
            client=client,
            replies=replies,
            user_context_resolver=user_context_resolver,
            delivery_history_limit=int(
                getattr(
                    settings,
                    "NOTIF_TELEGRAM_DELIVERY_HISTORY_LIMIT",
                    10,
                )
            ),
        )
        router = TelegramUpdateRouter(
            client=client,
            replies=replies,
            start_handler=start_handler,
            help_handler=help_handler,
            user_context_resolver=user_context_resolver,
            product_link_handler=product_link_handler,
            product_callback_handler=(
                product_callback_handler
            ),
            products_handler=products_handler,
            notifications_handler=notifications_handler,
            target_alert_settings_handler=(
                target_alert_settings_handler
            ),
            target_interval_handler=target_interval_handler,
            target_history_handler=target_history_handler,
            action_rate_limiter=action_rate_limiter,
        )
        runner = TelegramPollingRunner(
            client=client,
            router=router,
            drop_pending_updates_on_start=(
                settings.NOTIF_TELEGRAM_DROP_PENDING_UPDATES_ON_START
            ),
        )

        runner.run()
