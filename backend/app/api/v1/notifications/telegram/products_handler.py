from typing import Any
from uuid import UUID

from app.api.v1.monitoring.services.target_query_service import (
    DEFAULT_TELEGRAM_PRODUCTS_PAGE_SIZE,
    MonitoringTargetPage,
    list_monitoring_targets_for_user,
)
from app.api.v1.monitoring.services.target_service import (
    MonitoringTargetCheckBusyError,
    MonitoringTargetCheckError,
    MonitoringTargetCheckTemporarilyUnavailableError,
    MonitoringTargetNotFoundError,
    check_monitoring_target_now,
    delete_monitoring_target,
    get_monitoring_target_for_user,
    pause_monitoring_target,
    resume_monitoring_target,
)
from app.api.v1.notifications.telegram.action_rate_limiter import (
    TelegramActionRateLimiter,
)
from app.api.v1.notifications.telegram.client import TelegramBotClient
from app.api.v1.notifications.telegram.keyboards import (
    PRODUCTS_PAGE_CALLBACK_PREFIX,
    TARGET_CHECK_CALLBACK_PREFIX,
    TARGET_DELETE_ASK_CALLBACK_PREFIX,
    TARGET_DELETE_CANCEL_CALLBACK_PREFIX,
    TARGET_DELETE_CONFIRM_CALLBACK_PREFIX,
    TARGET_PAUSE_CALLBACK_PREFIX,
    TARGET_RESUME_CALLBACK_PREFIX,
    TARGET_SETTINGS_CALLBACK_PREFIX,
    build_products_keyboard,
    build_target_delete_confirmation_keyboard,
)
from app.api.v1.notifications.telegram.products_presenter import (
    build_products_page_text,
    build_target_check_result_text,
    build_target_delete_confirmation_text,
)
from app.api.v1.notifications.telegram.replies import (
    TelegramReplyService,
)
from app.api.v1.notifications.telegram.telegram_metrics import (
    TELEGRAM_TARGET_ACTIONS_TOTAL,
)
from app.api.v1.notifications.telegram.user_context import (
    TelegramUserContext,
    TelegramUserContextResolver,
)
from app.core.logging import get_logger


logger = get_logger(__name__)

MESSAGE_CALLBACK_EXPIRED = (
    "Это действие устарело. Откройте список заново командой /products."
)
MESSAGE_CALLBACK_FORBIDDEN = (
    "Это действие относится к другому пользователю."
)
MESSAGE_CONNECT_REQUIRED = (
    "Telegram не подключён к аккаунту. "
    "Откройте персональную ссылку подключения."
)
MESSAGE_TARGET_NOT_FOUND = (
    "Товар не найден или уже был удалён."
)
MESSAGE_CHECK_BUSY = (
    "Товар уже обновляется. Попробуйте ещё раз через несколько секунд."
)
MESSAGE_CHECK_RATE_LIMITED = (
    "Слишком много ручных проверок. "
    "Повторите через {retry_after_seconds} секунд."
)
MESSAGE_SETTINGS_NOT_AVAILABLE = (
    "Настройки уведомлений для товара добавим следующим этапом."
)


class TelegramProductsHandler:
    def __init__(
        self,
        *,
        client: TelegramBotClient,
        replies: TelegramReplyService,
        user_context_resolver: TelegramUserContextResolver,
        action_rate_limiter: TelegramActionRateLimiter | None = None,
        page_size: int = DEFAULT_TELEGRAM_PRODUCTS_PAGE_SIZE,
    ) -> None:
        self.client = client
        self.replies = replies
        self.user_context_resolver = user_context_resolver
        self.action_rate_limiter = action_rate_limiter
        self.page_size = page_size

    def can_handle_callback(
        self,
        *,
        callback_data: str,
    ) -> bool:
        prefixes = (
            PRODUCTS_PAGE_CALLBACK_PREFIX,
            TARGET_CHECK_CALLBACK_PREFIX,
            TARGET_PAUSE_CALLBACK_PREFIX,
            TARGET_RESUME_CALLBACK_PREFIX,
            TARGET_SETTINGS_CALLBACK_PREFIX,
            TARGET_DELETE_ASK_CALLBACK_PREFIX,
            TARGET_DELETE_CONFIRM_CALLBACK_PREFIX,
            TARGET_DELETE_CANCEL_CALLBACK_PREFIX,
        )
        return callback_data.startswith(prefixes)

    def handle_command(
        self,
        *,
        user_context: TelegramUserContext,
        page: int = 1,
    ) -> None:
        target_page = self._get_page(
            user_context=user_context,
            page=page,
        )
        self.replies.send_message(
            chat_id=user_context.telegram_chat_id,
            text=build_products_page_text(
                target_page=target_page,
            ),
            reply_markup=build_products_keyboard(
                target_page=target_page,
            ),
        )

    def edit_page(
        self,
        *,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        page: int,
    ) -> None:
        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )

    def handle_callback(
        self,
        *,
        callback_query: dict[str, Any],
    ) -> None:
        callback_query_id = str(
            callback_query.get("id") or ""
        ).strip()
        callback_data = str(
            callback_query.get("data") or ""
        ).strip()
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        message_id = message.get("message_id")
        from_user = callback_query.get("from") or {}
        from_user_id = str(from_user.get("id") or "").strip()

        if not callback_query_id:
            return

        if not chat_id or not isinstance(message_id, int):
            self._answer_expired(
                callback_query_id=callback_query_id,
            )
            return

        if from_user_id and from_user_id != chat_id:
            self.client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=MESSAGE_CALLBACK_FORBIDDEN,
                show_alert=True,
            )
            return

        user_context = self.user_context_resolver.resolve(
            telegram_chat_id=chat_id,
        )

        if user_context is None:
            self.client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=MESSAGE_CONNECT_REQUIRED,
                show_alert=True,
            )
            return

        parsed_callback = self._parse_callback_data(
            callback_data=callback_data,
        )

        if parsed_callback is None:
            self._answer_expired(
                callback_query_id=callback_query_id,
            )
            return

        action, target_id, page = parsed_callback

        if action == "page":
            self.client.answer_callback_query(
                callback_query_id=callback_query_id,
            )
            self._edit_page(
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                page=page,
            )
            return

        if target_id is None:
            self._answer_expired(
                callback_query_id=callback_query_id,
            )
            return

        if action == "check":
            self._check_target(
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                target_id=target_id,
                page=page,
            )
            return

        if action == "pause":
            self._pause_target(
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                target_id=target_id,
                page=page,
            )
            return

        if action == "resume":
            self._resume_target(
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                target_id=target_id,
                page=page,
            )
            return

        if action == "settings":
            self.client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=MESSAGE_SETTINGS_NOT_AVAILABLE,
                show_alert=True,
            )
            return

        if action == "delete_ask":
            self._show_delete_confirmation(
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                target_id=target_id,
                page=page,
            )
            return

        if action == "delete_confirm":
            self._delete_target(
                callback_query_id=callback_query_id,
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                target_id=target_id,
                page=page,
            )
            return

        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
        )
        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )

    def _check_target(
        self,
        *,
        callback_query_id: str,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        target_id: UUID,
        page: int,
    ) -> None:
        if self.action_rate_limiter is not None:
            rate_limit_result = (
                self.action_rate_limiter.check_check_now(
                    user_id=user_context.user.pk,
                )
            )
            if not rate_limit_result.allowed:
                TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                    action="check",
                    result="rate_limited",
                ).inc()

                retry_after_seconds = max(
                    rate_limit_result.retry_after_seconds,
                    1,
                )
                self.client.answer_callback_query(
                    callback_query_id=callback_query_id,
                    text=MESSAGE_CHECK_RATE_LIMITED.format(
                        retry_after_seconds=retry_after_seconds,
                    ),
                    show_alert=True,
                )
                return

        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
            text="Проверяю товар...",
        )

        try:
            result = check_monitoring_target_now(
                user=user_context.user,
                target_id=target_id,
            )
        except MonitoringTargetCheckTemporarilyUnavailableError as exc:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="check",
                result="temporarily_unavailable",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=f"⏳ {exc}",
            )
            return
        except MonitoringTargetCheckBusyError:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="check",
                result="busy",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=MESSAGE_CHECK_BUSY,
            )
            return
        except MonitoringTargetNotFoundError:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="check",
                result="not_found",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=MESSAGE_TARGET_NOT_FOUND,
            )
            self._edit_page(
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                page=page,
            )
            return
        except MonitoringTargetCheckError as exc:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="check",
                result="error",
            ).inc()

            logger.warning(
                "Telegram target manual check failed",
                extra={
                    "service": "telegram_bot",
                    "user_id": str(user_context.user.pk),
                    "target_id": str(target_id),
                    "error": str(exc),
                },
            )
            self.client.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ Не удалось проверить товар.\n\n"
                    f"{exc}"
                ),
            )
            return
        except Exception as exc:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="check",
                result="unexpected_error",
            ).inc()

            logger.exception(
                "Unexpected Telegram target manual check error",
                extra={
                    "service": "telegram_bot",
                    "user_id": str(user_context.user.pk),
                    "target_id": str(target_id),
                    "error": str(exc),
                },
            )
            self.client.send_message(
                chat_id=chat_id,
                text="⚠️ Внутренняя ошибка при проверке товара.",
            )
            return

        TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
            action="check",
            result="success",
        ).inc()

        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )
        self.client.send_message(
            chat_id=chat_id,
            text=build_target_check_result_text(
                target=result.target,
                price=result.snapshot.price,
                currency=result.snapshot.currency,
                is_available=result.snapshot.is_available,
                alerts_count=result.alerts_count,
            ),
        )

    def _pause_target(
        self,
        *,
        callback_query_id: str,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        target_id: UUID,
        page: int,
    ) -> None:
        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
            text="Приостанавливаю отслеживание...",
        )

        try:
            pause_monitoring_target(
                user=user_context.user,
                target_id=target_id,
            )
        except MonitoringTargetNotFoundError:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="pause",
                result="not_found",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=MESSAGE_TARGET_NOT_FOUND,
            )
        else:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="pause",
                result="success",
            ).inc()

        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )

    def _resume_target(
        self,
        *,
        callback_query_id: str,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        target_id: UUID,
        page: int,
    ) -> None:
        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
            text="Возобновляю отслеживание...",
        )

        try:
            resume_monitoring_target(
                user=user_context.user,
                target_id=target_id,
            )
        except MonitoringTargetNotFoundError:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="resume",
                result="not_found",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=MESSAGE_TARGET_NOT_FOUND,
            )
        else:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="resume",
                result="success",
            ).inc()

        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )

    def _show_delete_confirmation(
        self,
        *,
        callback_query_id: str,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        target_id: UUID,
        page: int,
    ) -> None:
        try:
            target = get_monitoring_target_for_user(
                user=user_context.user,
                target_id=target_id,
            )
        except MonitoringTargetNotFoundError:
            self.client.answer_callback_query(
                callback_query_id=callback_query_id,
                text=MESSAGE_TARGET_NOT_FOUND,
                show_alert=True,
            )
            self._edit_page(
                chat_id=chat_id,
                message_id=message_id,
                user_context=user_context,
                page=page,
            )
            return

        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
        )
        self.client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=build_target_delete_confirmation_text(
                target=target,
            ),
            reply_markup=(
                build_target_delete_confirmation_keyboard(
                    target_id=str(target.id),
                    page=page,
                )
            ),
        )

    def _delete_target(
        self,
        *,
        callback_query_id: str,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        target_id: UUID,
        page: int,
    ) -> None:
        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
            text="Удаляю товар...",
        )

        try:
            delete_monitoring_target(
                user=user_context.user,
                target_id=target_id,
            )
        except MonitoringTargetNotFoundError:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="delete",
                result="not_found",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text=MESSAGE_TARGET_NOT_FOUND,
            )
        else:
            TELEGRAM_TARGET_ACTIONS_TOTAL.labels(
                action="delete",
                result="success",
            ).inc()

            self.client.send_message(
                chat_id=chat_id,
                text="✅ Товар удалён из отслеживания.",
            )

        self._edit_page(
            chat_id=chat_id,
            message_id=message_id,
            user_context=user_context,
            page=page,
        )

    def _edit_page(
        self,
        *,
        chat_id: str,
        message_id: int,
        user_context: TelegramUserContext,
        page: int,
    ) -> None:
        target_page = self._get_page(
            user_context=user_context,
            page=page,
        )
        self.client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=build_products_page_text(
                target_page=target_page,
            ),
            reply_markup=build_products_keyboard(
                target_page=target_page,
            ),
        )

    def _get_page(
        self,
        *,
        user_context: TelegramUserContext,
        page: int,
    ) -> MonitoringTargetPage:
        return list_monitoring_targets_for_user(
            user=user_context.user,
            page=page,
            page_size=self.page_size,
        )

    def _answer_expired(
        self,
        *,
        callback_query_id: str,
    ) -> None:
        self.client.answer_callback_query(
            callback_query_id=callback_query_id,
            text=MESSAGE_CALLBACK_EXPIRED,
            show_alert=True,
        )

    @staticmethod
    def _parse_callback_data(
        *,
        callback_data: str,
    ) -> tuple[str, UUID | None, int] | None:
        if callback_data.startswith(PRODUCTS_PAGE_CALLBACK_PREFIX):
            page_raw = callback_data[
                len(PRODUCTS_PAGE_CALLBACK_PREFIX):
            ]

            try:
                return "page", None, max(1, int(page_raw))
            except (TypeError, ValueError):
                return None

        prefix_actions = (
            (TARGET_CHECK_CALLBACK_PREFIX, "check"),
            (TARGET_PAUSE_CALLBACK_PREFIX, "pause"),
            (TARGET_RESUME_CALLBACK_PREFIX, "resume"),
            (TARGET_SETTINGS_CALLBACK_PREFIX, "settings"),
            (TARGET_DELETE_ASK_CALLBACK_PREFIX, "delete_ask"),
            (
                TARGET_DELETE_CONFIRM_CALLBACK_PREFIX,
                "delete_confirm",
            ),
            (
                TARGET_DELETE_CANCEL_CALLBACK_PREFIX,
                "delete_cancel",
            ),
        )

        for prefix, action in prefix_actions:
            if not callback_data.startswith(prefix):
                continue

            payload = callback_data[len(prefix):]

            try:
                target_id_raw, page_raw = payload.rsplit(":", maxsplit=1)
                target_id = UUID(target_id_raw)
                page = max(1, int(page_raw))
            except (TypeError, ValueError):
                return None

            return action, target_id, page

        return None
