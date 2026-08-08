from app.api.v1.common.rate_limit import check_rate_limit
from app.api.v1.monitoring.services.marketplace_url import (
    MarketplaceUrlError,
    resolve_marketplace_url,
)
from app.api.v1.monitoring.services.product_preview import (
    ProductPreviewError,
    ProductPreviewService,
    ProductPreviewTemporarilyUnavailableError,
)
from app.api.v1.monitoring.services.target_duplicate_service import (
    find_existing_monitoring_target,
)
from app.api.v1.notifications.telegram.action_rate_limiter import (
    TelegramActionRateLimiter,
)
from app.api.v1.notifications.telegram.keyboards import (
    build_existing_product_keyboard,
    build_product_preview_keyboard,
)
from app.api.v1.notifications.telegram.pending_product import (
    PendingProductStoreError,
    TelegramPendingProductStore,
)
from app.api.v1.notifications.telegram.product_presenter import (
    build_product_already_tracked_text,
    build_product_preview_text,
)
from app.api.v1.notifications.telegram.replies import TelegramReplyService
from app.api.v1.notifications.telegram.telegram_metrics import (
    TELEGRAM_PREVIEWS_TOTAL,
    normalize_marketplace_label,
)
from app.api.v1.notifications.telegram.user_context import (
    TelegramUserContext,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


class TelegramProductLinkHandler:
    def __init__(
        self,
        *,
        replies: TelegramReplyService,
        preview_service: ProductPreviewService,
        pending_store: TelegramPendingProductStore,
        action_rate_limiter: TelegramActionRateLimiter | None = None,
        preview_rate_limit: int = 5,
        preview_rate_limit_window_seconds: int = 60,
    ) -> None:
        self.replies = replies
        self.preview_service = preview_service
        self.pending_store = pending_store
        self.action_rate_limiter = action_rate_limiter
        self.preview_rate_limit = preview_rate_limit
        self.preview_rate_limit_window_seconds = (
            preview_rate_limit_window_seconds
        )

    def handle(
        self,
        *,
        user_context: TelegramUserContext,
        text: str,
    ) -> None:
        try:
            resolved_url = resolve_marketplace_url(
                text=text,
            )
        except MarketplaceUrlError as exc:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace="unknown",
                result="invalid_url",
            ).inc()
            self.replies.send_message(
                chat_id=user_context.telegram_chat_id,
                text=str(exc),
            )
            return

        marketplace_label = normalize_marketplace_label(
            resolved_url.marketplace
        )

        existing_target = find_existing_monitoring_target(
            user=user_context.user,
            marketplace=resolved_url.marketplace,
            external_id="",
            url=resolved_url.url,
        )

        if existing_target is not None:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace=marketplace_label,
                result="duplicate",
            ).inc()
            self._send_existing_target(
                user_context=user_context,
                target=existing_target,
            )
            return

        if not self._check_preview_rate_limit(
            user_context=user_context,
            marketplace=marketplace_label,
        ):
            return

        try:
            preview = self.preview_service.preview_product(
                marketplace=resolved_url.marketplace,
                url=resolved_url.url,
            )
        except ProductPreviewTemporarilyUnavailableError as exc:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace=marketplace_label,
                result="temporarily_unavailable",
            ).inc()
            self.replies.send_message(
                chat_id=user_context.telegram_chat_id,
                text=f"⏳ {exc}",
            )
            return

        except ProductPreviewError as exc:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace=marketplace_label,
                result="error",
            ).inc()
            self.replies.send_message(
                chat_id=user_context.telegram_chat_id,
                text=f"⚠️ {exc}",
            )
            return

        existing_target = find_existing_monitoring_target(
            user=user_context.user,
            marketplace=resolved_url.marketplace,
            external_id=preview.external_id,
            url=resolved_url.url,
        )

        if existing_target is not None:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace=marketplace_label,
                result="duplicate",
            ).inc()
            self._send_existing_target(
                user_context=user_context,
                target=existing_target,
            )
            return

        try:
            pending_product = self.pending_store.create(
                user_id=user_context.user.pk,
                telegram_chat_id=(
                    user_context.telegram_chat_id
                ),
                marketplace=resolved_url.marketplace,
                url=resolved_url.url,
                external_id=preview.external_id,
                title=preview.title,
                seller_name=preview.seller_name,
                brand=preview.brand,
                price=preview.price,
                old_price=preview.old_price,
                currency=preview.currency,
                is_available=preview.is_available,
                rating=preview.rating,
                reviews_count=preview.reviews_count,
            )
        except PendingProductStoreError as exc:
            TELEGRAM_PREVIEWS_TOTAL.labels(
                marketplace=marketplace_label,
                result="pending_store_error",
            ).inc()
            logger.exception(
                "Failed to store Telegram pending product",
                extra={
                    "service": "telegram_bot",
                    "user_id": str(user_context.user.pk),
                    "chat_id": user_context.telegram_chat_id,
                    "marketplace": resolved_url.marketplace,
                },
            )
            self.replies.send_message(
                chat_id=user_context.telegram_chat_id,
                text=f"⚠️ {exc}",
            )
            return

        sent = self.replies.send_message(
            chat_id=user_context.telegram_chat_id,
            text=build_product_preview_text(
                marketplace=resolved_url.marketplace,
                preview=preview,
            ),
            reply_markup=build_product_preview_keyboard(
                token=pending_product.token,
            ),
        )

        TELEGRAM_PREVIEWS_TOTAL.labels(
            marketplace=marketplace_label,
            result=(
                "success"
                if sent
                else "reply_failed"
            ),
        ).inc()

        if not sent:
            self.pending_store.delete(
                token=pending_product.token,
            )

    def _send_existing_target(
        self,
        *,
        user_context: TelegramUserContext,
        target,
    ) -> None:
        self.replies.send_message(
            chat_id=user_context.telegram_chat_id,
            text=build_product_already_tracked_text(
                target=target,
            ),
            reply_markup=build_existing_product_keyboard(
                target_id=str(target.id),
            ),
        )

    def _check_preview_rate_limit(
        self,
        *,
        user_context: TelegramUserContext,
        marketplace: str,
    ) -> bool:
        if self.action_rate_limiter is not None:
            result = self.action_rate_limiter.check_preview(
                user_id=user_context.user.pk,
            )
        else:
            result = check_rate_limit(
                key=(
                    "telegram_bot:preview:"
                    f"{user_context.user.pk}"
                ),
                limit=self.preview_rate_limit,
                window_seconds=(
                    self.preview_rate_limit_window_seconds
                ),
            )

        if result.allowed:
            return True

        TELEGRAM_PREVIEWS_TOTAL.labels(
            marketplace=marketplace,
            result="rate_limited",
        ).inc()

        retry_after_seconds = max(
            int(result.retry_after_seconds),
            1,
        )
        self.replies.send_message(
            chat_id=user_context.telegram_chat_id,
            text=(
                "Слишком много запросов на проверку товара. "
                f"Повторите через {retry_after_seconds} секунд."
            ),
        )
        return False
