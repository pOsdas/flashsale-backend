import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from ozon_browser_fetcher.app.models.product import Product
from ozon_browser_fetcher.app.parsers.product_parser import (
    clean_title,
    extract_product_id,
    is_antibot_url,
    is_meaningful_title,
    parse_price_to_cents,
)


DEFAULT_CURRENCY = "RUB"

BAD_TITLE_MARKERS = {
    "в корзину",
    "доставка",
    "завтра",
    "послезавтра",
    "сегодня",
    "скидка",
    "бестселлер",
    "реклама",
    "осталось",
    "осталась",
    "остался",
    "отзывы",
    "отзыв",
    "оценка",
    "рейтинг",
    "ozon",
    "узнать больше",
    "посмотреть",
    "купить",
    "рассрочка",
    "кешбэк",
    "кэшбэк",
}

BAD_TITLE_EXACT_VALUES = {
    "распродажа",
    "оригинал",
    "вау-цены",
    "хит",
    "хит продаж",
    "лучшая цена",
    "суперцена",
    "новинка",
    "товар партнера",
    "осталась 1 шт",
    "остался 1 шт",
    "осталось 2 шт",
    "осталось 3 шт",
    "осталось 4 шт",
    "осталось 5 шт",
}


def normalize_ozon_url(url: str) -> str:
    url = url.strip()

    if url.startswith("https://www.ozon.ru"):
        return url

    if url.startswith("https://ozon.ru"):
        return url.replace("https://ozon.ru", "https://www.ozon.ru", 1)

    if url.startswith("/"):
        return "https://www.ozon.ru" + url

    return url


def extract_product_path(product_url: str) -> str:
    try:
        return urlparse(product_url).path
    except Exception:
        return ""


def normalize_slug_word(word: str) -> str:
    common_words = {
        "apple": "Apple",
        "iphone": "iPhone",
        "ipad": "iPad",
        "macbook": "MacBook",
        "samsung": "Samsung",
        "xiaomi": "Xiaomi",
        "redmi": "Redmi",
        "poco": "Poco",
        "oppo": "OPPO",
        "realme": "Realme",
        "iqoo": "iQOO",
        "honor": "Honor",
        "huawei": "Huawei",
        "smartfon": "смартфон",
        "chernyy": "черный",
        "belyy": "белый",
        "siniy": "синий",
        "zelenyy": "зеленый",
        "zolotoy": "золотой",
        "fioletovyy": "фиолетовый",
        "prozrachnyy": "прозрачный",
        "oranzhevyy": "оранжевый",
        "rozovyy": "розовый",
    }

    lowered = word.lower()

    if lowered in common_words:
        return common_words[lowered]

    if re.fullmatch(r"\d+", word):
        return word

    if len(word) <= 3:
        return word.upper()

    return word.capitalize()


def title_from_product_url(product_url: str) -> str:
    """
    Fallback-название из URL.

    Пример:
    /product/apple-smartfon-iphone-17-pro-esim-2-12-256-gb-esim-oranzhevyy-2835197971/
    ->
    Apple смартфон iPhone 17 Pro Esim 2 12 256 GB Esim оранжевый
    """
    path = extract_product_path(product_url)

    if not path:
        return ""

    parts = [part for part in path.split("/") if part]

    if len(parts) < 2:
        return ""

    slug = parts[-1]
    slug = unquote(slug)

    product_id = extract_product_id(product_url)

    if product_id and slug.endswith("-" + product_id):
        slug = slug[:-(len(product_id) + 1)]

    slug = re.sub(r"[^a-zA-Zа-яА-Я0-9\-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")

    if not slug:
        return ""

    words = [
        normalize_slug_word(word)
        for word in slug.split("-")
        if word.strip()
    ]

    title = " ".join(words)
    title = clean_title(title)

    if is_meaningful_title(title):
        return title

    return ""


def is_bad_title_line(line: str) -> bool:
    line = line.strip()

    if not line:
        return True

    lowered = line.lower().strip()

    if lowered in BAD_TITLE_EXACT_VALUES:
        return True

    if "₽" in line:
        return True

    if re.fullmatch(r"[\d\s.,]+", line):
        return True

    if re.fullmatch(r"остал[а-я]+\s+\d+\s+шт\.?", lowered):
        return True

    for marker in BAD_TITLE_MARKERS:
        if marker in lowered:
            return True

    return False


def split_text_lines(text: str) -> List[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]


def choose_title_from_raw_item(raw_item: Dict[str, Any]) -> str:
    product_url = normalize_ozon_url(str(raw_item.get("href") or ""))

    candidates: List[str] = []

    for key in ["title_attr", "img_alt", "title_text", "card_text"]:
        value = str(raw_item.get(key) or "").strip()

        if not value:
            continue

        for line in split_text_lines(value):
            line = clean_title(line)

            if is_bad_title_line(line):
                continue

            if is_meaningful_title(line):
                candidates.append(line)

    if candidates:
        candidates = sorted(
            candidates,
            key=lambda item: len(item),
            reverse=True,
        )

        return candidates[0]

    return title_from_product_url(product_url)


def extract_price_cents_from_card_text(text: str) -> int:
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


def extract_rating_from_card_text(text: str) -> float:
    patterns = [
        r"(\d[,.]\d)\s*(?:из\s*5|/5)",
        r"рейтинг[:\s]+(\d[,.]\d)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if not match:
            continue

        value = match.group(1).replace(",", ".")

        try:
            return float(value)
        except ValueError:
            continue

    return 0.0


def extract_reviews_count_from_card_text(text: str) -> int:
    patterns = [
        r"(\d[\d\s]*)\s+отзыв",
        r"(\d[\d\s]*)\s+оцен",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

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


def validate_listing_product(product: Product) -> bool:
    if not product.sku:
        return False

    if not is_meaningful_title(product.title):
        return False

    if is_bad_title_line(product.title):
        return False

    if product.price_cents <= 0:
        return False

    if product.available not in (0, 1):
        return False

    return True


def raw_item_to_product(raw_item: Dict[str, Any]) -> Optional[Product]:
    product_url = normalize_ozon_url(str(raw_item.get("href") or ""))

    if "/product/" not in product_url:
        return None

    sku = extract_product_id(product_url)

    if not sku:
        return None

    card_text = str(raw_item.get("card_text") or "")
    title = choose_title_from_raw_item(raw_item)
    price_cents = extract_price_cents_from_card_text(card_text)

    product = Product(
        sku=sku,
        title=title,
        seller_name="",
        brand="",
        price_cents=price_cents,
        currency=DEFAULT_CURRENCY,
        available=1,
        is_active=True,
        rating=extract_rating_from_card_text(card_text),
        reviews_count=extract_reviews_count_from_card_text(card_text),
        url=product_url,
        product_path=extract_product_path(product_url),
    )

    if not validate_listing_product(product):
        return None

    return product


def collect_raw_listing_items(page: Page) -> List[Dict[str, Any]]:
    script = """
    () => {
        const results = [];
        const seen = new Set();

        const anchors = Array.from(document.querySelectorAll('a[href*="/product/"]'));

        for (const anchor of anchors) {
            try {
                const rawHref = anchor.href || anchor.getAttribute("href") || "";

                if (!rawHref || !rawHref.includes("/product/")) {
                    continue;
                }

                const url = new URL(rawHref, window.location.origin);
                const pathKey = url.pathname;

                if (seen.has(pathKey)) {
                    continue;
                }

                seen.add(pathKey);

                const titleText = (anchor.innerText || "").trim();
                const titleAttr = (
                    anchor.getAttribute("title") ||
                    anchor.getAttribute("aria-label") ||
                    ""
                ).trim();

                let imgAlt = "";
                const localImg = anchor.querySelector("img[alt]");

                if (localImg) {
                    imgAlt = (localImg.getAttribute("alt") || "").trim();
                }

                let card = anchor;

                for (let depth = 0; depth < 10 && card; depth++) {
                    const text = (card.innerText || "").trim();

                    if (
                        text &&
                        text.includes("₽") &&
                        text.length < 3000
                    ) {
                        break;
                    }

                    card = card.parentElement;
                }

                if (!card) {
                    card = anchor.parentElement || anchor;
                }

                if (!imgAlt) {
                    const cardImg = card.querySelector("img[alt]");

                    if (cardImg) {
                        imgAlt = (cardImg.getAttribute("alt") || "").trim();
                    }
                }

                let cardText = (card.innerText || "").trim();

                if (!cardText) {
                    cardText = titleText;
                }

                results.push({
                    href: url.href,
                    title_text: titleText,
                    title_attr: titleAttr,
                    img_alt: imgAlt,
                    card_text: cardText,
                });
            } catch (error) {
                continue;
            }
        }

        return results;
    }
    """

    try:
        items = page.evaluate(script)
    except Exception:
        return []

    if not isinstance(items, list):
        return []

    return items


def deduplicate_products(products: List[Product]) -> List[Product]:
    result: List[Product] = []
    seen_sku = set()

    for product in products:
        if product.sku in seen_sku:
            continue

        seen_sku.add(product.sku)
        result.append(product)

    return result


def wait_listing_loaded(page: Page, url: str) -> None:
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=30_000,
    )

    if is_antibot_url(page.url):
        raise RuntimeError(f"ozon blocked by antibot page: current_url={page.url}")

    try:
        page.wait_for_selector(
            'a[href*="/product/"]',
            timeout=8_000,
        )
    except PlaywrightTimeoutError:
        pass


def parse_listing_from_page(page: Page, url: str, limit: int = 10) -> List[Product]:
    normalized_url = normalize_ozon_url(url)
    safe_limit = max(1, min(int(limit), 100))

    wait_listing_loaded(page, normalized_url)

    products: List[Product] = []

    for _ in range(4):
        raw_items = collect_raw_listing_items(page)

        for raw_item in raw_items:
            product = raw_item_to_product(raw_item)

            if product is None:
                continue

            products.append(product)

        products = deduplicate_products(products)

        if len(products) >= safe_limit:
            return products[:safe_limit]

        page.mouse.wheel(0, 1600)
        page.wait_for_timeout(600)

    products = deduplicate_products(products)

    if not products:
        raise RuntimeError(f"no valid Ozon listing products found: url={normalized_url}")

    return products[:safe_limit]
