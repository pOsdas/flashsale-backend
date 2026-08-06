from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.api.v1.monitoring.models import (
    AlertSeverity,
    AlertType,
    ProductSnapshot,
)


PERCENT_QUANTIZER = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    alert_type: str
    severity: str
    title: str
    message: str
    old_value: dict[str, Any]
    new_value: dict[str, Any]
    dedup_key: str
    change_percent: Decimal | None = None
    change_absolute: Decimal | None = None


def detect_snapshot_changes(
    *,
    previous_snapshot: ProductSnapshot | None,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    if previous_snapshot is None:
        return []

    candidates: list[AlertCandidate] = []

    candidates.extend(
        _detect_price_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )
    )
    candidates.extend(
        _detect_availability_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )
    )
    candidates.extend(
        _detect_rating_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )
    )
    candidates.extend(
        _detect_reviews_count_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )
    )
    candidates.extend(
        _detect_title_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )
    )

    return candidates


def _detect_price_changes(
    *,
    previous_snapshot: ProductSnapshot,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    previous_price = previous_snapshot.price
    current_price = current_snapshot.price

    if previous_price is None or current_price is None:
        return []

    if previous_price == current_price:
        return []

    difference = current_price - previous_price
    percent_change = _calculate_percent_change(
        previous_value=previous_price,
        current_value=current_price,
    )

    if current_price < previous_price:
        alert_type = AlertType.PRICE_DROPPED
        severity = AlertSeverity.HIGH
        title = "Цена товара снизилась"
        direction_text = "снизилась"
    else:
        alert_type = AlertType.PRICE_INCREASED
        severity = AlertSeverity.MEDIUM
        title = "Цена товара выросла"
        direction_text = "выросла"

    target_title = _get_target_title(
        snapshot=current_snapshot,
    )

    percent_text = ""

    if percent_change is not None:
        percent_text = (
            f" ({percent_change.quantize(PERCENT_QUANTIZER)}%)"
        )

    message = (
        f"Цена товара «{target_title}» {direction_text}: "
        f"{previous_price} ₽ → {current_price} ₽"
        f"{percent_text}."
    )

    new_value: dict[str, Any] = {
        "price": str(current_price),
    }

    if percent_change is not None:
        new_value["percent_change"] = str(
            percent_change.quantize(PERCENT_QUANTIZER)
        )

    return [
        AlertCandidate(
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            old_value={
                "price": str(previous_price),
            },
            new_value=new_value,
            dedup_key=_build_dedup_key(
                snapshot=current_snapshot,
                alert_type=alert_type,
            ),
            change_percent=(
                abs(percent_change)
                if percent_change is not None
                else None
            ),
            change_absolute=abs(difference),
        )
    ]


def _detect_availability_changes(
    *,
    previous_snapshot: ProductSnapshot,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    previous_available = previous_snapshot.is_available
    current_available = current_snapshot.is_available

    if previous_available is None or current_available is None:
        return []

    if previous_available == current_available:
        return []

    if current_available:
        alert_type = AlertType.BECAME_AVAILABLE
        severity = AlertSeverity.HIGH
        title = "Товар появился в наличии"
        message_text = "появился в наличии"
    else:
        alert_type = AlertType.BECAME_UNAVAILABLE
        severity = AlertSeverity.HIGH
        title = "Товар пропал из наличия"
        message_text = "пропал из наличия"

    target_title = _get_target_title(
        snapshot=current_snapshot,
    )

    message = f"Товар «{target_title}» {message_text}."

    return [
        AlertCandidate(
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            old_value={
                "is_available": previous_available,
            },
            new_value={
                "is_available": current_available,
            },
            dedup_key=_build_dedup_key(
                snapshot=current_snapshot,
                alert_type=alert_type,
            ),
        )
    ]


def _detect_rating_changes(
    *,
    previous_snapshot: ProductSnapshot,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    previous_rating = previous_snapshot.rating
    current_rating = current_snapshot.rating

    if previous_rating is None or current_rating is None:
        return []

    current_reviews_count = current_snapshot.reviews_count
    previous_reviews_count = previous_snapshot.reviews_count

    rating_disappeared_with_reviews = (
        previous_rating > 0
        and current_rating == 0
        and previous_reviews_count not in (None, 0)
        and current_reviews_count in (None, 0)
    )

    if rating_disappeared_with_reviews:
        return []

    if previous_rating == current_rating:
        return []

    difference = current_rating - previous_rating
    percent_change = _calculate_percent_change(
        previous_value=previous_rating,
        current_value=current_rating,
    )

    if current_rating < previous_rating:
        severity = AlertSeverity.MEDIUM
        title = "Рейтинг товара снизился"
        direction_text = "снизился"
    else:
        severity = AlertSeverity.LOW
        title = "Рейтинг товара вырос"
        direction_text = "вырос"

    target_title = _get_target_title(
        snapshot=current_snapshot,
    )

    message = (
        f"Рейтинг товара «{target_title}» {direction_text}: "
        f"{previous_rating} → {current_rating}."
    )

    new_value: dict[str, Any] = {
        "rating": str(current_rating),
    }

    if percent_change is not None:
        new_value["percent_change"] = str(
            percent_change.quantize(PERCENT_QUANTIZER)
        )

    return [
        AlertCandidate(
            alert_type=AlertType.RATING_CHANGED,
            severity=severity,
            title=title,
            message=message,
            old_value={
                "rating": str(previous_rating),
            },
            new_value=new_value,
            dedup_key=_build_dedup_key(
                snapshot=current_snapshot,
                alert_type=AlertType.RATING_CHANGED,
            ),
            change_percent=(
                abs(percent_change)
                if percent_change is not None
                else None
            ),
            change_absolute=abs(difference),
        )
    ]


def _detect_reviews_count_changes(
    *,
    previous_snapshot: ProductSnapshot,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    previous_reviews_count = previous_snapshot.reviews_count
    current_reviews_count = current_snapshot.reviews_count

    if (
        previous_reviews_count is None
        or current_reviews_count is None
    ):
        return []

    if previous_reviews_count == current_reviews_count:
        return []

    difference = (
        current_reviews_count - previous_reviews_count
    )
    percent_change = _calculate_percent_change(
        previous_value=Decimal(previous_reviews_count),
        current_value=Decimal(current_reviews_count),
    )

    if difference > 0:
        title = "Количество отзывов выросло"
        message_text = "выросло"
        severity = AlertSeverity.LOW
    else:
        title = "Количество отзывов снизилось"
        message_text = "снизилось"
        severity = AlertSeverity.MEDIUM

    target_title = _get_target_title(
        snapshot=current_snapshot,
    )

    message = (
        f"Количество отзывов у товара «{target_title}» "
        f"{message_text}: "
        f"{previous_reviews_count} → {current_reviews_count}."
    )

    new_value: dict[str, Any] = {
        "reviews_count": current_reviews_count,
    }

    if percent_change is not None:
        new_value["percent_change"] = str(
            percent_change.quantize(PERCENT_QUANTIZER)
        )

    return [
        AlertCandidate(
            alert_type=AlertType.REVIEWS_COUNT_CHANGED,
            severity=severity,
            title=title,
            message=message,
            old_value={
                "reviews_count": previous_reviews_count,
            },
            new_value=new_value,
            dedup_key=_build_dedup_key(
                snapshot=current_snapshot,
                alert_type=AlertType.REVIEWS_COUNT_CHANGED,
            ),
            change_percent=(
                abs(percent_change)
                if percent_change is not None
                else None
            ),
            change_absolute=Decimal(abs(difference)),
        )
    ]


def _detect_title_changes(
    *,
    previous_snapshot: ProductSnapshot,
    current_snapshot: ProductSnapshot,
) -> list[AlertCandidate]:
    previous_title = previous_snapshot.title.strip()
    current_title = current_snapshot.title.strip()

    if not previous_title or not current_title:
        return []

    if previous_title == current_title:
        return []

    message = (
        "Название товара изменилось: "
        f"«{previous_title}» → «{current_title}»."
    )

    return [
        AlertCandidate(
            alert_type=AlertType.TITLE_CHANGED,
            severity=AlertSeverity.MEDIUM,
            title="Название товара изменилось",
            message=message,
            old_value={
                "title": previous_title,
            },
            new_value={
                "title": current_title,
            },
            dedup_key=_build_dedup_key(
                snapshot=current_snapshot,
                alert_type=AlertType.TITLE_CHANGED,
            ),
        )
    ]


def _calculate_percent_change(
    *,
    previous_value: Decimal,
    current_value: Decimal,
) -> Decimal | None:
    if previous_value == 0:
        return None

    return (
        (current_value - previous_value)
        / previous_value
        * Decimal("100.00")
    )


def _get_target_title(
    *,
    snapshot: ProductSnapshot,
) -> str:
    return (
        snapshot.title
        or snapshot.target.title
        or snapshot.target.url
    )


def _build_dedup_key(
    *,
    snapshot: ProductSnapshot,
    alert_type: str,
) -> str:
    return (
        f"{snapshot.target_id}:"
        f"{alert_type}:"
        f"{snapshot.id}"
    )
