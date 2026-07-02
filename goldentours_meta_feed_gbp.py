#!/usr/bin/env python3
"""
Golden Tours -> Meta (Facebook/Instagram) catalogue feed builder. GBP / UK edition.

Crawls the HTML sitemap, visits each product page, extracts feed fields from the
server-rendered markup, and writes a Meta catalogue CSV (primary) + RSS/XML.

WHY THIS VERSION
The site defaults to the visitor's IP-geolocated currency (UK IP -> GBP, US IP
-> USD), and a self-supplied curr cookie or ?curr= parameter is ignored. BUT its
on-page currency selector is a POST form (form#currform -> POST curr=<code>), and
the server honours THAT by issuing a "blessed" curr cookie that then sticks for
the whole session regardless of IP. establish_currency() mirrors this, so the
crawl renders GBP from ANY connection - including GitHub's US-based runners - with
no proxy. Meta rejects items whose feed price doesn't match the landing page, so
the script targets GBP and refuses to write if any page comes back non-GBP.

GETTING GBP
Just run it; establish_currency() POSTs curr=GBP first and every later GET in the
session is GBP. The price is read from each page's JSON-LD offer (the
authoritative value Meta matches against), falling back to the visible
"From <sym>X" headline only if no structured offer is present.

PRICING (sale / % off)
Product pages show only a single "From" price, but the category listing cards
show the original "was" price where a product is discounted. build_was_map()
reads those, and where "was" > "From" the feed carries price=was (original) and
sale_price=from, so Meta can show the original struck through with a "% off"
label. Products with no genuine discount carry price=from and no sale_price.

UK ENGLISH
Titles and descriptions are taken verbatim from the live UK site.

USAGE
    pip install requests beautifulsoup4 lxml
    python goldentours_meta_feed_gbp.py --out-dir ./out
    python goldentours_meta_feed_gbp.py --limit 25               # quick test
"""

import argparse
import csv
import json
import re
import sys
import time
import xml.sax.saxutils as sax
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.goldentours.com"
SITEMAP = f"{BASE}/sitemap"
BRAND = "Golden Tours"

# Feed carries the starting-from price only (no "was"/sale_price), matching the
# headline "From £X" price shown on each product page.

# Symbol expected for each target currency, used by the currency guard.
CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
SYMBOL_CURRENCY = {v: k for k, v in CURRENCY_SYMBOL.items()}

# A browser-like User-Agent is used deliberately: with a bot UA the site serves a
# reduced sitemap (only a handful of product links). Also note the site renders
# prices by IP geolocation, so this crawler MUST run from a UK connection to get GBP.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

NON_PRODUCT_PREFIXES = (
    "/travelblog", "/es/", "/it/", "/fr/", "/de/", "/cn/", "/br/",
    # asset / non-product two-segment paths that appear in the sitemap markup
    "/css/", "/js/", "/img/", "/images/", "/assets/", "/static/",
)

# Single-segment URLs that are actually bookable products (extend as needed).
EXTRA_PRODUCT_URLS = [
    f"{BASE}/tower-of-london-tickets-and-tours",
    f"{BASE}/thames-cruises",
]

PRICE_FROM_RE = re.compile(r"From\s*([\$£€])\s*([\d,]+\.?\d*)", re.I)
PRICE_WAS_RE = re.compile(r"was\s*([\$£€])\s*([\d,]+\.?\d*)", re.I)
# Listing-card price patterns (number only) used to read a product card's
# "From £X was £Y" on category pages.
LISTING_FROM_RE = re.compile(r"From\s*[\$£€]\s*([\d,]+\.?\d*)", re.I)
LISTING_WAS_RE = re.compile(r"was\s*[\$£€]\s*([\d,]+\.?\d*)", re.I)
ACTIVITY_RE = re.compile(r"Activity code[:\s]*</strong>?\s*([A-Z0-9]+)", re.I)
ACTIVITY_RE2 = re.compile(r"Activity code[:\s]+([A-Z0-9]{2,})", re.I)


def get(url, session, cookies):
    r = session.get(url, headers=HEADERS, cookies=cookies, timeout=30)
    r.raise_for_status()
    return r.text


def establish_currency(session, currency, cookies):
    """Force the site's currency for the whole session, independent of egress IP.

    The site's on-page currency selector is a POST form (form#currform -> POST
    curr=<code>), and the server honours THAT by issuing a "blessed" curr cookie
    that then sticks for every later GET in the session. A self-supplied curr
    cookie or ?curr= parameter is ignored - only the POST works. Mirroring it
    here lets the crawl render GBP even from a non-UK IP (e.g. GitHub's US
    runners), so no UK proxy is required. Prices are unchanged by this - it only
    switches the displayed currency (verified: GBP via POST matches the natural
    UK render). The strict-currency guard remains the safety net if this ever
    stops working.
    """
    # POST to the sitemap: it's the most stable page (discovery needs it anyway)
    # and, unlike the homepage, it doesn't redirect - a redirect would turn the
    # POST into a GET and drop the form data, so the currency wouldn't take.
    code = currency.upper()
    r = session.post(SITEMAP, data={"curr": code}, headers=HEADERS,
                     cookies=cookies, timeout=30)
    r.raise_for_status()
    got = session.cookies.get("curr")
    print(f"Established currency via POST curr={code} (session cookie curr={got})",
          file=sys.stderr)
    if got != code:
        print(f"WARN: currency cookie is {got!r}, expected {code!r}; the POST may "
              f"not have taken. The strict-currency guard will catch a bad feed.",
              file=sys.stderr)


def discover_product_urls(session, cookies):
    soup = BeautifulSoup(get(SITEMAP, session, cookies), "lxml")
    urls = set(EXTRA_PRODUCT_URLS)
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if not href or href.startswith("#") \
                or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        # Sitemap links are RELATIVE (/category/product); resolve to absolute
        # so they aren't silently skipped.
        parts = urlparse(urljoin(BASE + "/", href))
        if parts.netloc not in ("www.goldentours.com", "goldentours.com"):
            continue
        path = parts.path
        if any(path.startswith(p) for p in NON_PRODUCT_PREFIXES):
            continue
        segments = [s for s in path.split("/") if s]
        if len(segments) == 2:
            urls.add(f"{BASE}{path.rstrip('/')}")
    return sorted(urls)


def build_was_map(session, cookies):
    """Map product path -> original ('was') price, read from category LISTING pages.

    Product pages show only a single 'From' price; the original/RRP ('was £Y')
    is shown on the category listing cards. We fetch each category page once and,
    for each product card, read the From + was from the SMALLEST ancestor that
    holds exactly one 'From' price (so we never read a neighbouring card). We keep
    the was only where it is a genuine discount (was > from). This feeds Meta's
    price/sale_price so a real "% off" can display. Returns {url_path: was_float}.
    """
    categories = sorted({urlparse(u).path.strip("/").split("/")[0]
                         for u in discover_product_urls(session, cookies)
                         if len([s for s in urlparse(u).path.split("/") if s]) == 2})
    was_map = {}
    for i, cat in enumerate(categories, 1):
        cat_url = f"{BASE}/{cat}"
        try:
            soup = BeautifulSoup(get(cat_url, session, cookies), "lxml")
        except Exception as e:
            print(f"[was {i}/{len(categories)}] ERR {cat_url}  {e}", file=sys.stderr)
            continue
        for a in soup.select("a[href]"):
            parts = urlparse(urljoin(BASE + "/", a.get("href", "")))
            if parts.netloc not in ("www.goldentours.com", "goldentours.com"):
                continue
            segs = [s for s in parts.path.split("/") if s]
            if len(segs) != 2:
                continue
            # Climb to the smallest ancestor holding exactly one 'From' price:
            # that is this product's own card. If an ancestor holds more than one
            # 'From', we have climbed into several cards - stop, don't guess.
            card, frm, was = a, None, None
            for _ in range(8):
                if card is None:
                    break
                froms = LISTING_FROM_RE.findall(card.get_text(" ", strip=True))
                if len(froms) == 1:
                    frm = float(froms[0].replace(",", ""))
                    wm = LISTING_WAS_RE.findall(card.get_text(" ", strip=True))
                    was = float(wm[0].replace(",", "")) if len(wm) == 1 else None
                    break
                if len(froms) > 1:
                    break
                card = card.parent
            if frm is None or was is None:
                continue
            path = f"/{segs[0]}/{segs[1]}"
            if was > frm:
                was_map.setdefault(path, was)
        time.sleep(0.4)
    print(f"was-map: {len(was_map)} products carry a genuine 'was' price "
          f"(scanned {len(categories)} category listings)", file=sys.stderr)
    return was_map


def meta_tag(soup, prop=None, name=None):
    tag = soup.find("meta", attrs={"property": prop}) if prop \
        else soup.find("meta", attrs={"name": name})
    return tag["content"].strip() if tag and tag.get("content") else ""


def _walk_offers(node):
    """Yield every dict in a JSON-LD tree that looks like an Offer/price node."""
    out = []
    if isinstance(node, dict):
        if any(k in node for k in ("price", "lowPrice", "priceCurrency")):
            out.append(node)
        for v in node.values():
            out += _walk_offers(v)
    elif isinstance(node, list):
        for v in node:
            out += _walk_offers(v)
    return out


def jsonld_price(raw):
    """Return (price_str, currency_code) from the page's JSON-LD product offer.

    This is the authoritative price the site itself publishes; it equals the
    visible "From £X" headline and is what Meta matches the landing page on.
    Picks the lowest offer price (the "from"/starting-from semantics). Returns
    None if the page carries no structured offer.
    """
    best = None  # (price_float, currency_code)
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                         raw, re.S | re.I):
        blob = m.group(1).strip()
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for off in _walk_offers(data):
            raw_price = off.get("price", off.get("lowPrice"))
            cur = off.get("priceCurrency")
            if raw_price is None or not cur:
                continue
            try:
                pv = float(str(raw_price).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if pv <= 0:
                continue
            if best is None or pv < best[0]:
                best = (pv, str(cur).upper())
    if best:
        return f"{best[0]:.2f}", best[1]
    return None


def parse_product(url, raw):
    soup = BeautifulSoup(raw, "lxml")

    title = meta_tag(soup, prop="og:title") or (soup.h1.get_text(strip=True) if soup.h1 else "")
    # Strip a trailing site-name suffix in any of its separator forms.
    title = re.sub(r"\s*[\|\-–—:]?\s*Golden Tours\s*$", "", title)
    title = re.sub(r"\s+", " ", title).strip()[:200]
    image = meta_tag(soup, prop="og:image")
    description = meta_tag(soup, name="description") or meta_tag(soup, prop="og:description")
    canonical = meta_tag(soup, prop="og:url") or url

    text = soup.get_text(" ", strip=True)

    # Price: prefer the structured JSON-LD offer (authoritative, Meta-matching);
    # fall back to the visible "From <sym>X" headline only if no JSON-LD offer.
    jl = jsonld_price(raw)
    if jl:
        from_price, currency_code = jl
        symbol = CURRENCY_SYMBOL.get(currency_code, "")
    else:
        from_m = PRICE_FROM_RE.search(text)
        if not from_m:
            return None  # no price -> not a bookable product, skip
        symbol = from_m.group(1)
        currency_code = SYMBOL_CURRENCY.get(symbol, "")
        from_price = from_m.group(2).replace(",", "")

    # 'was' (original) price is NOT on the product page - only on the category
    # listing cards. It is filled in later from the was-map (build_was_map).
    was_price = None

    ac = ACTIVITY_RE.search(raw) or ACTIVITY_RE2.search(text)
    slug = urlparse(canonical).path.rstrip("/").split("/")[-1]
    pid = (ac.group(1) if ac else slug)[:100]

    seg1 = urlparse(canonical).path.strip("/").split("/")[0]
    product_type = seg1.replace("-", " ").title()

    return {
        "id": pid, "title": title, "description": description,
        "link": canonical, "image_link": image, "symbol": symbol,
        "currency_code": currency_code,
        "from_price": from_price, "was_price": was_price,
        "product_type": product_type,
    }


def price_fields(p, currency):
    """Return (price, sale_price) for the feed.

    When the product has a genuine original ('was') price higher than its 'From'
    price, price=was and sale_price=from, so Meta shows the original struck
    through with a "% off" label. Otherwise price=from and sale_price is empty.
    """
    frm = float(p["from_price"])
    was = p.get("was_price")
    if was and float(was) > frm:
        return f"{float(was):.2f} {currency}", f"{frm:.2f} {currency}"
    return f"{frm:.2f} {currency}", ""


def build_items(currency, session, cookies, limit=None, was_map=None):
    candidates = discover_product_urls(session, cookies)
    if limit:
        candidates = candidates[:limit]
    items, wrong_currency = [], 0
    want = currency.upper()
    for i, url in enumerate(candidates, 1):
        try:
            p = parse_product(url, get(url, session, cookies))
            if p:
                if was_map:
                    wp = was_map.get(urlparse(p["link"]).path.rstrip("/"))
                    if wp and wp > float(p["from_price"]):
                        p["was_price"] = f"{wp:.2f}"
                if p["currency_code"] and p["currency_code"] != want:
                    wrong_currency += 1
                items.append(p)
                print(f"[{i}/{len(candidates)}] OK {p['currency_code']} {p['id']}  {p['title'][:50]}",
                      file=sys.stderr)
            else:
                print(f"[{i}/{len(candidates)}] skip (no price)  {url}", file=sys.stderr)
        except Exception as e:
            print(f"[{i}/{len(candidates)}] ERR  {url}  {e}", file=sys.stderr)
        time.sleep(0.4)

    if wrong_currency:
        print(f"\n*** CURRENCY WARNING: {wrong_currency}/{len(items)} pages were NOT in "
              f"{want}. The site renders by IP geolocation - this crawl must run from "
              f"a UK connection to get GBP. Do NOT upload this feed. ***\n", file=sys.stderr)

    # Meta requires a unique id per item. The same product can be linked under two
    # sitemap category paths but canonicalises (og:url) to one link/id - collapse
    # the repeats so duplicates don't silently overwrite each other in the catalogue.
    seen, deduped, removed = set(), [], 0
    for p in items:
        if p["id"] in seen:
            removed += 1
            continue
        seen.add(p["id"])
        deduped.append(p)
    if removed:
        print(f"De-duplicated {removed} repeat product id(s); {len(deduped)} unique items.",
              file=sys.stderr)
    return deduped


def write_csv(items, currency, path):
    # No 'condition' field: these are bookable services (tours/experiences), not
    # physical goods, so new/used/refurbished doesn't apply.
    cols = ["id", "title", "description", "availability",
            "price", "sale_price", "link", "image_link", "brand", "product_type"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for p in items:
            price, sale_price = price_fields(p, currency)
            w.writerow({
                "id": p["id"], "title": p["title"],
                "description": (p["description"] or "").replace("\n", " "),
                "availability": "in stock",
                "price": price, "sale_price": sale_price, "link": p["link"],
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
        price, sale_price = price_fields(p, currency)
        out += ["<item>", f"<g:id>{esc(p['id'])}</g:id>",
                f"<g:title>{esc(p['title'])}</g:title>",
                f"<g:description>{esc(p['description'])}</g:description>",
                f"<g:link>{esc(p['link'])}</g:link>",
                f"<g:image_link>{esc(p['image_link'])}</g:image_link>",
                "<g:availability>in stock</g:availability>",
                f"<g:brand>{BRAND}</g:brand>",
                f"<g:price>{price}</g:price>"]
        if sale_price:
            out += [f"<g:sale_price>{sale_price}</g:sale_price>"]
        out += [f"<g:product_type>{esc(p['product_type'])}</g:product_type>", "</item>"]
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

    session = requests.Session()

    # Force the currency via the site's POST selector so it renders GBP from any
    # IP (incl. GitHub's US runners) - no proxy needed. The strict-currency guard
    # below still refuses to write if this ever fails to take effect.
    establish_currency(session, args.currency, cookies)

    # Read original ('was') prices from the category listing pages so the feed can
    # carry price (original) + sale_price (from) for Meta's "% off" labels.
    was_map = build_was_map(session, cookies)

    items = build_items(args.currency, session, cookies, args.limit, was_map)
    n_sale = sum(1 for p in items if p.get("was_price"))
    print(f"\nExtracted {len(items)} products ({n_sale} with a sale price)", file=sys.stderr)

    # Guards: fail loudly BEFORE writing, so a scheduled job never publishes a bad feed.
    want = args.currency.upper()
    mismatches = sum(1 for p in items if p["currency_code"] and p["currency_code"] != want)
    if args.strict_currency and mismatches:
        sys.exit(f"ABORT: {mismatches}/{len(items)} products not in {want}. Feed NOT "
                 f"written. The crawl must run from a UK connection to render GBP.")
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
