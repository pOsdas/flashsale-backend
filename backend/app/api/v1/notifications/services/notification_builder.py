from django.utils import timezone


class AlertNotificationBuilder:
    @classmethod
    def build_telegram_message(cls, alert) -> str:
        product_title = cls._get_product_title(alert)
        marketplace = cls._get_marketplace(alert)
        change_title = cls._get_change_title(alert)
        old_value = cls._get_old_value(alert)
        new_value = cls._get_new_value(alert)
        created_at = cls._format_datetime(alert.created_at)

        return (
            "🔴 Конкурент изменил товар\n\n"
            f"Товар:\n{product_title}\n\n"
            f"Изменение:\n{change_title}\n\n"
            f"Было:\n{old_value}\n\n"
            f"Стало:\n{new_value}\n\n"
            f"{marketplace}\n"
            f"{created_at}"
        )

    @classmethod
    def _get_product_title(cls, alert) -> str:
        snapshot = getattr(alert, "snapshot", None)

        if snapshot and getattr(snapshot, "title", ""):
            return snapshot.title

        target = getattr(alert, "target", None)

        if target and getattr(target, "title", ""):
            return target.title

        return "Неизвестный товар"

    @classmethod
    def _get_marketplace(cls, alert) -> str:
        target = getattr(alert, "target", None)

        if target and getattr(target, "marketplace", ""):
            return str(target.marketplace).upper()

        return "MARKETPLACE"

    @classmethod
    def _get_change_title(cls, alert) -> str:
        alert_type = getattr(alert, "alert_type", "")

        titles = {
            "price_changed": "Цена изменилась",
            "price_dropped": "Цена снизилась",
            "price_increased": "Цена выросла",
            "became_available": "Товар появился в наличии",
            "became_unavailable": "Товар пропал из наличия",
            "title_changed": "Название изменилось",
            "rating_changed": "Рейтинг изменился",
            "reviews_count_changed": "Количество отзывов изменилось",
            "card_changed": "Карточка товара изменилась",
        }

        return titles.get(alert_type, "Обнаружено изменение")

    @classmethod
    def _get_old_value(cls, alert) -> str:
        old_value = getattr(alert, "old_value", None)

        if old_value not in (None, ""):
            return cls._format_value(alert, old_value)

        return "—"

    @classmethod
    def _get_new_value(cls, alert) -> str:
        new_value = getattr(alert, "new_value", None)

        if new_value not in (None, ""):
            return cls._format_value(alert, new_value)

        snapshot = getattr(alert, "snapshot", None)

        if snapshot:
            return cls._snapshot_value_by_alert_type(alert, snapshot)

        return "—"

    @classmethod
    def _snapshot_value_by_alert_type(cls, alert, snapshot) -> str:
        alert_type = getattr(alert, "alert_type", "")

        if alert_type in ("price_changed", "price_dropped", "price_increased"):
            value = getattr(snapshot, "price", None)
            return cls._format_price(value)

        if alert_type in ("became_available", "became_unavailable"):
            value = getattr(snapshot, "is_available", None)
            return "В наличии" if value else "Нет в наличии"

        if alert_type == "title_changed":
            return getattr(snapshot, "title", "—") or "—"

        if alert_type == "rating_changed":
            value = getattr(snapshot, "rating", None)
            return cls._format_rating(value)

        if alert_type == "reviews_count_changed":
            value = getattr(snapshot, "reviews_count", None)
            return cls._format_reviews_count(value)

        return "—"

    @classmethod
    def _format_value(cls, alert, value) -> str:
        alert_type = getattr(alert, "alert_type", "")

        if not isinstance(value, dict):
            return cls._format_scalar_value(
                alert_type=alert_type,
                value=value,
            )

        if alert_type in (
                "price_changed",
                "price_dropped",
                "price_increased",
        ):
            return cls._format_price_change_value(value)

        if alert_type in (
                "became_available",
                "became_unavailable",
        ):
            return cls._format_availability_value(
                value.get("is_available"),
            )

        if alert_type == "title_changed":
            return str(value.get("title") or "—")

        if alert_type == "rating_changed":
            return cls._format_rating(
                value.get("rating"),
            )

        if alert_type == "reviews_count_changed":
            return cls._format_reviews_count(
                value.get("reviews_count"),
            )

        return str(value)

    @classmethod
    def _format_scalar_value(
        cls,
        *,
        alert_type: str,
        value,
    ) -> str:
        if alert_type in (
            "price_changed",
            "price_dropped",
            "price_increased",
        ):
            return cls._format_price(value)

        if alert_type in (
            "became_available",
            "became_unavailable",
        ):
            return cls._format_availability_value(value)

        if alert_type == "rating_changed":
            return cls._format_rating(value)

        if alert_type == "reviews_count_changed":
            return cls._format_reviews_count(value)

        if value in (None, ""):
            return "—"

        return str(value)

    @classmethod
    def _format_price_change_value(
        cls,
        value: dict,
    ) -> str:
        price = cls._format_price(
            value.get("price"),
        )

        percent_change = value.get("percent_change")

        if percent_change in (None, ""):
            return price

        formatted_percent = cls._format_percent(
            percent_change,
        )

        if not formatted_percent:
            return price

        return f"{price} ({formatted_percent})"

    @staticmethod
    def _format_availability_value(value) -> str:
        if value in (True, "true", "True", 1, "1"):
            return "В наличии"

        if value in (False, "false", "False", 0, "0"):
            return "Нет в наличии"

        return "—"

    @staticmethod
    def _format_rating(value) -> str:
        if value in (None, ""):
            return "—"

        try:
            rating = float(value)
        except (TypeError, ValueError):
            return str(value)

        formatted = f"{rating:.2f}".rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _format_reviews_count(value) -> str:
        if value in (None, ""):
            return "—"

        try:
            return f"{int(value):,}".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_percent(value) -> str:
        try:
            percent = float(value)
        except (TypeError, ValueError):
            return ""

        sign = "+" if percent > 0 else ""
        formatted = f"{percent:.2f}".rstrip("0").rstrip(".")

        return f"{sign}{formatted}%"

    @classmethod
    def _format_price(cls, value) -> str:
        if value in (None, ""):
            return "—"

        try:
            price = float(value)
        except (TypeError, ValueError):
            return str(value)

        if price.is_integer():
            formatted = f"{int(price):,}".replace(",", " ")
        else:
            formatted = f"{price:,.2f}".replace(",", " ")

        return f"{formatted} ₽"

    @classmethod
    def _format_datetime(cls, value) -> str:
        if not value:
            value = timezone.now()

        local_value = timezone.localtime(value)
        return local_value.strftime("%d.%m.%Y %H:%M")
