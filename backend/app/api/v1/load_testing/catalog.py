

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyntheticProduct:
    marketplace: str
    external_id: str
    url: str


def build_synthetic_product(index: int) -> SyntheticProduct:
    """Return a deterministic marketplace-valid URL for a fake product."""
    marketplace = "wb" if index % 2 == 0 else "ozon"
    external_id = f"lt-{marketplace}-{index:08d}"

    if marketplace == "wb":
        url = (
            "https://www.wildberries.ru/catalog/"
            f"{10_000_000 + index}/detail.aspx"
        )
    else:
        url = (
            "https://www.ozon.ru/product/"
            f"load-test-product-{index}-{20_000_000 + index}/"
        )

    return SyntheticProduct(
        marketplace=marketplace,
        external_id=external_id,
        url=url,
    )


def product_index_for_target(
    *,
    target_index: int,
    total_targets: int,
    popular_products: int,
    medium_products: int,
) -> int:
    """Create a hot/warm/cold product distribution.

    50% of targets share a small hot set, 30% share a medium set and the
    remaining 20% are unique. This makes cache hit rate and refresh-lock
    behavior visible during the same run.
    """
    if total_targets <= 0:
        return target_index

    hot_boundary = int(total_targets * 0.50)
    warm_boundary = int(total_targets * 0.80)

    if target_index < hot_boundary:
        return target_index % max(popular_products, 1)

    if target_index < warm_boundary:
        return popular_products + (
            (target_index - hot_boundary) % max(medium_products, 1)
        )

    return popular_products + medium_products + (
        target_index - warm_boundary
    )
