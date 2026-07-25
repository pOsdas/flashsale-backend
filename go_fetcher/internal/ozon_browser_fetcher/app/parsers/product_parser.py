import json
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from ozon_browser_fetcher.app.models.product import Product


DEFAULT_CURRENCY = "RUB"

TECHNICAL_TITLES = {
    "default",
    "layout",
    "layout.column",
    "layout.row",
    "column",
    "row",
    "container",
    "widget",
    "section",
    "item",
    "page",
    "root",
}

BAD_SELLER_VALUES = {
    "продавец",
    "магазин",
    "подписаться",
    "вы подписаны",
    "отписаться",
    "перейти в магазин",
    "о магазине",
    "реквизиты",
    "спросить продавца",
    "спросить продавца о товаре",
    "написать продавцу",
    "чат с продавцом",
    "задать вопрос",
    "задать вопрос продавцу",
    "поделиться",
    "добавить в избранное",
    "ozon",
    "ozon fresh",
}


def extract_product_id(url: str) -> str:
    url = url.strip()

    patterns = [
        r"/product/[^/?#]*?-(\d+)(?:[/?#]|$)",
        r"/product/(\d+)(?:[/?#]|$)",
        r"-(\d+)(?:[/?#]|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)

        if match:
            return match.group(1)

    return ""


def normalize_url(url: str) -> str:
    url = url.strip()

    if url.startswith("https://www.ozon.ru"):
        return url

    if url.startswith("https://ozon.ru"):
        return url.replace("https://ozon.ru", "https://www.ozon.ru", 1)

    if url.startswith("/product/"):
        return "https://www.ozon.ru" + url

    return url


def is_meaningful_title(title: str) -> bool:
    title = title.strip()

    if not title:
        return False

    lowered = title.lower().strip()

    if lowered in TECHNICAL_TITLES:
        return False

    if lowered.startswith("layout."):
        return False

    if lowered.startswith("web") and "widget" in lowered:
        return False

    if len(title) < 8:
        return False

    if not re.search(r"[a-zA-Zа-яА-Я]", title):
        return False

    return True


def clean_title(title: str) -> str:
    title = title.strip()

    if not title:
        return ""

    title = re.sub(r"\s+", " ", title)

    split_markers = [
        " купить",
        " цена",
        " — купить",
        " - купить",
        " | Ozon",
        " | OZON",
        " OZON",
    ]

    for marker in split_markers:
        index = title.lower().find(marker.lower())

        if index > 0:
            title = title[:index].strip()

    return title


def clean_seller_name(value: str) -> str:
    value = value.strip()

    if not value:
        return ""

    raw_lines = [line.strip() for line in value.splitlines() if line.strip()]

    if len(raw_lines) > 1:
        for line in raw_lines:
            cleaned_line = clean_seller_name(line)

            if cleaned_line:
                return cleaned_line

        return ""

    value = re.sub(r"\s+", " ", value).strip()

    if not value:
        return ""

    lowered = value.lower().strip()

    if lowered in BAD_SELLER_VALUES:
        return ""

    if "подпис" in lowered:
        return ""

    if "спросить" in lowered and "продав" in lowered:
        return ""

    if "₽" in value:
        return ""

    if len(value) < 2:
        return ""

    if len(value) > 80:
        return ""

    return value


def safe_inner_text(page: Page, selector: str, timeout: int = 700) -> str:
    try:
        return page.locator(selector).first.inner_text(timeout=timeout).strip()
    except Exception:
        return ""


def safe_attr(page: Page, selector: str, attr: str, timeout: int = 700) -> str:
    try:
        value = page.locator(selector).first.get_attribute(attr, timeout=timeout)

        if value:
            return value.strip()

        return ""
    except Exception:
        return ""


def is_antibot_url(url: str) -> bool:
    lowered_url = url.lower()

    markers = [
        "challenge.html",
        "block.html",
    ]

    return any(marker in lowered_url for marker in markers)


def is_antibot_page(page: Page, body_text: str) -> bool:
    current_url = page.url.lower()
    lower_body = body_text.lower()

    markers = [
        "challenge.html",
        "block.html",
        "incidentid",
        "supporturl",
        "blockurl",
        "доступ ограничен",
        "проверяем ваш браузер",
        "security check",
    ]

    for marker in markers:
        if marker in current_url or marker in lower_body:
            return True

    return False


def parse_price_to_cents(raw_price: str) -> int:
    digits = re.sub(r"[^\d]", "", raw_price)

    if not digits:
        return 0

    try:
        return int(digits) * 100
    except ValueError:
        return 0


def extract_price_cents_from_text(text: str) -> int:
    patterns = [
        r"(\d[\d\s]{1,12})\s*₽",
        r"(\d[\d\s]{1,12})\s*руб",
    ]

    candidates: List[int] = []

    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)

        for match in matches:
            price_cents = parse_price_to_cents(match)

            if 10_00 <= price_cents <= 10_000_000_00:
                candidates.append(price_cents)

    if not candidates:
        return 0

    return candidates[0]


def extract_rating(body_text: str) -> float:
    patterns = [
        r"(\d[,.]\d)\s*(?:из\s*5|/5)",
        r"рейтинг[:\s]+(\d[,.]\d)",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text, flags=re.IGNORECASE)

        if not match:
            continue

        value = match.group(1).replace(",", ".")

        try:
            return float(value)
        except ValueError:
            continue

    return 0.0


def extract_reviews_count(body_text: str) -> int:
    patterns = [
        r"(\d[\d\s]*)\s+отзыв",
        r"(\d[\d\s]*)\s+оцен",
    ]

    for pattern in patterns:
        match = re.search(pattern, body_text, flags=re.IGNORECASE)

        if not match:
            continue

        value = re.sub(r"[^\d]", "", match.group(1))

        if not value:
            continue

        try:
            return int(value)
        except ValueError:
            continue

    return 0


def extract_available(body_text: str) -> int:
    lower_body = body_text.lower()

    unavailable_markers = [
        "нет в наличии",
        "товар закончился",
        "не продаётся",
        "снят с продажи",
        "страница не найдена",
    ]

    for marker in unavailable_markers:
        if marker in lower_body:
            return 0

    return 1


def safe_json_loads(value: str) -> Optional[Any]:
    value = value.strip()

    if not value:
        return None

    try:
        return json.loads(value)
    except Exception:
        return None


def extract_json_ld_product(page: Page) -> Optional[Dict[str, Any]]:
    locator = page.locator('script[type="application/ld+json"]')

    try:
        count = locator.count()
    except Exception:
        return None

    for index in range(count):
        try:
            raw = locator.nth(index).text_content(timeout=500) or ""
        except Exception:
            continue

        payload = safe_json_loads(raw)

        if payload is None:
            continue

        items = payload if isinstance(payload, list) else [payload]

        for item in items:
            if not isinstance(item, dict):
                continue

            item_type = item.get("@type")

            if item_type == "Product":
                return item

    return None


def extract_seller_from_offers(offers: Any) -> str:
    if isinstance(offers, list) and offers:
        offers = offers[0]

    if not isinstance(offers, dict):
        return ""

    seller = offers.get("seller")

    if isinstance(seller, dict):
        return clean_seller_name(str(seller.get("name", "")))

    if isinstance(seller, str):
        return clean_seller_name(seller)

    return ""


def extract_price_from_offers(offers: Any) -> int:
    if isinstance(offers, list) and offers:
        offers = offers[0]

    if not isinstance(offers, dict):
        return 0

    price = str(offers.get("price", ""))

    if not price:
        return 0

    try:
        return int(float(price.replace(",", ".")) * 100)
    except ValueError:
        return 0


def extract_available_from_offers(offers: Any) -> int:
    if isinstance(offers, list) and offers:
        offers = offers[0]

    if not isinstance(offers, dict):
        return 1

    availability = str(offers.get("availability", "")).lower()

    if "outofstock" in availability:
        return 0

    return 1


def extract_brand_from_json_ld(data: Dict[str, Any]) -> str:
    raw_brand = data.get("brand")

    if isinstance(raw_brand, dict):
        return str(raw_brand.get("name", "")).strip()

    if isinstance(raw_brand, str):
        return raw_brand.strip()

    return ""


def extract_rating_from_json_ld(data: Dict[str, Any]) -> float:
    aggregate_rating = data.get("aggregateRating")

    if not isinstance(aggregate_rating, dict):
        return 0.0

    try:
        return float(str(aggregate_rating.get("ratingValue", "0")).replace(",", "."))
    except ValueError:
        return 0.0


def extract_reviews_count_from_json_ld(data: Dict[str, Any]) -> int:
    aggregate_rating = data.get("aggregateRating")

    if not isinstance(aggregate_rating, dict):
        return 0

    try:
        return int(str(aggregate_rating.get("reviewCount", "0")))
    except ValueError:
        return 0


def extract_product_from_json_ld(page: Page, product_id: str) -> Optional[Product]:
    data = extract_json_ld_product(page)

    if not data:
        return None

    title = clean_title(str(data.get("name", "")))

    if not is_meaningful_title(title):
        return None

    offers = data.get("offers", {})

    seller_name = extract_seller_from_offers(offers)
    price_cents = extract_price_from_offers(offers)
    available = extract_available_from_offers(offers)
    brand = extract_brand_from_json_ld(data)
    rating = extract_rating_from_json_ld(data)
    reviews_count = extract_reviews_count_from_json_ld(data)

    return Product(
        sku=product_id,
        title=title,
        seller_name=seller_name,
        brand=brand,
        price_cents=price_cents,
        currency=DEFAULT_CURRENCY,
        available=available,
        is_active=True,
        rating=rating,
        reviews_count=reviews_count,
    )


def extract_seller_name_from_dom_fast(page: Page) -> str:
    selectors = [
        '[data-widget="webCurrentSeller"] a[href*="/seller/"]',
        '[data-widget="webCurrentSeller"] a[href*="/shop/"]',
        '[data-widget="webSeller"] a[href*="/seller/"]',
        '[data-widget="webSeller"] a[href*="/shop/"]',
        'a[href*="/seller/"]',
        'a[href*="/shop/"]',
    ]

    for selector in selectors:
        seller_name = clean_seller_name(
            safe_inner_text(
                page=page,
                selector=selector,
                timeout=800,
            )
        )

        if seller_name:
            return seller_name

    return ""


def enrich_seller_name(product: Product, page: Page) -> None:
    if product.seller_name:
        product.seller_name = clean_seller_name(product.seller_name)

    if not product.seller_name:
        product.seller_name = extract_seller_name_from_dom_fast(page)

    if not product.seller_name and product.brand:
        product.seller_name = clean_seller_name(product.brand)


def validate_product(product: Product, expected_sku: str) -> None:
    errors: List[str] = []

    product.sku = str(product.sku or "").strip()
    product.title = clean_title(str(product.title or ""))
    product.seller_name = clean_seller_name(str(product.seller_name or ""))
    product.brand = str(product.brand or "").strip()
    product.currency = str(product.currency or DEFAULT_CURRENCY).strip() or DEFAULT_CURRENCY

    if not product.sku:
        errors.append("sku is empty")

    if expected_sku and product.sku != expected_sku:
        errors.append(f"sku mismatch: expected={expected_sku}, got={product.sku}")

    if not is_meaningful_title(product.title):
        errors.append(f"title is invalid: {product.title!r}")

    if not isinstance(product.price_cents, int):
        errors.append(f"price_cents must be int: {product.price_cents!r}")
    elif product.price_cents <= 0:
        errors.append(f"price_cents must be positive: {product.price_cents}")

    if product.available not in (0, 1):
        errors.append(f"available must be 0 or 1: {product.available!r}")

    if not isinstance(product.is_active, bool):
        errors.append(f"is_active must be bool: {product.is_active!r}")

    if product.rating < 0:
        errors.append(f"rating must be non-negative: {product.rating}")

    if product.reviews_count < 0:
        errors.append(f"reviews_count must be non-negative: {product.reviews_count}")

    if errors:
        raise RuntimeError("invalid parsed Ozon product: " + "; ".join(errors))


def prepare_product_for_return(product: Product, expected_sku: str, page: Page) -> Product:
    enrich_seller_name(product, page)
    validate_product(product, expected_sku)

    return product


def extract_title_from_dom(page: Page, body_text: str) -> str:
    selectors = [
        "h1",
        '[data-widget="webProductHeading"] h1',
        '[data-widget="webProductHeading"]',
    ]

    for selector in selectors:
        title = clean_title(safe_inner_text(page, selector))

        if is_meaningful_title(title):
            return title

    meta_selectors = [
        'meta[property="og:title"]',
        'meta[name="title"]',
        'meta[property="twitter:title"]',
    ]

    for selector in meta_selectors:
        title = clean_title(safe_attr(page, selector, "content"))

        if is_meaningful_title(title):
            return title

    document_title = clean_title(page.title())

    if is_meaningful_title(document_title):
        return document_title

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]

    for line in lines[:80]:
        line = clean_title(line)

        if is_meaningful_title(line) and "₽" not in line:
            return line

    return ""


def extract_product_from_dom(page: Page, product_id: str, body_text: str) -> Product:
    if is_antibot_page(page, body_text):
        raise RuntimeError(f"ozon blocked by antibot page: current_url={page.url}")

    title = extract_title_from_dom(page, body_text)

    if not is_meaningful_title(title):
        raise RuntimeError(
            f"product title not found in DOM, got={title!r}, current_url={page.url}"
        )

    price_cents = extract_price_cents_from_text(body_text)
    rating = extract_rating(body_text)
    reviews_count = extract_reviews_count(body_text)
    available = extract_available(body_text)
    seller_name = extract_seller_name_from_dom_fast(page)

    return Product(
        sku=product_id,
        title=title,
        seller_name=seller_name,
        brand="",
        price_cents=price_cents,
        currency=DEFAULT_CURRENCY,
        available=available,
        is_active=True,
        rating=rating,
        reviews_count=reviews_count,
    )


def wait_page_loaded(page: Page, url: str) -> None:
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=30_000,
    )

    try:
        page.wait_for_selector(
            'script[type="application/ld+json"], h1, meta[property="og:title"]',
            timeout=2_000,
        )
    except PlaywrightTimeoutError:
        pass


def parse_product_from_page(page: Page, url: str) -> Product:
    normalized_url = normalize_url(url)
    product_id = extract_product_id(normalized_url)

    if not product_id:
        raise RuntimeError(f"failed to extract Ozon product id from url: {url}")

    wait_page_loaded(page, normalized_url)

    if is_antibot_url(page.url):
        raise RuntimeError(f"ozon blocked by antibot page: current_url={page.url}")

    json_ld_product = extract_product_from_json_ld(
        page=page,
        product_id=product_id,
    )

    if json_ld_product is not None and is_meaningful_title(json_ld_product.title):
        product = prepare_product_for_return(
            product=json_ld_product,
            expected_sku=product_id,
            page=page,
        )

        product.url = normalized_url
        product.product_path = extract_product_path_from_url(normalized_url)

        return product

    body_text = page.locator("body").inner_text(timeout=4_000)

    dom_product = extract_product_from_dom(
        page=page,
        product_id=product_id,
        body_text=body_text,
    )

    product = prepare_product_for_return(
        product=dom_product,
        expected_sku=product_id,
        page=page,
    )

    product.url = normalized_url
    product.product_path = extract_product_path_from_url(normalized_url)

    return product


def extract_product_path_from_url(url: str) -> str:
    try:
        return urlparse(url).path
    except Exception:
        return ""
