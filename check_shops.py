#!/usr/bin/env python3
"""
Shop new-product watcher.

- Reads a list of shops from shops.json
- For each shop, fetches the page and extracts the list of products currently shown
- Compares against the last-seen list (stored in state/<shop_id>.json)
- Sends a push notification (via ntfy.sh) for every product that's new
- Saves the updated list back to state/<shop_id>.json

Designed to run unattended (e.g. every 15 min via GitHub Actions).
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

STATE_DIR = Path("state")
SHOPS_FILE = Path("shops.json")

# Anything matching these (case-insensitive substrings) gets flagged as
# high-priority: a louder ntfy notification (max priority + different tag)
# instead of the normal one. Tune this list freely as more product names
# for the 30th Celebration set become known.
PRIORITY_KEYWORDS_FILE = Path("priority_keywords.json")


def load_priority_keywords():
    if PRIORITY_KEYWORDS_FILE.exists():
        return [k.lower() for k in json.loads(PRIORITY_KEYWORDS_FILE.read_text())]
    return []


PRIORITY_KEYWORDS = load_priority_keywords()


def is_priority(product_name: str) -> bool:
    name = product_name.lower()
    return any(kw in name for kw in PRIORITY_KEYWORDS)

# ntfy topic comes from an environment variable / GitHub secret, never hardcoded
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# games-island.eu asks bots/AI systems to identify themselves honestly instead
# of pretending to be a browser, and to use their dedicated crawlme.* feed
# rather than scraping the normal site. We respect both.
GAMES_ISLAND_HEADERS = {
    "User-Agent": "ShopMonitorBot/1.0 (personal-use notifier; run by an individual for private stock alerts)"
}


def extract_products(html: str, base_url: str):
    """
    Try, in order of reliability:
      1. JSON-LD structured data (schema.org Product / ItemList) - most shops
         embed this for SEO, and it gives us clean name+url pairs.
      2. A generic fallback: anchor tags that look like product links.

    Returns a dict of {product_url: product_name}
    """
    soup = BeautifulSoup(html, "html.parser")
    products = {}

    # --- Attempt 1: JSON-LD ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            item_list = entry.get("itemListElement")
            if isinstance(item_list, list):
                for li in item_list:
                    item = li.get("item", li) if isinstance(li, dict) else {}
                    url = item.get("url") or li.get("url")
                    name = item.get("name") or li.get("name")
                    if url and name:
                        products[url.strip()] = name.strip()
            elif entry.get("@type") == "Product":
                url = entry.get("url")
                name = entry.get("name")
                if url and name:
                    products[url.strip()] = name.strip()

    if products:
        return products

    # --- Attempt 2: generic fallback ---
    # Look for anchors whose href stays on the same domain and whose text
    # looks like a real product title (long enough, not a nav/footer link).
    blacklist_words = [
        "kontakt", "agb", "datenschutz", "impressum", "widerruf", "sitemap",
        "newsletter", "warenkorb", "wunschliste", "anmelden", "registrieren",
        "faq", "versand", "karriere", "sortierung", "trustpilot",
    ]
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not text or len(text) < 8:
            continue
        if any(b in href.lower() for b in blacklist_words):
            continue
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        if "?" in href:  # skip sort/filter/pagination links
            continue
        full_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        products.setdefault(full_url, text)

    return products


def extract_products_shopify(collection_url: str):
    """
    Shopify exposes a clean JSON feed for any collection page:
        https://shop.com/collections/<handle>/products.json

    We derive <handle> from the given URL (keeping only the
    /collections/<handle> portion, dropping any extra path segments like a
    tag filter) and fetch that instead of scraping HTML.
    Returns a dict of {product_url: product_name}.
    """
    from urllib.parse import urlparse

    parsed = urlparse(collection_url)
    parts = [p for p in parsed.path.split("/") if p]
    # find "collections" and keep exactly the next segment (the handle)
    try:
        idx = parts.index("collections")
        handle = parts[idx + 1]
    except (ValueError, IndexError):
        raise ValueError(f"Could not find a collection handle in {collection_url}")

    base = f"{parsed.scheme}://{parsed.netloc}"
    json_url = f"{base}/collections/{handle}/products.json?limit=250"

    resp = requests.get(json_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    products = {}
    for p in data.get("products", []):
        handle_slug = p.get("handle")
        title = p.get("title")
        if handle_slug and title:
            products[f"{base}/products/{handle_slug}"] = title
    return products


def extract_products_games_island(category_url: str):
    """
    games-island.eu explicitly asks AI/bots to use their machine-readable
    mirror at crawlme.games-island.eu instead of scraping the normal site,
    and to identify themselves in the User-Agent rather than impersonate a
    browser. Paths are identical between the two hosts.
    """
    crawl_url = category_url.replace("games-island.eu", "crawlme.games-island.eu")
    resp = requests.get(crawl_url, headers=GAMES_ISLAND_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    products = {}
    items = data if isinstance(data, list) else data.get("products", [])
    for item in items:
        url = item.get("url") or item.get("link")
        name = item.get("name") or item.get("title")
        if url and name:
            # always point the human-facing link at the real site, per their policy
            url = url.replace("crawlme.games-island.eu", "games-island.eu")
            products[url] = name
    return products


def send_notification(topic: str, title: str, message: str, url: str = None, urgent: bool = False):
    if not topic:
        print("No NTFY_TOPIC set, skipping notification. Message was:")
        print(title, "-", message)
        return
    headers = {"Title": title.encode("utf-8")}
    if url:
        headers["Click"] = url
    if urgent:
        headers["Priority"] = "urgent"
        headers["Tags"] = "rotating_light"
    resp = requests.post(
        f"{NTFY_SERVER}/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()


def load_state(shop_id: str):
    path = STATE_DIR / f"{shop_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(shop_id: str, data: dict):
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{shop_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def check_shop(shop: dict):
    shop_id = shop["id"]
    name = shop["name"]
    url = shop["url"]

    platform = shop.get("platform", "generic")
    print(f"Checking {name} ({url}) [{platform}] ...")

    if platform == "shopify":
        current = extract_products_shopify(url)
    elif platform == "games_island":
        current = extract_products_games_island(url)
    else:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        current = extract_products(resp.text, url)
    if not current:
        print(f"  WARNING: no products detected for {name}. Page structure may need adjusting.")
        return

    previous = load_state(shop_id)
    is_first_run = len(previous) == 0

    new_urls = [u for u in current if u not in previous]

    if is_first_run:
        print(f"  First run for {name}: saving {len(current)} products as baseline, no notifications sent.")
    elif new_urls:
        print(f"  {len(new_urls)} new product(s) found for {name}!")

        priority_urls = [u for u in new_urls if is_priority(current[u])]
        normal_urls = [u for u in new_urls if u not in priority_urls]

        if priority_urls:
            print(f"  🚨 {len(priority_urls)} of those match a priority keyword!")
            for u in priority_urls:
                send_notification(
                    NTFY_TOPIC,
                    title=f"🚨 PRIORITY: {name}",
                    message=current[u],
                    url=u,
                    urgent=True,
                )

        if len(normal_urls) == 1:
            u = normal_urls[0]
            send_notification(
                NTFY_TOPIC,
                title=f"🆕 New at {name}",
                message=current[u],
                url=u,
            )
        elif normal_urls:
            names = "\n".join(f"• {current[u]}" for u in normal_urls[:15])
            more = f"\n(+{len(normal_urls) - 15} more)" if len(normal_urls) > 15 else ""
            send_notification(
                NTFY_TOPIC,
                title=f"🆕 {len(normal_urls)} new items at {name}",
                message=names + more,
                url=url,
            )
    else:
        print(f"  No new products for {name}.")

    save_state(shop_id, current)


def main():
    shops_file = Path(sys.argv[1]) if len(sys.argv) > 1 else SHOPS_FILE
    if not shops_file.exists():
        print(f"{shops_file} not found.", file=sys.stderr)
        sys.exit(1)

    if PRIORITY_KEYWORDS:
        print(f"Loaded {len(PRIORITY_KEYWORDS)} priority keywords.")
    else:
        print("No priority_keywords.json found or it's empty — no urgent alerts will fire.")

    shops = json.loads(shops_file.read_text())
    for shop in shops:
        try:
            check_shop(shop)
        except Exception as e:
            print(f"  ERROR checking {shop.get('name', shop.get('id'))}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
