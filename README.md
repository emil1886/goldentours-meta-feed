# goldentours-meta-feed

Builds a Meta (Facebook/Instagram) Commerce catalogue feed for Golden Tours and
publishes it via GitHub Pages so Meta can fetch it on a schedule.

- **Feed (CSV):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.csv`
- **Feed (XML):** `https://emil1886.github.io/goldentours-meta-feed/goldentours_meta_feed.xml`

The crawler discovers products from the site's HTML sitemap, reads each product
page's JSON-LD offer (the authoritative price Meta matches against), and writes a
Meta-compliant feed into `docs/`, which Pages deploys. No `condition` field is
emitted (these are services, not physical goods).

## Content IDs (pixel matching)

The catalogue `id` must equal what the Meta Pixel sends as `content_id`, otherwise
pixel events (ViewContent / Purchase) don't match the catalogue. The site books
through **Ventrata**, and its Pixel fires the **Ventrata product UUID**, so the
feed uses that UUID (scraped from each page's `productID`) as the `id`, keeping the
old activity code in `custom_label_0`. Where no `productID` exists (~3%) it falls
back to the activity code/slug.

## Category (`product_type`)

The `product_type` field carries the activity's site category, taken from the
product page breadcrumb (e.g. `/day-trips-from-london/...` →
`Tours & Day Trips from London`). Multi-level breadcrumbs are joined with ` > `.
This is Meta's merchant-category field, usable for ad-set targeting and product
sets. Falls back to the prettified URL slug where no breadcrumb exists.

## Images (square)

The site's product images are small landscape (~728×485), which look poor in the
feed. The pipeline centre-crops each to a **square** and upscales to **1080×1080**,
serves them from `docs/images/` on Pages, and points `image_link` there. These are
regenerated and deployed with the Pages artifact on every run (not committed to
git, to keep the repo lean). A sharper result would require higher-resolution
source images from Golden Tours.

## Pricing / sale labels

Product pages show a single "From £X". The category **listing cards** additionally
show an original "was £Y" where a product is discounted. `build_was_map()` reads
those, and where `was > From` the feed sets `price` = was (original) and
`sale_price` = From (current) so Meta can display the original struck through with
a "% off" label. Products without a genuine discount carry `price` = From and no
`sale_price`.

## How it runs

A GitHub Action (`.github/workflows/goldentours-feed.yml`) runs **daily**,
rebuilds the feed, commits it to `main` for history, and deploys `docs/` to
GitHub Pages. **No proxy, no server, no manual step** — it's fully automated.

## Currency (the interesting bit)

The site shows prices in the visitor's IP-geolocated currency (UK IP -> GBP, US
IP -> USD), and a self-supplied `curr` cookie or `?curr=` parameter is *ignored*.
But the on-page currency selector is a **POST form** (`form#currform` -> `POST
curr=<code>`), and the server honours that by issuing a "blessed" currency cookie
that then sticks for the whole session **regardless of IP**. `establish_currency()`
mirrors this, so the crawl renders GBP even from GitHub's US-based runners.

## Guards (a bad run never publishes)

The build aborts **before writing/deploying** if:

- any product is not in GBP (`--strict-currency`) — i.e. the POST override didn't take, or
- fewer than `--min-products` products were found (`--min-products 300`) — i.e. the
  site or sitemap changed.

On abort the workflow fails red and deploys nothing, so Meta keeps reading the
last good feed.

## Run it locally

```bash
pip install requests beautifulsoup4 lxml
python goldentours_meta_feed_gbp.py --currency GBP --strict-currency \
  --min-products 300 --out-dir docs
```

Works from any connection — `establish_currency()` forces GBP via the POST
selector.

## Meta setup

Commerce Manager → Catalogue → Data Sources → Add Items → **Data Feed** →
**Use a URL** → paste the CSV URL above → schedule **Daily**.
