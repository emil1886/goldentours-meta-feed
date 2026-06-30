#!/usr/bin/env python3
"""
Golden Tours -> Meta (Facebook/Instagram) catalogue feed builder. GBP / UK edition.

Crawls the HTML sitemap, visits each product page, extracts feed fields from the
server-rendered markup, and writes a Meta catalogue CSV (primary) + RSS/XML.

WHY THIS VERSION
The site shows prices in the visitor's local currency. From a UK connection it
serves GBP (£); from elsewhere it may serve USD ($). Meta rejects items whose
feed price doesn't match the landing page, so the feed MUST be built from the
GBP render of the site. This script targets GBP by default and refuses to stay
quiet if a page comes back in the wrong currency.

GETTING GBP TO STICK
The confirmed cookie is curr=GBP. Pass it on the command line:
    python goldentours_meta_feed_gbp.py --cookie curr=GBP
If the currency guard warns about "$", the cookie name/value is wrong.

UK ENGLISH
Titles and descriptions are taken verbatim from the live UK site.

USAGE
    pip install requests beautifulsoup4 lxml
    python goldentours_meta_feed_gbp.py --cookie curr=GBP --out-dir ./out
    python goldentours_meta_feed_gbp.py --cookie curr=GBP --limit 25
"""

import argparse
import csv
import re
import sys
import time
import xml.sax.saxutils as sax
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.goldentours.com"
SITEMAP = f"{BASE}/sitemap"
BRAND = "Golden Tours"

# Feed carries the starting-from price only (no "was"/sale_price), matching the
# headline "From £X" price shown on each product page.

# Symbol expected for each target currency, used by the currency guard.
CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FeedBuilder/1.0; +https://anicca.co.uk)"
}

NON_PRODUCT_PREFIXES = (
    "/travelblog", "/es/", "/it/", "/fr/", "/de/", "/cn/", "/br/",
)

# Single-segment URLs that are actually bookable products (extend as needed).
EXTRA_PRODUCT_URLS = [
    f"{BASE}/tower-of-london-tickets-and-tours",
    f"{BASE}/thames-cruises",
]

PRICE_FROM_RE = re.compile(r"From\s*([\$£€])\s*([\d,]+\.?\d*)", re.I)
PRICE_WAS_RE = re.compile(r"was\s*([\$£€])\s*([\d,]+\.?\d*)", re.I)
ACTIVITY_RE = re.compile(r"Activity code[:\s]*</strong>?\s*([A-Z0-9]+)", re.I)
ACTIVITY_RE2 = re.compile(r"Activity code[:\s]+([A-Z0-9]{2,})", re.I)


def get(url, session, cookies):
    r = session.get(url, headers=HEADERS, cookies=cookies, timeout=30)
    r.raise_for_status()
    return r.text


def discover_product_urls(session, cookies):
    soup = BeautifulSoup(get(SITEMAP, session, cookies), "lxml")
    urls = set(EXTRA_PRODUCT_URLS)
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if not href.startswith(BASE):
            continue
        path = urlparse(href).path
        if any(path.startswith(p) for p in NON_PRODUCT_PREFIXES):
            continue
        segments = [s for s in path.split("/") if s]
        if len(segments) == 2:
            urls.add(href.split("?")[0].split("#")[0])
    return sorted(urls)


def meta_tag(soup, prop=None, name=None):
    tag = soup.find("meta", attrs={"property": prop}) if prop \
        else soup.find("meta", attrs={"name": name})
    return tag["content"].strip() if tag and tag.get("content") else ""


def parse_product(url, raw):
    soup = BeautifulSoup(raw, "lxml")

    title = meta_tag(soup, prop="og:title") or (soup.h1.get_text(strip=True) if soup.h1 else "")
    title = re.sub(r"\s*-\s*Golden Tours\s*$", "", title).strip()[:200]
    image = meta_tag(soup, prop="og:image")
    description = meta_tag(soup, name="description") or meta_tag(soup, prop="og:description")
    canonical = meta_tag(soup, prop="og:url") or url

    text = soup.get_text(" ", strip=True)
    from_m = PRICE_FROM_RE.search(text)
    was_m = PRICE_WAS_RE.search(text)
    if not from_m:
        return None  # no price -> not a bookable product, skip

    symbol = from_m.group(1)
    from_price = from_m.group(2).replace(",", "")
    was_price = was_m.group(2).replace(",", "") if was_m else None

    ac = ACTIVITY_RE.search(raw) or ACTIVITY_RE2.search(text)
    slug = urlparse(canonical).path.rstrip("/").split("/")[-1]
    pid = (ac.group(1) if ac else slug)[:100]

    seg1 = urlparse(canonical).path.strip("/").split("/")[0]
    product_type = seg1.replace("-", " ").title()

    return {
        "id": pid, "title": title, "description": description,
        "link": canonical, "image_link": image, "symbol": symbol,
        "from_price": from_price, "was_price": was_price,
        "product_type": product_type,
    }


def price_value(p, currency):
    return f"{p['from_price']} {currency}"


def build_items(currency, session, cookies, limit=None):
    candidates = discover_product_urls(session, cookies)
    if limit:
        candidates = candidates[:limit]
    items, wrong_currency = [], 0
    want_symbol = CURRENCY_SYMBOL.get(currency.upper(), "")
    for i, url in enumerate(candidates, 1):
        try:
            p = parse_product(url, get(url, session, cookies))
            if p:
                if want_symbol and p["symbol"] != want_symbol:
                    wrong_currency += 1
                items.append(p)
                print(f"[{i}/{len(candidates)}] OK {p['symbol']} {p['id']}  {p['title'][:50]}",
                      file=sys.stderr)
            else:
                print(f"[{i}/{len(candidates)}] skip (no price)  {url}", file=sys.stderr)
        except Exception as e:
            print(f"[{i}/{len(candidates)}] ERR  {url}  {e}", file=sys.stderr)
        time.sleep(0.4)

    if want_symbol and wrong_currency:
        print(f"\n*** CURRENCY WARNING: {wrong_currency}/{len(items)} pages were NOT in "
              f"'{want_symbol}' ({currency}). The currency cookie did not take effect. "
              f"Do NOT upload this feed - re-check --cookie. ***\n", file=sys.stderr)
    return items


def write_csv(items, currency, path):
    cols = ["id", "title", "description", "availability", "condition",
            "price", "link", "image_link", "brand", "product_type"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for p in items:
            w.writerow({
                "id": p["id"], "title": p["title"],
                "description": (p["description"] or "").replace("\n", " "),
                "availability": "in stock", "condition": "new",
                "price": price_value(p, currency), "link": p["link"],
                "image_link": p["image_link"], "brand": BRAND,
                "product_type": p["product_type"],
            })


def write_xml(items, currency, path):
    esc = lambda s: sax.escape(s or "")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">',
           "<channel>", f"<title>{BRAND} Catalogue Feed</title>",
           f"<link>{BASE}</link>",
           "<description>Tours, attraction tickets and experiences</description>"]
    for p in items:
        out += ["<item>", f"<g:id>{esc(p['id'])}</g:id>",
                f"<g:title>{esc(p['title'])}</g:title>",
                f"<g:description>{esc(p['description'])}</g:description>",
                f"<g:link>{esc(p['link'])}</g:link>",
                f"<g:image_link>{esc(p['image_link'])}</g:image_link>",
                "<g:availability>in stock</g:availability>",
                "<g:condition>new</g:condition>", f"<g:brand>{BRAND}</g:brand>",
                f"<g:price>{price_value(p, currency)}</g:price>",
                f"<g:product_type>{esc(p['product_type'])}</g:product_type>", "</item>"]
    out += ["</channel>", "</rss>"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def parse_cookies(pairs):
    cookies = {}
    for pair in pairs or []:
        if "=" not in pair:
            sys.exit(f"--cookie must be name=value, got: {pair}")
        k, v = pair.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--currency", default="GBP")
    ap.add_argument("--cookie", action="append",
                    help="currency cookie as name=value (repeatable)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--limit", type=int, default=None, help="cap pages (testing)")
    ap.add_argument("--strict-currency", action="store_true",
                    help="exit non-zero (don't write) if any page is in the wrong currency")
    ap.add_argument("--min-products", type=int, default=0,
                    help="exit non-zero if fewer than this many products were extracted")
    args = ap.parse_args()

    cookies = parse_cookies(args.cookie)
    if args.currency.upper() == "GBP" and not cookies:
        print("Note: targeting GBP but no --cookie given. If pages come back in $, "
              "set the currency cookie (see header comment).", file=sys.stderr)

    session = requests.Session()
    items = build_items(args.currency, session, cookies, args.limit)
    print(f"\nExtracted {len(items)} products", file=sys.stderr)

    # Guards: fail loudly BEFORE writing, so a scheduled job never publishes a bad feed.
    want_symbol = CURRENCY_SYMBOL.get(args.currency.upper(), "")
    mismatches = sum(1 for p in items if want_symbol and p["symbol"] != want_symbol)
    if args.strict_currency and mismatches:
        sys.exit(f"ABORT: {mismatches}/{len(items)} products not in {args.currency}. "
                 f"Feed NOT written. Fix the --cookie value.")
    if args.min_products and len(items) < args.min_products:
        sys.exit(f"ABORT: only {len(items)} products (< --min-products {args.min_products}). "
                 f"Feed NOT written - the site or sitemap may have changed.")

    base = args.out_dir.rstrip("/")
    write_csv(items, args.currency, f"{base}/goldentours_meta_feed.csv")
    write_xml(items, args.currency, f"{base}/goldentours_meta_feed.xml")
    print(f"Wrote {base}/goldentours_meta_feed.csv\nWrote {base}/goldentours_meta_feed.xml",
          file=sys.stderr)


if __name__ == "__main__":
    main()
